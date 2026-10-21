"""Sandbox OAuth credential lifecycle. No business or payment execution authority."""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from backend.bank_sdk.contracts import BankError, UncertainBankWrite
from backend.security.local_files import protect_secret, write_secret_atomic

SANDBOX = 'https://sandbox-b2b.revolut.com'
TOKEN_PATH = '/api/1.0/auth/token'
_locks: dict[str,threading.Lock] = {}
_locks_guard = threading.Lock()


def client_assertion(client_id, issuer, key_path, certificate_path, now=None):
    try:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', client_id) or issuer != 'trusta.localhost':
            raise ValueError()
        protect_secret(Path(key_path).parent)
        protect_secret(key_path)
        key = serialization.load_pem_private_key(Path(key_path).read_bytes(), None)
        cert = x509.load_pem_x509_certificate(Path(certificate_path).read_bytes())
        public=cert.public_key()
        current = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
        if (not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048 or not isinstance(public,rsa.RSAPublicKey)
                or key.public_key().public_numbers() != public.public_numbers()
                or not cert.not_valid_before_utc <= current < cert.not_valid_after_utc):
            raise ValueError()
        def encode(value):
            return base64.urlsafe_b64encode(value).rstrip(b'=').decode()
        body = '.'.join(encode(json.dumps(v,separators=(',',':')).encode()) for v in (
            {'alg':'RS256','typ':'JWT'}, {'iss':issuer,'sub':client_id,'aud':'https://revolut.com',
                                        'exp':int(current.timestamp())+300}))
        signature = key.sign(body.encode(),padding.PKCS1v15(),hashes.SHA256())
        return body+'.'+encode(signature)
    except Exception:
        raise BankError('BANK_SIGNING_CREDENTIAL_INVALID') from None


@contextmanager
def credential_lock(path):
    """Serialize refresh across instances and processes sharing this credential file."""
    path = Path(path).resolve()
    with _locks_guard:
        lock = _locks.setdefault(str(path),threading.Lock())
    if not lock.acquire(timeout=10):
        raise BankError('BANK_CREDENTIAL_BUSY')
    stream = None
    acquired = False
    try:
        stream = path.with_suffix('.lock').open('a+b')
        stream.seek(0,2)
        if stream.tell()==0:
            stream.write(b'0');stream.flush()
        deadline=time.monotonic()+10
        while not acquired:
            try:
                stream.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                acquired=True
            except OSError:
                if time.monotonic()>=deadline: raise BankError('BANK_CREDENTIAL_BUSY') from None
                time.sleep(.05)
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        if stream: stream.close()
        lock.release()


