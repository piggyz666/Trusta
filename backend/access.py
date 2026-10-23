"""Who may reach this local server, from where (OpenClaw-style local deployment).

Access modes (``TRUSTA_ACCESS_MODE``)
  local   default. Bound to 127.0.0.1; only this computer's browser.
  lan     Also reachable from phones on the same Wi-Fi / hotspot at http://<LAN-IP>:<port>. Phones never receive the
          master API token: they pair once with a short-lived one-time code (QR or typed) and get their own device
          token, which is revocable, owner-scoped and expires.
  tunnel  Behind a tunnel such as Cloudflare Tunnel (``TRUSTA_PUBLIC_HOST=xxx.trycloudflare.com``). The tunnel
          connects to 127.0.0.1, so requests are treated as remote by their Host header, never by source address.

Pairing codes are also used to bind a Feishu account to a customer ("绑定 123456" sent to the bot).
Codes: 6 digits, 5 minutes, single use, stored as HMAC, at most 5 wrong tries per code and 20 per 10 minutes overall.
"""
import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
import threading
import time
from backend.domain import Rejected, uid

CODE_TTL, CODE_ATTEMPTS, GLOBAL_FAILURES, GLOBAL_WINDOW = 300, 5, 20, 600
DEVICE_TTL = 30 * 86400
PURPOSES = {"device", "feishu"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS pairing_codes(code_mac TEXT PRIMARY KEY, owner TEXT NOT NULL, purpose TEXT NOT NULL,
 created_at REAL NOT NULL, expires_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS paired_devices(id TEXT PRIMARY KEY, owner TEXT NOT NULL, token_mac TEXT NOT NULL UNIQUE,
 name TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, last_seen REAL);
CREATE TABLE IF NOT EXISTS channel_bindings(channel TEXT NOT NULL, external_id TEXT NOT NULL, owner TEXT NOT NULL,
 created_at REAL NOT NULL, PRIMARY KEY(channel, external_id));
"""


def lan_ip():
    """The address other devices on this network use to reach us (no packet is sent: UDP connect only picks a route)."""
    configured = os.environ.get("TRUSTA_LAN_HOST", "").strip()
    if configured:
        return configured
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


class AccessControl:
    def __init__(self, service, mode=None, public_host=None):
        self.s = service
        self.mode = (mode or os.environ.get("TRUSTA_ACCESS_MODE", "local")).strip().lower()
        if self.mode not in {"local", "lan", "tunnel"}:
            raise Rejected("INVALID_ACCESS_MODE")
        self.public_host = (public_host if public_host is not None else os.environ.get("TRUSTA_PUBLIC_HOST", "")).strip().lower()
        self.warning = None
        if self.mode == "tunnel" and not self.public_host:
            # Fail safe, not closed: without a tunnel host nothing remote can be allowed, so behave as local-only.
            self.mode, self.warning = "local", "TRUSTA_ACCESS_MODE=tunnel 但没有设置 TRUSTA_PUBLIC_HOST，已按“仅本机”运行"
        self.key = hmac.new(service.config.signing_key, b"trusta-access-v1", hashlib.sha256).digest()
        self._failures, self._lock = [], threading.Lock()
        with service.db.transaction() as conn:
            conn.executescript(SCHEMA)

    # ---- network policy -------------------------------------------------------------------------
    def bind_host(self):
        return "0.0.0.0" if self.mode == "lan" else "127.0.0.1"

    def local_hosts(self, port):
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def allowed_hosts(self, port):
        hosts = set(self.local_hosts(port))
        if self.mode == "lan":
            hosts.add(f"{lan_ip()}:{port}")
        if self.mode == "tunnel":
            hosts.add(self.public_host)
        return hosts

    def allowed_origins(self, port):
        origins = {f"http://{h}" for h in self.allowed_hosts(port) if h != self.public_host}
        if self.public_host:
            origins.add(f"https://{self.public_host}")
        return origins

    def is_local(self, host_header, client_ip, port):
        """This computer itself: loopback source AND a loopback Host (a tunnel also arrives on loopback)."""
        try:
            loopback = ipaddress.ip_address(client_ip).is_loopback
        except ValueError:
            loopback = False
        return loopback and host_header in self.local_hosts(port)

    def urls(self, port):
        result = {"local": f"http://127.0.0.1:{port}/"}
        if self.mode == "lan":
            result["lan"] = f"http://{lan_ip()}:{port}/"
        if self.mode == "tunnel":
            result["public"] = f"https://{self.public_host}/"
        return result

    # ---- pairing codes ---------------------------------------------------------------------------
    def _mac(self, value):
        return hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()

    def start_pairing(self, owner, purpose, port=8000):
        if purpose not in PURPOSES:
            raise Rejected("INVALID_INPUT")
        if purpose == "device" and self.mode == "local":
            raise Rejected("PAIRING_NEEDS_LAN_OR_TUNNEL")
        now = time.time()
        code = f"{secrets.randbelow(10 ** 6):06d}"
        with self.s.db.transaction() as conn:
            conn.execute("DELETE FROM pairing_codes WHERE expires_at<? OR used=1", (now,))
            conn.execute("DELETE FROM pairing_codes WHERE owner=? AND purpose=?", (owner, purpose))  # one live code at a time
            conn.execute("INSERT INTO pairing_codes(code_mac,owner,purpose,created_at,expires_at) VALUES(?,?,?,?,?)",
                         (self._mac(purpose + ":" + code), owner, purpose, now, now + CODE_TTL))
            self.s.audit.append_in(conn, None, None, "PAIRING_STARTED", "access", {"owner": owner, "purpose": purpose})
        result = {"code": code, "purpose": purpose, "expires_at": now + CODE_TTL}
        if purpose == "device":
            base = self.urls(port).get("lan") or self.urls(port).get("public")
            result["pair_url"] = f"{base}#pair={code}"  # what the QR code encodes; the code alone also works when typed
        else:
            result["instruction"] = f"在飞书里给 TRUSTA 机器人发送：绑定 {code}"
        return result

    def _throttled(self):
        now = time.time()
        with self._lock:
            self._failures = [t for t in self._failures if now - t < GLOBAL_WINDOW]
            return len(self._failures) >= GLOBAL_FAILURES

    def _fail(self):
        with self._lock:
            self._failures.append(time.time())

    def claim(self, code, purpose):
        """Consume a code; returns the owner. Wrong guesses count per code and globally."""
        if purpose not in PURPOSES or not isinstance(code, str) or len(code) != 6 or not code.isdigit():
            self._fail()
            raise Rejected("PAIRING_INVALID")
        if self._throttled():
            raise Rejected("PAIRING_RATE_LIMITED")
        now = time.time()
        with self.s.db.transaction() as conn:
            row = conn.execute("SELECT * FROM pairing_codes WHERE code_mac=?", (self._mac(purpose + ":" + code),)).fetchone()
            valid = row and not row["used"] and row["expires_at"] >= now and row["attempts"] < CODE_ATTEMPTS
            if valid:
                conn.execute("UPDATE pairing_codes SET used=1 WHERE code_mac=?", (row["code_mac"],))
            else:
                # Count a miss against every live code of this purpose, so brute force burns all of them quickly.
                conn.execute("UPDATE pairing_codes SET attempts=attempts+1 WHERE purpose=? AND used=0", (purpose,))
        if not valid:  # raised outside the transaction so the attempt counter is committed
            self._fail()
            raise Rejected("PAIRING_INVALID")
        return row["owner"]

    # ---- devices -----------------------------------------------------------------------------------
    def pair_device(self, code, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
            name = "手机"
        owner = self.claim(code, "device")
        token, device_id, now = secrets.token_urlsafe(32), "dev-" + uid().replace("-", "")[:12], time.time()
        with self.s.db.transaction() as conn:
            conn.execute("INSERT INTO paired_devices(id,owner,token_mac,name,created_at,expires_at) VALUES(?,?,?,?,?,?)",
                         (device_id, owner, self._mac("device:" + token), name.strip()[:40], now, now + DEVICE_TTL))
            self.s.audit.append_in(conn, None, None, "DEVICE_PAIRED", "access", {"owner": owner, "device_id": device_id})
        return {"device_id": device_id, "device_token": token, "expires_at": now + DEVICE_TTL}

    def device_owner(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            return None
        row = self.s.db.one("SELECT id, owner, expires_at, revoked FROM paired_devices WHERE token_mac=?", (self._mac("device:" + token),))
        if not row or row["revoked"] or row["expires_at"] < time.time():
            return None
        with self.s.db.transaction() as conn:
            conn.execute("UPDATE paired_devices SET last_seen=? WHERE id=?", (time.time(), row["id"]))
        return row["owner"]

    def devices(self, owner):
        return [dict(r) for r in self.s.db.rows(
            "SELECT id, name, created_at, expires_at, revoked, last_seen FROM paired_devices WHERE owner=? ORDER BY created_at DESC", (owner,))]

    def revoke(self, owner, device_id):
        with self.s.db.transaction() as conn:
            changed = conn.execute("UPDATE paired_devices SET revoked=1 WHERE id=? AND owner=? AND revoked=0", (device_id, owner)).rowcount
            if not changed:
                raise Rejected("DEVICE_NOT_FOUND")
            self.s.audit.append_in(conn, None, None, "DEVICE_REVOKED", "access", {"owner": owner, "device_id": device_id})
        return {"revoked": device_id}

    # ---- IM bindings -------------------------------------------------------------------------------
    def bind_channel(self, channel, external_id, code):
        owner = self.claim(code, channel)
        with self.s.db.transaction() as conn:
            conn.execute("INSERT INTO channel_bindings VALUES(?,?,?,?) ON CONFLICT(channel, external_id) DO UPDATE SET "
                         "owner=excluded.owner, created_at=excluded.created_at", (channel, external_id, owner, time.time()))
            self.s.audit.append_in(conn, None, None, "CHANNEL_BOUND", "access",
                                   {"channel": channel, "external_hash": self._mac(external_id)[:16], "owner": owner})
        return owner

    def channel_owner(self, channel, external_id):
        row = self.s.db.one("SELECT owner FROM channel_bindings WHERE channel=? AND external_id=?", (channel, external_id or ""))
        return row["owner"] if row else None

    def unbind_channel(self, channel, owner):
        with self.s.db.transaction() as conn:
            count = conn.execute("DELETE FROM channel_bindings WHERE channel=? AND owner=?", (channel, owner)).rowcount
            self.s.audit.append_in(conn, None, None, "CHANNEL_UNBOUND", "access", {"channel": channel, "owner": owner, "count": count})
        return {"unbound": count}
