import base64
import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from backend.bank_sdk.contracts import BankError

class StarlingAuth:
    """PAT lifecycle; OAuth token acquisition remains a separate explicit consent flow."""
    def __init__(self, token_env='BANK_ACCESS_TOKEN', key_path='', key_id='', expires_at=None):
        self.token_env, self.key_path, self.key_id = token_env,key_path,key_id
        self.expires_at, self.revoked = expires_at,False

    def revoke(self):
        self.revoked = True

    def headers(self, method, path, body):
        token = os.getenv(self.token_env,'')
        if self.revoked or self.expires_at is not None and time.time() >= self.expires_at or not token or any(c in token for c in '\r\n'):
            raise BankError('BANK_CREDENTIAL_EXPIRED')
        headers = {'Authorization':'Bearer '+token,'Accept':'application/json','Content-Type':'application/json','User-Agent':'TRUSTA/Phase6'}
        if method not in {'GET','HEAD'}:
            if not self.key_id or any(c in self.key_id for c in '"\r\n') or not self.key_path:
                raise BankError('BANK_SIGNING_CREDENTIAL_REQUIRED')
            try:
                from backend.security.local_files import protect_secret
                protect_secret(self.key_path)
                key = serialization.load_pem_private_key(Path(self.key_path).read_bytes(),password=None)
                if not isinstance(key,rsa.RSAPrivateKey) or key.key_size < 2048:
                    raise ValueError()
                date = datetime.now(timezone.utc).isoformat()
                digest = base64.b64encode(hashlib.sha512(body).digest()).decode() if body else 'X'
                content = f'(request-target): {method.lower()} {path}\nDate: {date}\nDigest: {digest}'
                signature = base64.b64encode(key.sign(content.encode(),padding.PKCS1v15(),hashes.SHA512())).decode()
                headers.update(Date=date,Digest=digest)
                headers['Authorization'] += f';Signature keyid="{self.key_id}",algorithm="rsa-sha512",headers="(request-target) Date Digest",signature="{signature}"'
            except Exception:
                raise BankError('BANK_SIGNING_CREDENTIAL_INVALID') from None
        return headers
