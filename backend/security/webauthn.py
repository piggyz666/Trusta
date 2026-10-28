"""WebAuthn as a strong-verification factor (赛题：红色操作须通过人脸识别、短信验证码或 U 盾等多因子认证).

The customer registers a platform authenticator (phone / laptop face or fingerprint unlock, Windows Hello) or a roaming
security key (USB / NFC key, the modern U 盾). A RED action is then approved by signing a challenge that is derived from
that action's authorization id and action hash, so the signature is valid for exactly one action:

  challenge = HMAC(server key, "webauthn:" + authorization_id + ":" + action_hash)

Checked on every assertion: credential belongs to the customer; client data type, challenge and origin; RP ID hash;
user present + user verified flags (biometric / PIN, not just a tap); signature over authenticatorData ‖ SHA-256(clientData)
with the registered public key (ES256, RS256 or EdDSA); signature counter never goes backwards. Attestation statements are
not verified (attestation "none"): the factor proves possession of the registered key plus local user verification, which
is what a payment confirmation needs.

Browsers allow WebAuthn only in a secure context: https, or http://localhost. Opened through a LAN IP over plain http it
is unavailable and TOTP / SMS remain the factors.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import struct
import time
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

from backend.domain import Rejected

SCHEMA = """
CREATE TABLE IF NOT EXISTS webauthn_credentials(id TEXT PRIMARY KEY, owner TEXT NOT NULL, rp_id TEXT NOT NULL, alg INTEGER NOT NULL,
 public_key TEXT NOT NULL, sign_count INTEGER NOT NULL, label TEXT NOT NULL, created_at REAL NOT NULL, last_used_at REAL);
CREATE TABLE IF NOT EXISTS webauthn_registrations(owner TEXT PRIMARY KEY, challenge TEXT NOT NULL, rp_id TEXT NOT NULL,
 origin TEXT NOT NULL, expires_at REAL NOT NULL);
