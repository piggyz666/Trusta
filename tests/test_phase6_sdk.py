import base64,json,os,tempfile,time,unittest,uuid,threading
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization,hashes
from cryptography.hazmat.primitives.asymmetric import rsa,padding
from backend.bank_sdk.contracts import BankError,BankRequest,BankIdempotency,ExternalBankSafeMode,UncertainBankWrite
from backend.bank_sdk.starling import ReferenceBankAdapter
from backend.bank_sdk.auth import StarlingAuth
from backend.bank_sdk.transport import BankTransport
from backend.bank_sdk.bridge import CanonicalBankAdapter
from backend.domain import digest,Rejected
from tests.phase6_helpers import service

ACCOUNT=str(uuid.UUID(int=1));CATEGORY=str(uuid.UUID(int=2));PAYEE=str(uuid.UUID(int=3));ORDER=str(uuid.UUID(int=4))
class AuthFixture:
    def headers(self,*args):return {}
class BankFixture:
    base_url='http://127.0.0.1:1234'
    def __init__(self): self.balance=1000;self.settled=True;self.pending=False;self.puts=0;self.payload=None;self.failed=False
    def send(self,method,path,body,headers):
        if method=='PUT':
            self.puts+=1;self.payload=json.loads(body)
            if self.failed: raise UncertainBankWrite()
            self.balance-=self.payload['amount']['minorUnits'];return {'paymentOrderUid':ORDER}
        if path=='/api/v2/accounts':return {'accounts':[{'accountUid':ACCOUNT,'defaultCategory':CATEGORY,'currency':'GBP'}]}
        if path.endswith('/balance'):return {'effectiveBalance':{'currency':'GBP','minorUnits':self.balance}}
        if path=='/api/v2/payees':return {'payees':[{'payeeUid':PAYEE,'payeeName':'张三','accounts':[{'defaultAccount':True,'countryCode':'GB','payeeAccountUid':PAYEE}]}]}
        common={'amount':self.payload['amount'],'payeeAccountUid':PAYEE,'reference':self.payload['reference']}
        if path.endswith('/payments'):return {'payments':[common|{'paymentStatusDetails':{'paymentStatus':'PENDING' if self.pending else 'ACCEPTED'},'completedAt':None if self.pending else datetime.now(timezone.utc).isoformat()}]}
        if path.startswith('/api/v2/feed/'):return {'feedItems':[{'feedItemUid':ORDER,'amount':self.payload['amount'],'reference':self.payload['reference'],'counterPartySubEntityUid':PAYEE,'direction':'OUT','transactionTime':datetime.now(timezone.utc).isoformat(),'status':'SETTLED' if self.settled else 'PENDING'}]}
        if path.endswith(ORDER):return common|{'paymentOrderUid':ORDER}
        raise AssertionError(path)

class SDKTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=service(self.tmp.name);self.net=BankFixture()
        self.provider=ReferenceBankAdapter(self.s.db,self.net,AuthFixture(),'demo',ExternalBankSafeMode(enabled=True,source_accounts=frozenset({ACCOUNT}),payees=frozenset({PAYEE})))
        self.s.gateway._adapter=CanonicalBankAdapter(self.provider)
    def task(self):return self.s.create_normalized_task('from reference bank','demo',ACCOUNT,{'task_type':'TRANSFER','needs_clarification':False,'recipient_name':'张三','amount':10,'currency':'GBP'})
    def test_canonical_reads_owner_and_currency(self):
        r=self.provider.request(BankRequest('accounts.read','demo',{'account_id':ACCOUNT}));self.assertEqual(r.data['currency'],'GBP')
        with self.assertRaises(BankError):self.provider.request(BankRequest('accounts.read','other'))
        self.net.payload={'amount':{'currency':'GBP','minorUnits':10},'reference':'synthetic'}
        feed=self.provider.request(BankRequest('transactions.read','demo',{'account_id':ACCOUNT,'month':'2026-09'})).data[0]
        self.assertEqual(feed['amount'],10);self.assertEqual(feed['currency'],'GBP');self.assertEqual(feed['kind'],'UNKNOWN');self.assertNotIn('feedItemUid',feed)
    def test_core_verified_reference_fixture_only_after_authorization(self):
        t=self.task();self.assertEqual(self.net.puts,0)
        done=self.s.authorize(t['task_id'],'demo',digest(t['proposed_actions'][0]),True)
        self.assertEqual(done['status'],'COMPLETED');self.assertEqual(self.net.puts,1);self.assertEqual(done['verification']['provider_status'],'SETTLED')
    def test_pending_not_receipt(self):
        self.net.pending=True;t=self.task();done=self.s.authorize(t['task_id'],'demo',digest(t['proposed_actions'][0]),True)
        self.assertEqual(done['status'],'MANUAL_REVIEW');self.assertIsNone(done['receipt'])
    def test_accepted_without_settled_feed_not_verified(self):
        self.net.settled=False;t=self.task();done=self.s.authorize(t['task_id'],'demo',digest(t['proposed_actions'][0]),True)
        self.assertEqual(done['status'],'MANUAL_REVIEW');self.assertIsNone(done['receipt'])
    def test_uncertain_put_never_automatic_retry(self):
        self.net.failed=True;t=self.task();a=t['proposed_actions'][0]
        done=self.s.authorize(t['task_id'],'demo',digest(a),True);self.assertEqual(done['status'],'MANUAL_REVIEW')
        req=BankRequest('payments.create','demo',action=a,idempotency=BankIdempotency(a['idempotency_key'],digest(a)),deadline=time.time()+30)
        with self.assertRaises(UncertainBankWrite):self.provider.request(req)
        self.assertEqual(self.net.puts,1)
    def test_settled_duplicate_returns_status_despite_balance_change(self):
        t=self.task();a=t['proposed_actions'][0];self.s.authorize(t['task_id'],'demo',digest(a),True)
        req=BankRequest('payments.create','demo',action=a,idempotency=BankIdempotency(a['idempotency_key'],digest(a)),deadline=time.time()+30)
        self.assertEqual(self.provider.request(req).data['status'],'SETTLED');self.assertEqual(self.net.puts,1)
    def test_safe_mode_default_disabled(self):
        self.provider.safe_mode=ExternalBankSafeMode();t=self.task();done=self.s.authorize(t['task_id'],'demo',digest(t['proposed_actions'][0]),True)
        self.assertNotEqual(done['status'],'COMPLETED');self.assertEqual(self.net.puts,0)
    def test_agent_cannot_execute_even_valid_action(self):
        t=self.task()
        with self.assertRaises(Rejected):self.s.gateway.execute('transaction',t['proposed_actions'][0],{})
        self.assertEqual(self.net.puts,0)
    def test_currency_is_not_silently_converted(self):
        t=self.s.create_normalized_task('wrong currency','demo',ACCOUNT,{'task_type':'TRANSFER','needs_clarification':False,'recipient_name':'张三','amount':10,'currency':'CNY'})
        self.assertNotIn(t['status'],{'WAITING_USER','COMPLETED'});self.assertEqual(self.net.puts,0)
    def test_environment_pinned(self):
        fake=BankFixture();fake.base_url='https://evil.example'
        with self.assertRaises(BankError):ReferenceBankAdapter(self.s.db,fake,AuthFixture(),'demo')

    def test_virtual_provider_verification_binding(self):
        with tempfile.TemporaryDirectory() as root:
            local=service(root);t=local.create_task('给张三转1元')
            done=local.authorize(t['task_id'],'demo',digest(t['proposed_actions'][0]),True)
            result=local.gateway._adapter.provider.verify(t['proposed_actions'][0],done['execution']['transaction']['id'])
            self.assertEqual(result.status,'VERIFIED')
    def test_webhook_signature_replay_and_no_authority(self):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048);raw=json.dumps({'feedItemUid':ORDER}).encode()
        sig=base64.b64encode(key.sign(raw,padding.PKCS1v15(),hashes.SHA512())).decode();pub=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
        event=self.provider.webhook(raw,sig,pub);self.assertEqual(event.authority,'QUERY_TRIGGER_ONLY')
        with self.assertRaises(BankError):self.provider.webhook(raw,sig,pub)
        with self.assertRaises(BankError):self.provider.webhook(raw+b' ',sig,pub)
    def test_auth_lifecycle_and_request_signature(self):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048);path=self.tmp.name+'/rsa.pem'
        from pathlib import Path
        Path(path).write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        with patch.dict(os.environ,{'PHASE6_TEST_TOKEN':'synthetic-token'}):
            auth=StarlingAuth('PHASE6_TEST_TOKEN',path,'fixture-key');h=auth.headers('PUT','/path',b'{}')
            signature=h['Authorization'].split('signature="')[1][:-1]
            content=f"(request-target): put /path\nDate: {h['Date']}\nDigest: {h['Digest']}".encode()
            key.public_key().verify(base64.b64decode(signature),content,padding.PKCS1v15(),hashes.SHA512());auth.revoke()
            with self.assertRaises(BankError):auth.headers('GET','/path',b'')
            with self.assertRaises(BankError):StarlingAuth('PHASE6_TEST_TOKEN',expires_at=0).headers('GET','/path',b'')

class TransportTests(unittest.TestCase):
    def setUp(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                status=int(self.path[1:]);self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Retry-After','3');self.end_headers();self.wfile.write(b'{}')
            do_PUT=do_GET
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.transport=BankTransport(f'http://127.0.0.1:{self.http.server_port}')
        self.addCleanup(self.http.server_close);self.addCleanup(self.http.shutdown)
    def test_known_error_and_rate_limit(self):
        for code,name in [(401,'BANK_CREDENTIAL_EXPIRED'),(403,'BANK_SCOPE_DENIED'),(404,'BANK_RESOURCE_NOT_FOUND'),(409,'BANK_CONFLICT'),(429,'BANK_RATE_LIMIT')]:
            with self.assertRaises(BankError) as c:self.transport.send('GET','/'+str(code),b'',{})
            self.assertEqual(c.exception.code,name);self.assertEqual(c.exception.retry_after,'3')
        from backend.bank_sdk.rate_limit import BankRateLimiter
        clock=[0.0];sleeps=[]
        def advance(seconds):sleeps.append(seconds);clock[0]+=seconds
        limiter=BankRateLimiter(5,lambda:clock[0],advance)
        for _ in range(6):limiter.acquire()
        self.assertEqual(len(sleeps),1);self.assertGreaterEqual(clock[0],1.0)
        with self.assertRaises(BankError):limiter.acquire(deadline=0)
    def test_write_500_uncertain(self):
        with self.assertRaises(UncertainBankWrite):self.transport.send('PUT','/503',b'{}',{})
    def test_read_500_known_error(self):
        with self.assertRaises(BankError):self.transport.send('GET','/503',b'',{})
    def test_paths_and_plain_external_endpoints_rejected(self):
        for p in ['//evil.example','/bad\nheader','/\\evil']:
            with self.assertRaises(BankError):self.transport.send('GET',p,b'',{})
        with self.assertRaises(BankError):BankTransport('http://evil.example')
