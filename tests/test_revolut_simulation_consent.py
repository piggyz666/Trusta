import http.client
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit

from backend.bank_sdk.revolut_auth import RevolutAuth,RevolutTransferAuth
from backend.bank_sdk.contracts import BankError
from scripts import revolut_sandbox_connect as mod
from tests import test_revolut_sandbox_connect as bootstrap_tests


class SimulationConsentTests(unittest.TestCase):
    def setUp(self):
        fixture=bootstrap_tests.RevolutBootstrapTests();fixture.setUp();self.addCleanup(fixture.temp.cleanup)
        self.root=fixture.root
        self.setup=dict(fixture.setup,oauth_permissions='READ,WRITE',operator_simulation_only=True)

    def test_operator_scope_requires_explicit_mode_and_excludes_pay(self):
        query=parse_qs(urlsplit(mod.authorization_url(self.setup,'test-state')).query)
        self.assertEqual(query['scope'],['READ,WRITE'])
        with self.assertRaises(ValueError):mod.requested_scope(dict(self.setup,operator_simulation_only=False))
        for scope in ('READ,WRITE,PAY','READ_SENSITIVE_CARD_DATA','WRITE','PAY'):
            with self.assertRaises(ValueError):mod.requested_scope(dict(self.setup,oauth_permissions=scope))

    def test_consent_saves_only_separate_operator_credentials_and_reads_accounts(self):
        for name in ('oauth-tokens.local.json','oauth-payment-tokens.local.json'):
            (self.root/name).write_text('original')
        calls=[]
        def request(path,**kwargs):
            calls.append(path)
            return ({'access_token':'synthetic-access','refresh_token':'synthetic-refresh','token_type':'bearer','scope':'READ,WRITE'}
                if path=='/auth/token' else [{'id':'synthetic-private-account','currency':'EUR'}])
        with patch.object(mod,'ROOT',self.root):
            report=mod.connect(self.setup,'synthetic-code',returned_state=True,request=request)
        self.assertEqual(calls,['/auth/token','/accounts'])
        self.assertFalse(report['simulation_permission_verified']);self.assertEqual(report['bank_write_calls'],0)
        saved=json.loads((self.root/'oauth-simulation-tokens.local.json').read_text())
        self.assertTrue(saved['operator_simulation_only'])
        for name in ('oauth-tokens.local.json','oauth-payment-tokens.local.json'):
            self.assertEqual((self.root/name).read_text(),'original')
        for secret in ('synthetic-access','synthetic-refresh','synthetic-private-account','synthetic-code'):
            self.assertNotIn(secret,json.dumps(report))

    def test_extra_pay_or_sensitive_scope_is_rejected_before_storage(self):
        for scope in ('READ,WRITE,PAY','READ,WRITE,READ_SENSITIVE_CARD_DATA'):
            with self.assertRaises(ValueError):
                mod.connect(self.setup,'code',returned_state=True,request=lambda *a,**kw:
                    {'access_token':'synthetic','token_type':'bearer','scope':scope})
            self.assertFalse((self.root/'oauth-simulation-tokens.local.json').exists())

    def test_business_authentication_rejects_write_credentials(self):
        for row in ({'scope':'READ,WRITE'},
                    {'scope_requested':'READ,WRITE','operator_simulation_only':True},
                    {'operator_simulation_only':True}):
            for auth in (RevolutAuth,RevolutTransferAuth):
                with self.assertRaises(BankError):auth._validate_scope(row)

    def test_start_page_explains_management_grant_and_does_not_exchange(self):
        flow=mod.Flow(self.setup)
        server=mod.ThreadingHTTPServer(('127.0.0.1',0),mod.handler_for(flow))
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);self.addCleanup(conn.close)
        with patch.object(mod,'connect') as exchange:
            conn.request('GET','/start',headers={'Host':'trusta.localhost'})
            response=conn.getresponse();body=response.read().decode()
            self.assertEqual(response.status,200)
            self.assertIn('Manage your account details',body)
            self.assertIn('关闭 Make payments',body);self.assertIn('403',body)
            self.assertIn('SameSite=Lax',response.getheader('Set-Cookie'))
            exchange.assert_not_called()


if __name__=='__main__':unittest.main()
