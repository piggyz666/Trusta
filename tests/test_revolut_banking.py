from datetime import datetime,timezone
from decimal import Decimal
import json
import http.client
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import parse_qs,urlsplit
import uuid

from backend.bank_sdk.contracts import BankError,BankRequest,BankIdempotency,ExternalBankSafeMode,UncertainBankWrite
from backend.bank_sdk.revolut import decimal_json
from backend.bank_sdk.revolut_auth import RevolutTransferAuth,SANDBOX
from backend.bank_sdk.revolut_transfer import RevolutTransferAdapter
from backend.bank_sdk.reconciliation import reconcile
from backend.config import Config
from backend.domain import Rejected,digest
from backend.persistence import Database
from backend.server import create_server
from backend.service import TrustaService
from scripts import revolut_sandbox_connect as bootstrap
from scripts import revolut_transfer_prepare as preparation
from tests import test_revolut_sandbox_connect as bootstrap_tests
from tests import test_revolut_sdk as readonly_tests

A=str(uuid.UUID(int=11));CP=str(uuid.UUID(int=12));DEST=str(uuid.UUID(int=13));TX=str(uuid.UUID(int=14))
PAYEE=CP+':'+DEST


class TransferFixture:
    base_url=SANDBOX
    def __init__(self,currency='GBP'):
        self.calls=[];self.posts=[];self.currency=currency;self.balance=100000;self.raw=None
        self.pending=False;self.lost=False;self.reject=False;self.malformed=False
    def send(self,method,path,body=b'',headers=None):
        self.calls.append((method,path))
        if path=='/api/1.0/accounts':return [{'id':A,'state':'active','name':'Synthetic sandbox','balance':Decimal(self.balance)/100,'currency':self.currency}]
        if path=='/api/1.0/counterparty/'+CP:
            return {'id':CP,'state':'created','name':'Synthetic recipient','accounts':[{'id':DEST,'currency':self.currency,'bank_country':'GB' if self.currency=='GBP' else 'DE'}]}
        if method=='POST' and path=='/api/1.0/pay':
            self.posts.append(decimal_json(body.decode()))
            if self.reject:raise BankError('BANK_SCOPE_DENIED',403)
            p=self.posts[-1];now=datetime.now(timezone.utc).isoformat()
            self.raw={'id':TX,'state':'pending','type':'transfer','request_id':p['request_id'],
                'reference':p['reference'],'created_at':now,'legs':[{'leg_id':DEST,'account_id':A,
                'amount':-p['amount'],'fee':Decimal('0'),'currency':self.currency,
                'counterparty':{'id':CP,'account_id':DEST}}]}
            if not self.pending:self.settle()
            if self.lost:raise UncertainBankWrite()
            if self.malformed:return {'state':'completed'}
            return {'id':TX,'state':self.raw['state']}
        if path=='/api/1.0/transaction/'+TX:return self.raw
        if path.startswith('/api/1.0/transactions?'):
            query=parse_qs(urlsplit(path).query)
            if 'request_id' in query:return [self.raw] if self.raw and self.raw['request_id']==query['request_id'][0] else []
            return []
        raise AssertionError('unexpected synthetic bank endpoint')
    def settle(self):
        self.balance-=int(-self.raw['legs'][0]['amount']*100)
        self.raw['state']='completed';self.raw['completed_at']=datetime.now(timezone.utc).isoformat()
        self.raw['legs'][0]['balance']=Decimal(self.balance)/100


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Database(str(Path(self.temp.name)/'test.sqlite'))
        self.net=TransferFixture()
        self.auth=SimpleNamespace(headers=lambda *a,**k:{'Authorization':'Bearer synthetic'})
        self.provider=RevolutTransferAdapter(self.db,self.net,self.auth,'demo',ExternalBankSafeMode('sandbox',True,frozenset({A}),frozenset({PAYEE}),100))
        self.provider.rate_limiter=SimpleNamespace(acquire=lambda *a,**k:None)
        self.s=TrustaService(Config(db_path=self.db.path,banking_version=5,communication_version=3,signing_key=b'synthetic-32-byte-transfer-key'),bank_provider=self.provider,recover_workflows=False)
    def propose(self,amount='0.01'):
        payee=self.s.transaction.recipient('demo',self.provider._payees()[0]['name'])
        task=self.s.create_task('从 '+A+' 给 '+payee['name']+' 转 '+amount+' '+self.net.currency,source_account_id=A)
        self.assertEqual(task['status'],'WAITING_USER')
        return task
    def approve(self,task):
        return self.s.authorize(task['task_id'],'demo',task['authorization']['action_hash'],True)

    def test_original_pipeline_waits_for_explicit_authorization(self):
        task=self.propose();self.assertEqual(self.net.posts,[])
        done=self.approve(task)
        self.assertEqual(done['status'],'COMPLETED');self.assertEqual(done['receipt']['verification_status'],'VERIFIED')
        self.assertEqual(len(self.net.posts),1);self.assertEqual(self.net.posts[0]['amount'],Decimal('.01'))
        self.assertEqual(self.net.posts[0]['receiver'],{'counterparty_id':CP,'account_id':DEST})
        self.assertTrue(self.s.audit.verify_chain())
        self.assertIsNotNone(self.db.one('SELECT body FROM execution_contracts'))

    def test_native_eur_transfer_does_not_convert_currency(self):
        self.net.currency='EUR';done=self.approve(self.propose())
        self.assertEqual(done['status'],'COMPLETED');self.assertEqual(self.net.posts[0]['currency'],'EUR')

    def test_denial_and_wrong_hash_never_send_payment(self):
        task=self.propose()
        with self.assertRaises(Rejected):self.s.authorize(task['task_id'],'demo','0'*64,True)
        task=self.s.authorize(task['task_id'],'demo',task['authorization']['action_hash'],False)
        self.assertEqual(task['status'],'CANCELLED');self.assertEqual(self.net.posts,[])

    def test_safe_mode_amount_owner_and_missing_contract_deny(self):
        task=self.propose('1.01');done=self.approve(task)
        self.assertEqual(done['status'],'FAILED');self.assertEqual(self.net.posts,[])
        with self.assertRaises(Rejected):self.s.gateway.execute('executor',task['proposed_actions'][0],{})
        with self.assertRaises(BankError):self.provider.request(BankRequest('payments.status','other',{'transaction_id':TX}))

    def test_known_bank_id_duplicate_only_queries_status(self):
        task=self.approve(self.propose());a=task['proposed_actions'][0]
        req=BankRequest('payments.create','demo',{},a,BankIdempotency(a['idempotency_key'],digest(a)),a['expires_at'])
        self.assertEqual(self.provider.request(req).data['status'],'SETTLED');self.assertEqual(len(self.net.posts),1)

    def test_lost_post_response_recovers_by_get_and_preserves_terminal_history(self):
        self.net.lost=True;task=self.approve(self.propose())
        self.assertEqual(task['status'],'MANUAL_REVIEW');self.assertIsNone(task['receipt'])
        steps=task['steps'];done=reconcile(self.s,task['task_id'],'demo')
        self.assertEqual(done['status'],'MANUAL_REVIEW');self.assertEqual(done['steps'],steps)
        self.assertEqual(done['reconciliation']['status'],'VERIFIED');self.assertIsNotNone(done['reconciliation']['receipt'])
        again=reconcile(self.s,task['task_id'],'demo');self.assertEqual(again,done)
        self.assertEqual(len(self.net.posts),1);self.assertEqual(len(self.db.rows('SELECT * FROM receipts')),1)

    def test_pending_late_settlement_only_generates_supplementary_receipt(self):
        self.net.pending=True;task=self.approve(self.propose())
        pending=reconcile(self.s,task['task_id'],'demo')
        self.assertEqual(pending['reconciliation']['status'],'MANUAL_REVIEW');self.assertIsNone(pending['reconciliation']['receipt'])
        self.net.settle();done=reconcile(self.s,task['task_id'],'demo')
        self.assertEqual(done['reconciliation']['status'],'VERIFIED');self.assertEqual(len(self.net.posts),1)

    def test_malformed_post_response_remains_unknown_and_cannot_resend(self):
        self.net.malformed=True;task=self.approve(self.propose());a=task['proposed_actions'][0]
        with self.assertRaises(UncertainBankWrite):self.provider.request(BankRequest('payments.create','demo',{},a,BankIdempotency(a['idempotency_key'],digest(a)),a['expires_at']))
        self.assertEqual(len(self.net.posts),1)

    def test_403_does_not_refresh_or_retry_post(self):
        self.net.reject=True;task=self.approve(self.propose())
        self.assertEqual(task['status'],'FAILED');self.assertEqual(len(self.net.posts),1)

    def test_wrong_amount_route_fee_reference_or_time_never_verified(self):
        for field,value in [('amount',Decimal('-.02')),('fee',Decimal('.01')),('counterparty',{'id':CP,'account_id':A})]:
            with self.subTest(field=field):
                self.net.pending=True;task=self.approve(self.propose());self.net.settle()
                self.net.raw['legs'][0][field]=value
                self.assertEqual(reconcile(self.s,task['task_id'],'demo')['reconciliation']['status'],'MANUAL_REVIEW')
        task=self.approve(self.propose());self.net.settle();self.net.raw['reference']='not-authorized'
        self.assertEqual(reconcile(self.s,task['task_id'],'demo')['reconciliation']['status'],'MANUAL_REVIEW')

    def test_other_customer_and_tampered_contract_cannot_reconcile(self):
        self.net.pending=True;task=self.approve(self.propose())
        with self.assertRaises(Rejected):reconcile(self.s,task['task_id'],'other')
        with self.db.transaction() as conn:
            row=conn.execute('SELECT id,body FROM execution_contracts').fetchone();body=json.loads(row['body']);body['expires_at']+=100
            conn.execute('UPDATE execution_contracts SET body=? WHERE id=?',(json.dumps(body),row['id']))
        with self.assertRaises(Rejected):reconcile(self.s,task['task_id'],'demo')
        self.assertEqual(len(self.net.posts),1)

    def test_no_matching_lost_response_stays_manual_review(self):
        self.net.lost=True;task=self.approve(self.propose());self.net.raw=None
        done=reconcile(self.s,task['task_id'],'demo')
        self.assertEqual(done['reconciliation']['status'],'MANUAL_REVIEW');self.assertEqual(len(self.net.posts),1)

    def test_extra_capabilities_and_scheduled_payments_denied(self):
        with self.assertRaises(BankError):self.provider.request(BankRequest('cards.freeze','demo'))
        self.assertNotIn('investments.purchase',self.provider.manifest.capabilities)
        with self.assertRaises(BankError):RevolutTransferAdapter(self.db,self.net,self.auth,'demo',ExternalBankSafeMode('production',True))
        with self.assertRaises(BankError):RevolutTransferAdapter(self.db,self.net,self.auth,'demo',ExternalBankSafeMode('sandbox',True,frozenset({A}),frozenset({PAYEE}),100,True))
        task=self.propose();a=task['proposed_actions'][0]
        with self.assertRaises(BankError):self.provider.request(BankRequest('payments.create','demo',{'scheduled':True},a,BankIdempotency(a['idempotency_key'],digest(a)),a['expires_at']))
        self.assertEqual(self.net.posts,[])


