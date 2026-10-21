import base64
from datetime import datetime, timedelta, timezone
import http.client
from http.server import ThreadingHTTPServer
import json
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from scripts import revolut_sandbox_connect as mod


class RevolutBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'test')])
        now = datetime.now(timezone.utc)
        self.cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                     .public_key(self.key.public_key()).serial_number(x509.random_serial_number())
                     .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
                     .sign(self.key, hashes.SHA256()))
        (self.root/'key.pem').write_bytes(self.key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (self.root/'public.cer').write_bytes(self.cert.public_bytes(serialization.Encoding.PEM))
        self.setup = {'scope':'REVOLUT_SANDBOX_ONLY', 'client_id':'synthetic-client',
            'oauth_redirect_uri':mod.CALLBACK, 'private_key_path':str(self.root/'key.pem'),
            'public_certificate_path':str(self.root/'public.cer')}

    def test_url_uses_read_scope_and_new_callback(self):
        url = urlsplit(mod.authorization_url(self.setup, 'random-state'))
        self.assertEqual(url.netloc, 'sandbox-business.revolut.com')
        self.assertEqual(parse_qs(url.query), {'client_id':['synthetic-client'],
            'redirect_uri':[mod.CALLBACK], 'response_type':['code'], 'scope':['READ'],
            'state':['random-state']})

    def test_config_rejects_production_and_wrong_callback(self):
        path = self.root/'setup.json'
        for change in ({'scope':'PRODUCTION'}, {'oauth_redirect_uri':'https://example.com'},
                       {'client_id':'bad&scope=PAY'}):
            path.write_text(json.dumps(dict(self.setup, **change)))
            with self.assertRaises(ValueError): mod.load_setup(path)

    def test_jwt_claims_and_signature(self):
        a, b, signature = mod.assertion(self.setup, now=100).split('.')
        decode = lambda s: base64.urlsafe_b64decode(s + '='*((-len(s))%4))
        self.assertEqual(json.loads(decode(a)), {'alg':'RS256', 'typ':'JWT'})
        self.assertEqual(json.loads(decode(b)), {'iss':'trusta.localhost',
            'sub':'synthetic-client', 'aud':'https://revolut.com', 'exp':400})
        self.cert.public_key().verify(decode(signature), (a+'.'+b).encode(),
                                      padding.PKCS1v15(), hashes.SHA256())

    def test_callback_state_mismatch_and_duplicates_rejected(self):
        flow = mod.Flow(self.setup)
        for query in ({'code':['secret'], 'state':['wrong']}, {'code':['a','b']},
                      {'error':['denied']}, {}):
            with self.assertRaises(ValueError): flow.accept(query)
        self.assertIsNone(flow.code)

    def test_no_state_still_needs_explicit_confirmation_and_is_single_use(self):
        flow = mod.Flow(self.setup)
        flow.accept({'code':['synthetic-code']})
        self.assertFalse(flow.returned_state)
        with self.assertRaises(ValueError): flow.consume('wrong')
        self.assertEqual(flow.consume(flow.csrf), 'synthetic-code')
        self.assertIsNone(flow.code)
        with self.assertRaises(ValueError): flow.consume(flow.csrf)
        with self.assertRaises(ValueError): flow.accept({'code':['another']})

    def test_expired_code_is_not_exchanged(self):
        flow = mod.Flow(self.setup)
        flow.accept({'code':['synthetic-code'], 'state':[flow.state]})
        flow.code_received_at -= 111
        with self.assertRaises(ValueError): flow.consume(flow.csrf)

    def test_endpoint_allowlist_blocks_payments_and_redirects(self):
        for path in ('/pay','/transfer','https://b2b.revolut.com/api/1.0/accounts'):
            with self.assertRaises(ValueError): mod.bank_json(path)
        self.assertIsNone(mod.NoRedirect().redirect_request(None,None,None,None,None,None))

    def test_only_token_exchange_and_accounts_and_no_secret_in_report(self):
        calls = []
        def request(path, **kwargs):
            calls.append((path,kwargs))
            if path == '/auth/token':
                return {'access_token':'synthetic-token','refresh_token':'synthetic-refresh',
                        'token_type':'bearer','scope':'READ'}
            return [{'id':'private-id','currency':'GBP','balance':42}]
        with patch.object(mod, 'ROOT', self.root):
            report = mod.connect(self.setup, 'synthetic-code', returned_state=True, request=request)
        self.assertEqual([x[0] for x in calls], ['/auth/token','/accounts'])
        self.assertEqual(report['account_count'], 1)
        self.assertFalse(report['core_integrated'])
        serialized = json.dumps(report)
        for secret in ('synthetic-token','synthetic-refresh','private-id','balance','synthetic-code'):
            self.assertNotIn(secret, serialized)
        self.assertEqual(json.loads((self.root/'oauth-tokens.local.json').read_text())['client_id'], 'synthetic-client')

    def test_scope_escalation_is_not_saved_or_used(self):
        calls = []
        def request(path, **kwargs):
            calls.append(path)
            return {'access_token':'token','token_type':'bearer','scope':'READ,PAY'}
        with self.assertRaises(ValueError):
            mod.connect(self.setup, 'code', returned_state=False, request=request)
        self.assertEqual(calls, ['/auth/token'])
        self.assertFalse((self.root/'oauth-tokens.local.json').exists())

    def test_tls_certificate_is_for_localhost_and_not_a_ca(self):
        cert_path, key_path = mod.tls_files(self.setup)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        self.assertEqual(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                         .value.get_values_for_type(x509.DNSName), ['trusta.localhost'])
        self.assertFalse(cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
        self.assertNotEqual(key_path.read_bytes(), (self.root/'key.pem').read_bytes())

    def test_http_rejects_foreign_browser_host_and_post_origin(self):
        flow = mod.Flow(self.setup)
        server = ThreadingHTTPServer(('127.0.0.1',0), mod.handler_for(flow))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
        self.addCleanup(conn.close)
        conn.request('GET', '/start', headers={'Host':'evil.invalid'})
        response = conn.getresponse(); response.read()
        self.assertEqual(response.status, 400)
        conn.request('GET', '/revolut/callback?code=synthetic-secret', headers={'Host':'trusta.localhost'})
        response = conn.getresponse(); body = response.read()
        self.assertEqual(response.status, 400)
        self.assertNotIn(b'synthetic-secret', body)
        self.assertIsNone(flow.code)
        conn.request('POST','/exchange',body='csrf='+flow.csrf,headers={'Host':'trusta.localhost',
            'Cookie':'__Host-trusta-oauth='+flow.cookie,'Origin':'https://evil.invalid'})
        response = conn.getresponse(); response.read()
        self.assertEqual(response.status, 403)
        self.assertFalse(flow.used)

    def test_confirmation_form_keeps_origin_and_accepts_only_explicit_same_origin_post(self):
        flow = mod.Flow(self.setup)
        server = ThreadingHTTPServer(('127.0.0.1',0), mod.handler_for(flow))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        self.addCleanup(conn.close)
        conn.request('GET','/start',headers={'Host':'trusta.localhost'})
        response = conn.getresponse(); response.read()
        self.assertEqual(response.getheader('Referrer-Policy'), 'same-origin')
        self.assertIn('Secure; HttpOnly; SameSite=Lax', response.getheader('Set-Cookie'))
        headers = {'Host':'trusta.localhost','Cookie':'__Host-trusta-oauth='+flow.cookie}
        conn.request('GET', '/revolut/callback?code=synthetic-code&state='+flow.state, headers=headers)
        response = conn.getresponse(); body = response.read()
        self.assertEqual(response.status,303)
        self.assertEqual(response.getheader('Location'),'/confirm')
        self.assertEqual(response.getheader('Referrer-Policy'),'no-referrer')
        self.assertNotIn(b'synthetic-code',body)
        conn.request('GET','/confirm',headers=headers)
        response = conn.getresponse(); body = response.read()
        self.assertEqual(response.status,200)
        self.assertEqual(response.getheader('Referrer-Policy'),'same-origin')
        self.assertNotIn(b'synthetic-code',body)
        output = io.StringIO()
        with patch.object(mod,'connect',return_value={'account_count':1}) as exchange, patch('sys.stdout',output):
            # Previous no-referrer form behavior is still rejected, never silently allowed.
            conn.request('POST','/exchange',body='csrf='+flow.csrf,
                         headers=dict(headers,Origin='null'))
            response = conn.getresponse(); response.read()
            self.assertEqual(response.status,403)
            self.assertFalse(flow.used)
            exchange.assert_not_called()
            conn.request('POST','/exchange',body='csrf='+flow.csrf,
                         headers=dict(headers,Origin=mod.ORIGIN))
            response = conn.getresponse(); body = response.read()
            self.assertEqual(response.status,200)
            exchange.assert_called_once_with(self.setup,'synthetic-code',returned_state=True)
            self.assertTrue(flow.used)
            self.assertIsNone(flow.code)
        for secret in (flow.cookie,flow.csrf,flow.state,'synthetic-code'):
            self.assertNotIn(secret,output.getvalue())
        self.assertIn('ORIGIN_NULL',output.getvalue())


if __name__ == '__main__':
    unittest.main()
