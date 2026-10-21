from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import uuid

from backend.audit import AuditEngine
from backend.bank_sdk.configuration import reference_from_config
from backend.bank_sdk.contracts import BankError, BankRequest
from backend.bank_sdk.revolut import RevolutReadOnlyAdapter, decimal_json, minor_units
from backend.bank_sdk.revolut_auth import RevolutAuth, SANDBOX, TOKEN_PATH
from backend.config import Config
from backend.domain import Rejected
from backend.persistence import Database
from backend.runtime.taskgraph import node
from backend.service import TrustaService
from tests import test_revolut_sandbox_connect as bootstrap_tests

ACCOUNT=str(uuid.UUID(int=1)); OTHER=str(uuid.UUID(int=2))


def transaction(n, date='2026-10-06T12:00:00Z', kind='card_payment', amount='-11',
                account=ACCOUNT, currency='GBP', state='completed'):
    return {'id':str(uuid.UUID(int=100+n)), 'created_at':date,'type':kind,'state':state,
            'merchant':{'name':'Synthetic merchant','category_code':'7311'},
            'card':{'card_number':'synthetic-sensitive-card'},'auth_code':'synthetic-sensitive-auth',
            'legs':[{'leg_id':str(uuid.UUID(int=1000+n)), 'account_id':account,
                     'amount':Decimal(amount),'currency':currency}]}


class Fixture:
    base_url=SANDBOX
    def __init__(self,rows=()): self.rows=list(rows);self.calls=[];self.refreshes=0;self.unauthorized=False
    def send(self,method,path,body,headers):
        self.calls.append((method,path))
        if method=='POST' and path==TOKEN_PATH:
            self.refreshes+=1
            params=parse_qs(body.decode())
            assert params['grant_type']==['refresh_token']
            assert headers['Content-Type']=='application/x-www-form-urlencoded'
            return {'access_token':'fresh-synthetic-token','token_type':'bearer','expires_in':2399}
        assert method=='GET'
        if self.unauthorized:
            self.unauthorized=False
            raise BankError('BANK_CREDENTIAL_EXPIRED',401)
        if path=='/api/1.0/accounts':
            return [{'id':ACCOUNT,'name':'Synthetic GBP','balance':Decimal('1000.23'),'currency':'GBP','state':'active'}]
        query=parse_qs(urlsplit(path).query)
        assert query['account']==[ACCOUNT]
        parsed=lambda s:datetime.fromisoformat(s.replace('Z','+00:00'))
        start,end=parsed(query['from'][0]),parsed(query['to'][0])
        return sorted([r for r in self.rows if start<=parsed(r['created_at'])<end],
                      key=lambda r:r['created_at'],reverse=True)[:int(query['count'][0])]


class AuthStub:
    def headers(self,method,path,body=b'',**kwargs): return {'Authorization':'Bearer synthetic'}


class RevolutSDKTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=Database(str(self.root/'service.sqlite3'))
        self.net=Fixture([transaction(1),transaction(2,kind='topup',amount='50')])
        self.provider=RevolutReadOnlyAdapter(self.db,self.net,AuthStub(),'demo')
        self.s=TrustaService(Config(db_path=str(self.root/'service.sqlite3'),banking_version=5,
            communication_version=3,signing_key=b'synthetic-revolut-test-key-32bytes'),
            bank_provider=self.provider,recover_workflows=False)

    def test_money_exactness_and_fail_closed(self):
        self.assertEqual(minor_units(Decimal('0.29'),'GBP'),29)
        self.assertEqual(minor_units(Decimal('-21.50'),'GBP'),-2150)
        self.assertEqual(minor_units(Decimal('900719925474.09'),'USD'),90071992547409)
        for value,currency in [(True,'GBP'),(.29,'GBP'),(Decimal('.001'),'GBP'),
                               (Decimal('NaN'),'GBP'),(Decimal('1e100'),'GBP'),(1,'JPY')]:
            with self.subTest(value=value),self.assertRaises(BankError):minor_units(value,currency)

    def test_wire_json_preserves_decimal_and_rejects_duplicates(self):
        value=decimal_json('{"balance":900719925474.09}')
        self.assertEqual(value['balance'],Decimal('900719925474.09'))
        for text in ('{"x":1,"x":2}','{"balance":NaN}'):
            with self.assertRaises(ValueError):decimal_json(text)

    def test_account_currency_and_customer_mapping(self):
        account=self.s.finance.account('demo',ACCOUNT)
        self.assertEqual(account['balance'],100023);self.assertEqual(account['currency'],'GBP')
        self.assertEqual(account['customer_id'],'demo')
        with self.assertRaises(Rejected):self.s.finance.account('other',ACCOUNT)
        self.assertTrue(self.s.audit.verify_chain())

    def test_provider_owner_denies_before_bank_call(self):
        with self.assertRaises(BankError):self.provider.request(BankRequest('accounts.read','other'))
        self.assertEqual(self.net.calls,[])

    def test_manifest_write_and_unknown_capabilities_denied(self):
        for cap in ('payments.create','cards.freeze','investments.products.read','payments.status'):
            with self.assertRaises(BankError):self.provider.request(BankRequest(cap,'demo'))
        with self.assertRaises(BankError):self.provider.verify({},'id')
        self.assertEqual(self.net.calls,[])
        self.assertNotIn('transfer.create',self.s.gateway._adapter.manifest)

    def test_agent_capability_grants_not_broadened(self):
        with self.assertRaises(Rejected):self.s.gateway.read('finance','demo','account.get_balance',account_id=ACCOUNT)
        with self.assertRaises(Rejected):self.s.gateway.execute('finance',{'task_id':'synthetic','action_type':'TRANSFER'}, {})
        self.assertEqual(self.net.calls,[])

    def test_no_cross_account_legs_or_sensitive_fields(self):
        self.net.rows[0]['legs'].append({'leg_id':str(uuid.UUID(int=2000)),'account_id':OTHER,
            'amount':Decimal('11'),'currency':'EUR','description':'other-private'})
        rows=self.s.finance.records('demo',ACCOUNT,'2026-10')
        self.assertEqual(len(rows),2)
        value=json.dumps(rows)
        for secret in (OTHER,'synthetic-sensitive-card','synthetic-sensitive-auth','other-private'):
            self.assertNotIn(secret,value)

    def test_no_matching_leg_or_wrong_currency_rejected(self):
        for row in (transaction(1,account=OTHER),transaction(1,currency='EUR')):
            self.net.rows=[row]
            with self.assertRaises(BankError):self.s.finance.records('demo',ACCOUNT,'2026-10')

    def test_bill_analysis_excludes_topup_and_uses_native_currency(self):
        task=self.s.create_task('分析2026-10消费',source_account_id=ACCOUNT)
        self.assertEqual(task['status'],'COMPLETED')
        summary=task['result']['summary']
        self.assertEqual(summary['currency'],'GBP');self.assertEqual(summary['gross_spend'],1100)
        self.assertEqual(summary['excluded_count'],1)
        self.assertEqual(task['result']['categories'][0]['category'],'UNCLASSIFIED')
        self.assertIsNone(task['result']['anomalies']['rules']['large_amount_cents'])
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])
        self.assertTrue(self.s.audit.verify_chain())
        self.assertTrue(all(m=='GET' for m,p in self.net.calls))

    def test_card_refund_settlement_and_month_boundary(self):
        self.net.rows=[transaction(1,amount='-12.35'),transaction(2,kind='card_refund',amount='2.35'),
                       transaction(3,state='pending'),transaction(4,date='2026-09-30T15:59:59Z'),
                       transaction(5,date='2026-09-30T16:00:00Z')]
        rows=self.s.finance.records('demo',ACCOUNT,'2026-10')
        self.assertEqual(len(rows),4)
        result=self.s.finance.summarize(rows)
        self.assertEqual(result['net_spend'],2100);self.assertEqual(result['excluded_count'],1)

    def test_empty_month_correct_currency_and_ambiguous_graph_summary_rejected(self):
        self.net.rows=[]
        task=self.s.create_task('分析2026-09消费',source_account_id=ACCOUNT)
        self.assertEqual(task['status'],'COMPLETED');self.assertEqual(task['result']['summary']['currency'],'GBP')
        with self.assertRaises(Rejected):self.s.finance.summarize([])

    def test_mixed_currency_never_aggregated(self):
        rows=self.s.finance.records('demo',ACCOUNT,'2026-10')
        rows.append(dict(rows[0],currency='EUR'))
        with self.assertRaises(Rejected):self.s.finance.summarize(rows)

    def test_finance_presentation_keeps_native_currency(self):
        from backend.llm.explanations import finance_presentation
        report=self.s.create_task('分析2026-10消费',source_account_id=ACCOUNT)['result']
        text=finance_presentation(report,{'focus':'summary','style':'concise'})['text']
        self.assertIn('11.00 GBP',text);self.assertNotIn('元',text)

    def test_pagination_overlap_deduplicated_completely(self):
        self.provider.page_size=2
        self.net.rows=[transaction(i,date=f'2026-10-0{i}T12:00:00Z') for i in range(1,5)]
        rows=self.s.finance.records('demo',ACCOUNT,'2026-10')
        self.assertEqual(len(rows),4);self.assertEqual(len({r['id'] for r in rows}),4)
        self.assertEqual(sum('transactions?' in p for m,p in self.net.calls),4)

    def test_timestamp_group_overflow_does_not_publish_partial_report(self):
        self.provider.page_size=2
        self.net.rows=[transaction(i) for i in range(1,4)]
        with self.assertRaises(BankError) as exc:self.s.finance.records('demo',ACCOUNT,'2026-10')
        self.assertEqual(exc.exception.code,'BANK_PAGINATION_INCOMPLETE')

    def test_graph_reads_via_gateway_and_retains_signed_audit(self):
        graph={'schema_version':1,'nodes':[
            node('Read','READ','finance','transactions.read',parameters={'account_id':'input:account','month':'input:month'}),
            node('Sum','ANALYZE','finance','finance.summary',['Read'],{'records':'Read:result'}),
            node('Report','REPORT','supervisor','graph.report',['Sum'])]}
        task=self.s.graphs.create('demo','synthetic bank bill',graph,{'input:account':ACCOUNT,'input:month':'2026-10'})
        self.assertEqual(task['status'],'COMPLETED');self.assertEqual(task['result']['Sum:result']['currency'],'GBP')
        self.assertTrue(self.s.audit.verify_chain());self.assertEqual(task['proposed_actions'],[])

    def test_configuration_read_only_and_sandbox_pinned(self):
        values={'provider':'revolut','environment':'sandbox','customer_id':'demo','client_id':'synthetic',
            'issuer':'trusta.localhost','key_path':str(self.root/'key.pem'),'certificate_path':str(self.root/'public.cer'),
            'token_path':str(self.root/'tokens.json')}
        path=self.root/'bank.toml'
        def write(changes):
            row=dict(values,**changes)
            path.write_text('\n'.join(k+' = '+(str(v).lower() if type(v) is bool else json.dumps(v)) for k,v in row.items()))
        write({});self.assertEqual(reference_from_config(path,self.db).manifest.provider,'revolut')
        for changes in ({'environment':'production'},{'writes_enabled':True},{'endpoint':'https://evil.invalid'},
                        {'issuer':'evil.invalid'},{'page_size':True}):
            write(changes)
            with self.assertRaises(Rejected):reference_from_config(path,self.db)