class TransferAuthTests(unittest.TestCase):
    def setUp(self):
        fixture=bootstrap_tests.RevolutBootstrapTests();fixture.setUp();self.addCleanup(fixture.temp.cleanup)
        self.setup=fixture.setup;self.root=fixture.root;self.path=self.root/'tokens.json'
        self.saved={'client_id':'synthetic-client','issuer':'trusta.localhost','sandbox_only':True,
            'access_token':'synthetic-token','refresh_token':'synthetic-refresh','token_type':'bearer','expires_in':2399,
            'obtained_at':datetime.now(timezone.utc).isoformat()}
        self.path.write_text(json.dumps(self.saved))
        self.net=readonly_tests.Fixture()
        self.auth=RevolutTransferAuth(self.net,'synthetic-client',self.setup['private_key_path'],self.setup['public_certificate_path'],self.path)
    def test_read_token_can_discover_but_cannot_sign_payment(self):
        self.assertIn('Authorization',self.auth.headers('GET','/api/1.0/counterparties?limit=1000'))
        with self.assertRaises(BankError):self.auth.headers('POST','/api/1.0/pay',b'{}')
    def test_explicit_pay_consent_marker_required_and_preserved_on_refresh(self):
        self.path.write_text(json.dumps(dict(self.saved,scope_requested='READ,PAY',scope='READ,PAY',obtained_at='2020-01-01T00:00:00+00:00')))
        self.assertIn('Authorization',self.auth.headers('POST','/api/1.0/pay',b'{}'))
        self.assertEqual(json.loads(self.path.read_text())['scope_requested'],'READ,PAY')
    def test_management_sensitive_and_production_endpoints_denied(self):
        for method,path in [('POST','/api/1.0/counterparty'),('GET','/api/1.0/cards'),('POST','/api/1.0/exchange')]:
            with self.assertRaises(BankError):self.auth.headers(method,path,b'{}')
        self.path.write_text(json.dumps(dict(self.saved,scope='READ,WRITE,PAY')))
        with self.assertRaises(BankError):self.auth.access_token()
    def test_bootstrap_pay_stores_separate_credentials_and_never_pays(self):
        calls=[]
        def request(path,**kwargs):
            calls.append(path)
            return {'access_token':'synthetic','refresh_token':'synthetic','token_type':'bearer','scope':'READ,PAY'} if path=='/auth/token' else []
        with patch.object(bootstrap,'ROOT',self.root):
            report=bootstrap.connect(dict(self.setup,oauth_permissions='READ,PAY'),'synthetic-code',returned_state=True,request=request)
        self.assertEqual(calls,['/auth/token','/accounts']);self.assertEqual(report['scope_requested'],'READ,PAY')
        self.assertFalse((self.root/'oauth-tokens.local.json').exists())
        self.assertTrue((self.root/'oauth-payment-tokens.local.json').exists())

    def test_browser_disconnect_does_not_report_successful_exchange_as_bank_failure(self):
        flow=bootstrap.Flow(dict(self.setup,oauth_permissions='READ,PAY'))
        flow.accept({'code':['synthetic-code'],'state':[flow.state]})
        handler=bootstrap.handler_for(flow)
        server=bootstrap.ThreadingHTTPServer(('127.0.0.1',0),handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2);self.addCleanup(conn.close)
        output=io.StringIO()
        with patch.object(bootstrap,'connect',return_value={'status':'ACCOUNT_READ_VERIFIED','account_count':5}) as exchange,patch.object(handler,'_page',side_effect=ConnectionAbortedError()),patch.object(bootstrap.threading,'Timer'),patch('sys.stdout',output):
            conn.request('POST','/exchange',body='csrf='+flow.csrf,headers={'Host':'trusta.localhost','Origin':bootstrap.ORIGIN,'Cookie':'__Host-trusta-oauth='+flow.cookie})
            with self.assertRaises(http.client.RemoteDisconnected):conn.getresponse()
            exchange.assert_called_once();self.assertTrue(flow.used)
        self.assertIn('ACCOUNT_READ_VERIFIED',output.getvalue())
        self.assertIn('LOCAL_RESPONSE_DISCONNECTED',output.getvalue())
        self.assertNotIn('SANDBOX_CONNECT_FAILED',output.getvalue())