class RevolutAuth:
    def __init__(self, transport, client_id, key_path, certificate_path, token_path,
                 issuer='trusta.localhost', audit=None, clock=time.time):
        if transport.base_url != SANDBOX:
            raise BankError('BANK_ENVIRONMENT_MISMATCH')
        if not isinstance(client_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',client_id) or issuer!='trusta.localhost':
            raise BankError('BANK_CREDENTIAL_INVALID')
        self.transport,self.client_id,self.key_path,self.certificate_path = transport,client_id,key_path,certificate_path
        self.token_path,self.issuer,self.audit,self.clock = Path(token_path),issuer,audit,clock
        if self.token_path.resolve().parent != Path(key_path).resolve().parent:
            raise BankError('BANK_CREDENTIAL_STORAGE_MISMATCH')
        self.revoked=False
        self.refresh_count=0

    def revoke(self):
        self.revoked=True

    def _load(self):
        try:
            protect_secret(self.token_path.parent)
            protect_secret(self.token_path)
            row=json.loads(self.token_path.read_text(encoding='utf-8'))
            if (row.get('client_id')!=self.client_id or row.get('issuer')!=self.issuer
                    or row.get('sandbox_only') is not True or row.get('token_type','').lower()!='bearer'):
                raise ValueError()
            self._validate_scope(row)
            for name in ('access_token','refresh_token'):
                value=row.get(name)
                if not isinstance(value,str) or not value or len(value)>8192 or any(c.isspace() for c in value): raise ValueError()
            expires=row.get('expires_in')
            issued=datetime.fromisoformat(row['obtained_at'].replace('Z','+00:00'))
            if type(expires) is not int or not 0<expires<=86400 or issued.tzinfo is None: raise ValueError()
            return row,issued.timestamp()+expires
        except BankError:
            raise
        except Exception:
            raise BankError('BANK_CREDENTIAL_INVALID') from None

    @staticmethod
    def _validate_scope(row):
        if row.get('operator_simulation_only') is True or row.get('scope_requested')=='READ,WRITE':
            raise BankError('BANK_SCOPE_DENIED')
        if 'scope' in row and (not isinstance(row['scope'],str) or
                              set(row['scope'].replace(',',' ').split())!={'READ'}):
            raise BankError('BANK_SCOPE_DENIED')

    def access_token(self, failed_token=None):
        if self.revoked: raise BankError('BANK_CREDENTIAL_EXPIRED')
        with credential_lock(self.token_path):
            if self.revoked: raise BankError('BANK_CREDENTIAL_EXPIRED')
            row,expiry=self._load()
            if self.clock()+30 < expiry and (failed_token is None or row['access_token']!=failed_token):
                return row['access_token']
            assertion=client_assertion(self.client_id,self.issuer,self.key_path,self.certificate_path, self.clock())
            body=urlencode({'grant_type':'refresh_token','refresh_token':row['refresh_token'],
                'client_assertion_type':'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
                'client_assertion':assertion}).encode()
            try:
                updated=self.transport.send('POST',TOKEN_PATH,body,{
                    'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'})
            except UncertainBankWrite:
                raise BankError('BANK_CREDENTIAL_REFRESH_UNAVAILABLE') from None
            if not isinstance(updated,dict): raise BankError('BANK_CREDENTIAL_INVALID')
            self._validate_scope(updated)
            token=updated.get('access_token');expiry_seconds=updated.get('expires_in')
            if (not isinstance(token,str) or not token or len(token)>8192 or any(c.isspace() for c in token)
                    or not isinstance(updated.get('token_type'),str)
                    or updated['token_type'].lower()!='bearer' or type(expiry_seconds) is not int
                    or not 0<expiry_seconds<=86400): raise BankError('BANK_CREDENTIAL_INVALID')
            refresh=updated.get('refresh_token',row['refresh_token'])
            if not isinstance(refresh,str) or not refresh or len(refresh)>8192 or any(c.isspace() for c in refresh):
                raise BankError('BANK_CREDENTIAL_INVALID')
            saved={'access_token':token,'refresh_token':refresh,'token_type':'bearer','expires_in':expiry_seconds,
                   'obtained_at':datetime.fromtimestamp(self.clock(),timezone.utc).isoformat(),
                   'client_id':self.client_id,'issuer':self.issuer,'sandbox_only':True}
            if 'scope' in updated: saved['scope']=updated['scope']
            elif 'scope' in row: saved['scope']=row['scope']
            if 'scope_requested' in row: saved['scope_requested']=row['scope_requested']
            try:
                write_secret_atomic(self.token_path,json.dumps(saved).encode())
            except OSError:
                raise BankError('BANK_CREDENTIAL_STORAGE_FAILED') from None
            self.refresh_count+=1
            if self.audit:
                self.audit.append(None,None,'BANK_CREDENTIAL_REFRESH','bank_auth',
                    {'provider':'revolut','environment':'sandbox','scope_reported':updated.get('scope'),
                     'expires_in':expiry_seconds})
            return token

    def headers(self, method, path, body=b'', failed_token=None):
        if method!='GET' or body or not (path=='/api/1.0/accounts' or path.startswith('/api/1.0/transactions?')):
            raise BankError('BANK_READ_ONLY')
        return {'Authorization':'Bearer '+self.access_token(failed_token),'Accept':'application/json',
                'User-Agent':'TRUSTA/RevolutReadOnly'}


class RevolutTransferAuth(RevolutAuth):
    """Separate transfer profile. READ credentials may only discover recipients.

    PAY headers additionally require the explicit user-consent bootstrap marker.
    The marker describes requested permissions; bank 403 remains authoritative.
    """
    @staticmethod
    def _validate_scope(row):
        if row.get('operator_simulation_only') is True or row.get('scope_requested')=='READ,WRITE':
            raise BankError('BANK_SCOPE_DENIED')
        if 'scope' in row:
            scope=row['scope']
            values=set(scope.replace(',',' ').split()) if isinstance(scope,str) else set()
            if 'READ' not in values or values-{'READ','PAY'}: raise BankError('BANK_SCOPE_DENIED')

    def headers(self,method,path,body=b'',failed_token=None):
        simple=path=='/api/1.0/accounts' or path.startswith('/api/1.0/transactions?') or path.startswith('/api/1.0/counterparties?')
        resource=re.fullmatch(r'/api/1.0/(?:transaction|counterparty)/[0-9a-f-]{36}',path)
        if method=='GET' and not body and (simple or resource):
            return {'Authorization':'Bearer '+self.access_token(failed_token),'Accept':'application/json'}
        if method=='POST' and path=='/api/1.0/pay' and body:
            token=self.access_token()
            row,_=self._load()
            if row.get('scope_requested')!='READ,PAY' or 'scope' in row and 'PAY' not in row['scope'].replace(',',' ').split():
                raise BankError('BANK_PAYMENT_SCOPE_NOT_AUTHORIZED')
            return {'Authorization':'Bearer '+token,'Accept':'application/json','Content-Type':'application/json'}
        raise BankError('BANK_ENDPOINT_DENIED')