class RevolutAuthTests(unittest.TestCase):
    def setUp(self):
        fixture=bootstrap_tests.RevolutBootstrapTests();fixture.setUp()
        self.addCleanup(fixture.temp.cleanup)
        self.root,self.setup=fixture.root,fixture.setup
        self.path=self.root/'tokens.json';self.net=Fixture();self.clock=datetime.now(timezone.utc).timestamp()
        self.saved={'access_token':'expired-synthetic','refresh_token':'synthetic-refresh','expires_in':2399,
            'token_type':'bearer','obtained_at':datetime.fromtimestamp(self.clock-3000,timezone.utc).isoformat(),
            'client_id':'synthetic-client','issuer':'trusta.localhost','sandbox_only':True}
        self.path.write_text(json.dumps(self.saved))
        self.auth=RevolutAuth(self.net,'synthetic-client',self.setup['private_key_path'],
                             self.setup['public_certificate_path'],self.path,clock=lambda:self.clock)

    def test_expired_refresh_preserves_refresh_token_and_reuses_new_access(self):
        self.assertEqual(self.auth.access_token(),'fresh-synthetic-token')
        self.assertEqual(self.auth.access_token(),'fresh-synthetic-token')
        self.assertEqual(self.net.refreshes,1)
        self.assertEqual(json.loads(self.path.read_text())['refresh_token'],'synthetic-refresh')

    def test_concurrent_instances_refresh_once(self):
        other=RevolutAuth(self.net,'synthetic-client',self.setup['private_key_path'],
                         self.setup['public_certificate_path'],self.path,clock=lambda:self.clock)
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda a:a.access_token(),[self.auth,other]))
        self.assertEqual(results,['fresh-synthetic-token']*2);self.assertEqual(self.net.refreshes,1)

    def test_401_refresh_then_retry_get_once(self):
        self.auth.access_token();self.net.unauthorized=True
        provider=RevolutReadOnlyAdapter(Database(str(self.root/'test.sqlite')),self.net,self.auth,'demo')
        self.assertEqual(len(provider.request(BankRequest('accounts.read','demo')).data),1)
        self.assertEqual(self.net.refreshes,2)

    def test_revoked_invalid_owner_and_bad_scope_do_not_refresh(self):
        self.auth.revoke()
        with self.assertRaises(BankError):self.auth.access_token()
        self.auth.revoked=False
        for changes in ({'client_id':'other'},{'issuer':'other'},{'scope':'READ,PAY'},{'sandbox_only':False}):
            self.path.write_text(json.dumps(dict(self.saved,**changes)))
            with self.assertRaises(BankError):self.auth.access_token()
        self.assertEqual(self.net.refreshes,0)

    def test_refresh_scope_escalation_never_saved(self):
        with patch.object(self.net,'send',return_value={'access_token':'bad','expires_in':2399,
                                                       'token_type':'bearer','scope':'READ,WRITE'}):
            with self.assertRaises(BankError):self.auth.access_token()
        self.assertEqual(json.loads(self.path.read_text()),self.saved)

    def test_signing_key_mismatch_blocks_refresh(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        self.root.joinpath('key.pem').write_bytes(rsa.generate_private_key(public_exponent=65537,key_size=2048)
            .private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        with self.assertRaises(BankError):self.auth.access_token()
        self.assertEqual(self.net.refreshes,0)

    def test_auth_cannot_sign_payment_or_read_sensitive_card_endpoint(self):
        for method,path in [('POST','/api/1.0/pay'),('GET','/api/1.0/cards')]:
            with self.assertRaises(BankError):self.auth.headers(method,path)
        self.assertEqual(self.net.refreshes,0)


if __name__=='__main__':unittest.main()