class PreparationTests(unittest.TestCase):
    def setUp(self):
        fixture=TransferTests();fixture.setUp();self.addCleanup(fixture.temp.cleanup)
        self.s,self.net=fixture.s,fixture.net
        self.profile=Path(fixture.temp.name)/'bank.local.toml'
        self.profile.write_text('provider="revolut"\nenvironment="sandbox"\ncustomer_id="demo"\ntransfer_profile=true\nwrites_enabled=false\nsource_accounts=["'+A+'"]\npayees=["'+PAYEE+'"]\nmaximum_amount=100\n')

    def test_preparation_only_proposes_and_reuses_same_pending_action(self):
        with patch.object(preparation,'build',return_value=(self.s,'synthetic')):
            first=preparation.prepare(self.profile);again=preparation.prepare(self.profile)
        self.assertEqual(first['status'],'WAITING_USER');self.assertEqual(first['task_id'],again['task_id'])
        self.assertEqual(self.net.posts,[])
        self.assertEqual(self.s.db.rows('SELECT * FROM execution_contracts'),[])

    def test_missing_bank_consent_does_not_enable_profile_or_propose(self):
        with patch.object(preparation,'build',return_value=(self.s,'synthetic')),patch.object(self.s.gateway._adapter.provider.auth,'headers',side_effect=BankError('BANK_PAYMENT_SCOPE_NOT_AUTHORIZED')):
            with self.assertRaises(BankError):preparation.prepare(self.profile)
        self.assertIn('writes_enabled=false',self.profile.read_text())
        self.assertEqual(self.s.blackboard.list('demo'),[]);self.assertEqual(self.net.posts,[])


