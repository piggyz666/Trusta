"""Public-only ECDSA verification and an encrypted local key store, never bank authority."""
import base64
import binascii
import hashlib
import hmac
import os
from pathlib import Path
import re
import subprocess
import getpass
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from backend.domain import Rejected, canonical

ALGORITHM = "ECDSA-P256-SHA256"
PROFILE = "TRUSTA-JSON-v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")


def signed_bytes(domain, body):
    # The new signing profile contains strings, integers, bools, null, arrays and objects.
    # Existing financial/payload/event hashes retain their pre-Phase4 serialization.
    def validate(value):
        if type(value) in {int, bool} or value is None:
            return
        if isinstance(value, str):
            value.encode("utf-8", errors="strict")
            return
        if isinstance(value, list):
            for item in value:
                validate(item)
            return
        if isinstance(value, dict) and all(isinstance(k, str) for k in value):
            for key, item in value.items():
                validate(key)
                validate(item)
            return
        raise ValueError("unsupported signing type")
    try:
        validate(body)
        return domain.encode("ascii") + b"\x00" + canonical(body).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Rejected("INVALID_CANONICAL_OBJECT") from None


def public_pem(key):
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def fingerprint(pem):
    key = load_public(pem)
    return hashlib.sha256(key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()


def load_public(pem):
    try:
        if not isinstance(pem, str) or len(pem) > 1024:
            raise ValueError()
        key = serialization.load_pem_public_key(pem.encode("ascii"))
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError()
        return key
    except (ValueError, TypeError, UnicodeError):
        raise Rejected("INVALID_PUBLIC_KEY") from None


def sign_object(key, domain, body):
    return base64.b64encode(key.sign(signed_bytes(domain, body), ec.ECDSA(hashes.SHA256()))).decode("ascii")


def verify_object(pem, domain, body, signature):
    try:
        if not isinstance(signature, str) or not 12 <= len(signature) <= 96:
            raise ValueError()
        raw = base64.b64decode(signature, validate=True)
        if not 8 <= len(raw) <= 72 or base64.b64encode(raw).decode("ascii") != signature:
            raise ValueError()
        load_public(pem).verify(raw, signed_bytes(domain, body), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError, binascii.Error):
        raise Rejected("INVALID_SIGNATURE") from None


class LocalKeyStore:
    def __init__(self, directory, root):
        candidate=Path(directory)
        if candidate.is_symlink() or (os.name=='nt' and candidate.is_junction()):raise Rejected('SECRET_PATH_LINK_DENIED')
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        from backend.security.local_files import protect_secret
        protect_secret(self.directory)
        self.password = hmac.new(root, b"trusta-pkcs8-encryption-v1", hashlib.sha256).digest()
        self.cache = {}

    def path(self, key_id):
        if not isinstance(key_id, str) or not re.fullmatch(r"key-[0-9a-f-]{36}", key_id):
            raise Rejected("INVALID_KEY_ID")
        return self.directory / (key_id + ".pem")

    def generate(self, key_id):
        key = ec.generate_private_key(ec.SECP256R1())
        data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.BestAvailableEncryption(self.password))
        descriptor = os.open(self.path(key_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        self.cache[key_id] = key
        return public_pem(key.public_key())

    def load(self, record):
        key_id = record["key_id"]
        if key_id not in self.cache:
            try:
                from backend.security.local_files import protect_secret
                protect_secret(self.path(key_id))
                key = serialization.load_pem_private_key(self.path(key_id).read_bytes(), password=self.password)
                if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
                    raise ValueError()
                self.cache[key_id] = key
            except (OSError, ValueError, TypeError, Rejected):
                raise Rejected("PRIVATE_KEY_UNAVAILABLE") from None
        key = self.cache[key_id]
        if public_pem(key.public_key()) != record["public_key"]:
            raise Rejected("PRIVATE_KEY_MISMATCH")
        return key