"""
ALGORITHMS = {-7: "ES256", -257: "RS256", -8: "EdDSA"}
REGISTRATION_TTL = 300
MAX_CREDENTIALS = 10
FLAG_UP, FLAG_UV, FLAG_AT = 0x01, 0x04, 0x40


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text):
    if not isinstance(text, str) or len(text) > 16384:
        raise Rejected("WEBAUTHN_INVALID")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError):
        raise Rejected("WEBAUTHN_INVALID") from None


# ---- minimal CBOR (RFC 8949) for attestationObject and COSE keys --------------------------------------------------
def cbor_decode(data):
    value, end = _cbor(data, 0, 0)
    return value, end


def _cbor(data, i, depth):
    if depth > 16 or i >= len(data):
        raise Rejected("WEBAUTHN_INVALID")
    major, info = data[i] >> 5, data[i] & 0x1F
    i += 1
    if info < 24:
        arg = info
    elif info in (24, 25, 26, 27):
        size = 1 << (info - 24)
        if i + size > len(data):
            raise Rejected("WEBAUTHN_INVALID")
        arg = int.from_bytes(data[i:i + size], "big")
        i += size
    else:
        raise Rejected("WEBAUTHN_INVALID")  # indefinite lengths are not used by authenticators
    if major == 0:
        return arg, i
    if major == 1:
        return -1 - arg, i
    if major in (2, 3):
        if i + arg > len(data):
            raise Rejected("WEBAUTHN_INVALID")
        chunk = data[i:i + arg]
        return (chunk if major == 2 else chunk.decode()), i + arg
    if major == 4:
        items = []
        for _ in range(arg):
            item, i = _cbor(data, i, depth + 1)
            items.append(item)
        return items, i
    if major == 5:
        out = {}
        for _ in range(arg):
            key, i = _cbor(data, i, depth + 1)
            out[key], i = _cbor(data, i, depth + 1)
        return out, i
    if major == 7 and info in (20, 21, 22):
        return {20: False, 21: True, 22: None}[info], i
    raise Rejected("WEBAUTHN_INVALID")


def cose_public_key(cose):
    """COSE_Key map -> (alg, cryptography public key)."""
    kty, alg = cose.get(1), cose.get(3)
    if alg == -7 and kty == 2 and cose.get(-1) == 1:
        x, y = cose.get(-2), cose.get(-3)
        if not (isinstance(x, bytes) and isinstance(y, bytes) and len(x) == len(y) == 32):
            raise Rejected("WEBAUTHN_UNSUPPORTED_KEY")
        return alg, ec.EllipticCurvePublicNumbers(int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()).public_key()
    if alg == -257 and kty == 3:
        n, e = cose.get(-1), cose.get(-2)
        if not (isinstance(n, bytes) and isinstance(e, bytes)) or len(n) < 256:
            raise Rejected("WEBAUTHN_UNSUPPORTED_KEY")
        return alg, rsa.RSAPublicNumbers(int.from_bytes(e, "big"), int.from_bytes(n, "big")).public_key()
    if alg == -8 and kty == 1 and cose.get(-1) == 6 and isinstance(cose.get(-2), bytes):
        return alg, ed25519.Ed25519PublicKey.from_public_bytes(cose[-2])
    raise Rejected("WEBAUTHN_UNSUPPORTED_KEY")


def verify_signature(alg, key, signature, message):
    try:
        if alg == -7:
            key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        elif alg == -257:
            key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
        else:
            key.verify(signature, message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def parse_auth_data(data):
    if len(data) < 37:
        raise Rejected("WEBAUTHN_INVALID")
    rp_hash, flags, count = data[:32], data[32], struct.unpack(">I", data[33:37])[0]
    credential = None
    if flags & FLAG_AT:
        if len(data) < 55:
            raise Rejected("WEBAUTHN_INVALID")
        length = struct.unpack(">H", data[53:55])[0]
        credential_id = data[55:55 + length]
        cose, _ = cbor_decode(data[55 + length:])
        credential = {"id": credential_id, "cose": cose}
    return {"rp_hash": rp_hash, "flags": flags, "count": count, "credential": credential}


def rp_from(origin):
    """RP ID for an origin: its host name. WebAuthn does not work for IP addresses or plain http other than localhost."""
    parsed = urlparse(origin or "")
    host = (parsed.hostname or "").lower()
    if not host:
        raise Rejected("WEBAUTHN_ORIGIN_REQUIRED")
    try:
        ipaddress.ip_address(host)
        raise Rejected("WEBAUTHN_NEEDS_HOSTNAME")  # 用 http://localhost:端口 或 https 域名打开
    except ValueError:
        pass
    if parsed.scheme != "https" and host != "localhost":
        raise Rejected("WEBAUTHN_NEEDS_HTTPS")
    return host


class WebAuthn:
    def __init__(self, db, audit, key):
        self.db, self.audit = db, audit
        self.key = hmac.new(key, b"trusta-webauthn-v1", hashlib.sha256).digest()
        with db.transaction() as conn:
            conn.executescript(SCHEMA)

    # ---- registration ------------------------------------------------------------------------------------------
    def credentials(self, owner):
        return [{"id": r["id"], "label": r["label"], "algorithm": ALGORITHMS.get(r["alg"], str(r["alg"])), "rp_id": r["rp_id"],
                 "created_at": r["created_at"], "last_used_at": r["last_used_at"]}
                for r in self.db.rows("SELECT * FROM webauthn_credentials WHERE owner=? ORDER BY created_at", (owner,))]

    def begin_registration(self, owner, origin):
        rp_id = rp_from(origin)
        if len(self.credentials(owner)) >= MAX_CREDENTIALS:
            raise Rejected("WEBAUTHN_TOO_MANY_CREDENTIALS")
        challenge = b64url(secrets.token_bytes(32))
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO webauthn_registrations VALUES(?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET challenge=excluded.challenge, "
                         "rp_id=excluded.rp_id, origin=excluded.origin, expires_at=excluded.expires_at",
                         (owner, challenge, rp_id, origin, time.time() + REGISTRATION_TTL))
        return {"challenge": challenge, "rp": {"id": rp_id, "name": "TRUSTA"},
                "user": {"id": b64url(hashlib.sha256(("trusta-user:" + owner).encode()).digest()), "name": owner, "displayName": owner},
                "pubKeyCredParams": [{"type": "public-key", "alg": alg} for alg in ALGORITHMS],
                "authenticatorSelection": {"userVerification": "required", "residentKey": "discouraged"},
                "excludeCredentials": [{"type": "public-key", "id": c["id"]} for c in self.credentials(owner)],
                "attestation": "none", "timeout": REGISTRATION_TTL * 1000}

    def finish_registration(self, owner, body, label="本机"):
        if not isinstance(body, dict) or set(body) - {"id", "clientDataJSON", "attestationObject", "label"}:
            raise Rejected("INVALID_INPUT")
        with self.db.transaction() as conn:
            pending = conn.execute("SELECT * FROM webauthn_registrations WHERE owner=?", (owner,)).fetchone()
            conn.execute("DELETE FROM webauthn_registrations WHERE owner=?", (owner,))  # single use
        if not pending or pending["expires_at"] < time.time():
            raise Rejected("WEBAUTHN_REGISTRATION_EXPIRED")
        client = self._client(body.get("clientDataJSON"), "webauthn.create", pending["challenge"], pending["rp_id"])
        if client["origin"] != pending["origin"]:
            raise Rejected("WEBAUTHN_ORIGIN_MISMATCH")
        attestation, _ = cbor_decode(unb64url(body.get("attestationObject")))
        if not isinstance(attestation, dict) or not isinstance(attestation.get("authData"), bytes):
            raise Rejected("WEBAUTHN_INVALID")
        auth = parse_auth_data(attestation["authData"])
        self._check_rp_and_flags(auth, pending["rp_id"])
        if not auth["credential"]:
            raise Rejected("WEBAUTHN_INVALID")
        credential_id = b64url(auth["credential"]["id"])
        if credential_id != body.get("id"):
            raise Rejected("WEBAUTHN_INVALID")
        alg, key = cose_public_key(auth["credential"]["cose"])
        from cryptography.hazmat.primitives import serialization
        public = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        label = str(body.get("label") or label)[:30]
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM webauthn_credentials WHERE id=?", (credential_id,)).fetchone():
                raise Rejected("WEBAUTHN_ALREADY_REGISTERED")
            conn.execute("INSERT INTO webauthn_credentials VALUES(?,?,?,?,?,?,?,?,NULL)",
                         (credential_id, owner, pending["rp_id"], alg, b64url(public), auth["count"], label, time.time()))
            self.audit.append_in(conn, None, None, "WEBAUTHN_REGISTERED", "security",
                                 {"owner": owner, "credential": hashlib.sha256(credential_id.encode()).hexdigest()[:16],
                                  "algorithm": ALGORITHMS[alg], "rp_id": pending["rp_id"]})
        return {"registered": True, "credentials": self.credentials(owner)}

    def remove(self, owner, credential_id):
        with self.db.transaction() as conn:
            gone = conn.execute("DELETE FROM webauthn_credentials WHERE id=? AND owner=?", (credential_id, owner)).rowcount
            if gone:
                self.audit.append_in(conn, None, None, "WEBAUTHN_REMOVED", "security", {"owner": owner})
        if not gone:
            raise Rejected("RESOURCE_NOT_FOUND_OR_DENIED")
        return {"credentials": self.credentials(owner)}

    # ---- assertion for one authorization --------------------------------------------------------------------------
    def challenge(self, authorization_id, action_hash):
        return b64url(hmac.new(self.key, f"webauthn:{authorization_id}:{action_hash}".encode(), hashlib.sha256).digest())

    def request_options(self, owner, auth):
        credentials = self.credentials(owner)
        if not credentials:
            raise Rejected("WEBAUTHN_NOT_REGISTERED")
        return {"challenge": self.challenge(auth["authorization_id"], auth["action_hash"]), "rpId": credentials[0]["rp_id"],
                "allowCredentials": [{"type": "public-key", "id": c["id"]} for c in credentials],
                "userVerification": "required", "timeout": 120000}

    def verify_assertion(self, conn, owner, auth, action_hash, payload):
        """Returns (credential reference, None) or (None, error code). `payload` is the JSON the browser produced."""
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return None, "WEBAUTHN_INVALID"
        if not isinstance(data, dict) or set(data) - {"id", "clientDataJSON", "authenticatorData", "signature", "userHandle"}:
            return None, "WEBAUTHN_INVALID"
        row = conn.execute("SELECT * FROM webauthn_credentials WHERE id=? AND owner=?", (data.get("id"), owner)).fetchone()
        if not row:
            return None, "WEBAUTHN_UNKNOWN_CREDENTIAL"
        try:
            client_raw = unb64url(data.get("clientDataJSON"))
            self._client(data.get("clientDataJSON"), "webauthn.get", self.challenge(auth["authorization_id"], action_hash), row["rp_id"])
            auth_raw = unb64url(data.get("authenticatorData"))
            parsed = parse_auth_data(auth_raw)
            self._check_rp_and_flags(parsed, row["rp_id"])
        except Rejected as exc:
            return None, exc.code
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_der_public_key(unb64url(row["public_key"]))
        if not verify_signature(row["alg"], key, unb64url(data.get("signature")), auth_raw + hashlib.sha256(client_raw).digest()):
            return None, "WEBAUTHN_SIGNATURE_INVALID"
        if (parsed["count"] or row["sign_count"]) and parsed["count"] <= row["sign_count"]:
            return None, "WEBAUTHN_REPLAY"  # a cloned authenticator or a replayed assertion
        conn.execute("UPDATE webauthn_credentials SET sign_count=?, last_used_at=? WHERE id=?", (parsed["count"], time.time(), row["id"]))
        return hashlib.sha256(row["id"].encode()).hexdigest()[:16], None

    # ---- shared checks --------------------------------------------------------------------------------------------
    @staticmethod
    def _client(encoded, kind, challenge, rp_id):
        try:
            client = json.loads(unb64url(encoded))
        except ValueError:
            raise Rejected("WEBAUTHN_INVALID") from None
        if not isinstance(client, dict) or client.get("type") != kind:
            raise Rejected("WEBAUTHN_INVALID")
        if not hmac.compare_digest(str(client.get("challenge", "")), challenge):
            raise Rejected("WEBAUTHN_CHALLENGE_MISMATCH")
        if rp_from(client.get("origin")) != rp_id:
            raise Rejected("WEBAUTHN_ORIGIN_MISMATCH")
        return client

    @staticmethod
    def _check_rp_and_flags(parsed, rp_id):
        if not hmac.compare_digest(parsed["rp_hash"], hashlib.sha256(rp_id.encode()).digest()):
            raise Rejected("WEBAUTHN_RP_MISMATCH")
        if not parsed["flags"] & FLAG_UP or not parsed["flags"] & FLAG_UV:
            raise Rejected("WEBAUTHN_USER_NOT_VERIFIED")  # needs face / fingerprint / PIN, not only a tap