class BankUITests(unittest.TestCase):
    def setUp(self):
        fixture=readonly_tests.RevolutSDKTests();fixture.setUp();self.addCleanup(fixture.tmp.cleanup)
        self.s,self.net=fixture.s,fixture.net
        self.server=create_server(self.s,'synthetic-ui-token',0,{'synthetic-ui-token':'demo','synthetic-other-token':'other'})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
    def request(self,path,token='synthetic-ui-token'):
        req=Request(f'http://127.0.0.1:{self.server.server_port}'+path,headers={'Authorization':'Bearer '+token})
        try:
            with urlopen(req) as r:return r.status,json.loads(r.read())
        except HTTPError as e:return e.code,json.loads(e.read())
    def test_authenticated_catalog_and_native_balance(self):
        status,r=self.request('/api/bank');self.assertEqual(status,200)
        self.assertEqual(r['accounts'][0]['currency'],'GBP');self.assertFalse(r['writes_enabled'])
        status,balance=self.request('/api/balance?account_id='+readonly_tests.ACCOUNT)
        self.assertEqual(balance['balance'],100023);self.assertEqual(balance['currency'],'GBP')
        self.assertNotIn('token',json.dumps(r).lower())
    def test_unauthorised_user_and_bad_query_denied(self):
        self.assertEqual(self.request('/api/bank','bad')[0],403)
        self.assertNotEqual(self.request('/api/bank','synthetic-other-token')[0],200)
        self.assertEqual(self.request('/api/balance?account_id=x&account_id=y')[0],400)
        self.assertEqual(self.request('/api/transactions?account_id=x&month=2026-10&extra=y')[0],400)
    def test_records_read_without_exposing_sensitive_fields_and_readonly_payees_empty(self):
        status,r=self.request('/api/transactions?account_id='+readonly_tests.ACCOUNT+'&month=2026-10')
        self.assertEqual(status,200);self.assertEqual(len(r['records']),2)
        self.assertNotIn('synthetic-sensitive',json.dumps(r))
        self.assertEqual(self.request('/api/payees')[1]['payees'],[])


if __name__=='__main__':unittest.main()
