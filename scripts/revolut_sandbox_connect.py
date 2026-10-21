"""Local, user-confirmed OAuth bootstrap. Does not implement a banking adapter."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
from backend.security.local_files import protect_secret, write_secret_atomic
from backend.network import read_bounded
from backend.llm.validation import strict_json
CONFIG = ROOT / 'config/revolut-sandbox/setup.local.json'
ORIGIN = 'https://trusta.localhost'
CALLBACK = ORIGIN + '/revolut/callback'
BANK = 'https://sandbox-b2b.revolut.com/api/1.0'


def load_setup(path=CONFIG):
    setup = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if setup.get('scope') != 'REVOLUT_SANDBOX_ONLY' or setup.get('oauth_redirect_uri') != CALLBACK:
        raise ValueError('sandbox callback configuration mismatch')
    client = setup.get('client_id', '')
    if not client or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in client):
        raise ValueError('invalid client ID')
    return setup


def requested_scope(setup):
    value=setup.get('oauth_permissions','READ')
    if value=='READ,WRITE' and setup.get('operator_simulation_only') is True:return value
    if value not in {'READ','READ,PAY'}: raise ValueError('unsupported sandbox permissions')
    return value


def credential_profile(setup):
    scope=requested_scope(setup)
    return {'READ':('oauth-tokens.local.json','revolut-sandbox-connect-result.json'),
            'READ,PAY':('oauth-payment-tokens.local.json','revolut-sandbox-payment-connect-result.json'),
            'READ,WRITE':('oauth-simulation-tokens.local.json','revolut-sandbox-simulation-connect-result.json')}[scope]


def authorization_url(setup, state):
    return 'https://sandbox-business.revolut.com/app-confirm?' + urlencode({
        'client_id': setup['client_id'], 'redirect_uri': CALLBACK,
        'response_type': 'code', 'scope': requested_scope(setup), 'state': state})


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def assertion(setup, now=None):
    protect_secret(setup['private_key_path'])
    key = serialization.load_pem_private_key(Path(setup['private_key_path']).read_bytes(), None)
    cert = x509.load_pem_x509_certificate(Path(setup['public_certificate_path']).read_bytes())
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError('RSA-2048 or stronger signing key required')
    if key.public_key().public_numbers() != cert.public_key().public_numbers():
        raise ValueError('registered certificate and signing key mismatch')
    if cert.not_valid_after_utc <= datetime.now(timezone.utc):
        raise ValueError('registered signing certificate expired')
    claims = {'iss': 'trusta.localhost', 'sub': setup['client_id'],
              'aud': 'https://revolut.com', 'exp': int(time.time() if now is None else now) + 300}
    packed = '.'.join(b64(json.dumps(item, separators=(',', ':')).encode())
                      for item in ({'alg': 'RS256', 'typ': 'JWT'}, claims))
    signature = key.sign(packed.encode(), padding.PKCS1v15(), hashes.SHA256())
    cert.public_key().verify(signature, packed.encode(), padding.PKCS1v15(), hashes.SHA256())
    return packed + '.' + b64(signature)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Never forward credentials to another endpoint.


def bank_json(path, *, form=None, access_token=None):
    if (path, form is not None) not in {('/auth/token', True), ('/accounts', False)}:
        raise ValueError('endpoint outside sandbox bootstrap allowlist')
    headers = {'Accept': 'application/json'}
    data = None
    if form is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        data = urlencode(form).encode()
    if access_token:
        headers['Authorization'] = 'Bearer ' + access_token
    request = Request(BANK + path, data=data, headers=headers, method='POST' if form is not None else 'GET')
    deadline=time.monotonic()+25
    try:
        with build_opener(NoRedirect()).open(request, timeout=25) as response:
            raw=read_bounded(response,2_000_000,deadline)
            return strict_json(raw.decode('utf-8'))
    except HTTPError as exc:
        raise ValueError('bank HTTP status ' + str(exc.code)) from None
    except (URLError, TimeoutError):
        raise ValueError('bank network request failed') from None


def connect(setup, code, *, returned_state, request=bank_json):
    scope=requested_scope(setup)
    tokens = request('/auth/token', form={
        'grant_type': 'authorization_code', 'code': code,
        'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
        'client_assertion': assertion(setup)})
    if not isinstance(tokens, dict) or not isinstance(tokens.get('access_token'), str) or not tokens['access_token']:
        raise ValueError('invalid token response')
    if tokens.get('token_type', '').lower() != 'bearer':
        raise ValueError('unexpected token type')
    if 'scope' in tokens and (not isinstance(tokens['scope'], str) or
                             set(tokens['scope'].replace(',', ' ').split()) != set(scope.split(','))):
        raise ValueError('returned token scope differs from requested scope')
    # Store secrets only beneath the existing private-key directory (restricted NTFS ACL).
    credential_name,report_name=credential_profile(setup)
    secret_path = Path(setup['private_key_path']).parent / credential_name
    saved = dict(tokens, client_id=setup['client_id'], issuer='trusta.localhost',
                 sandbox_only=True, scope_requested=scope,obtained_at=datetime.now(timezone.utc).isoformat())
    if scope=='READ,WRITE':saved['operator_simulation_only']=True
    protect_secret(secret_path.parent)
    write_secret_atomic(secret_path,json.dumps(saved,indent=2).encode())
    accounts = request('/accounts', access_token=tokens['access_token'])
    if not isinstance(accounts, list) or any(not isinstance(a, dict) for a in accounts):
        raise ValueError('unexpected accounts response')
    # Report counts and currencies, never identifiers, balances, or tokens.
    report = {'sandbox_only': True, 'status': 'ACCOUNT_READ_VERIFIED',
              'verified_at': datetime.now(timezone.utc).isoformat(),
              'scope_requested': scope, 'scope_returned': tokens.get('scope'),
              'oauth_state_echoed': returned_state, 'local_confirmation_required': True,
              'account_count': len(accounts), 'currencies': sorted({a['currency'] for a in accounts
                  if isinstance(a.get('currency'), str)}), 'bank_write_calls': 0,
              'core_integrated': False}
    if scope=='READ,WRITE':
        report.update(operator_simulation_only=True,simulation_permission_verified=False,
                      note='Account read does not verify simulator permission; no simulation performed.')
    output = ROOT / 'data' / report_name
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def tls_files(setup):
    directory = Path(setup['private_key_path']).parent
    protect_secret(directory)
    cert_path, key_path = directory / 'callback-tls.cer', directory / 'callback-tls.pem'
    if cert_path.exists() and key_path.exists():
        protect_secret(key_path)
        return cert_path, key_path
    if cert_path.exists() or key_path.exists():
        raise ValueError('partial callback TLS keypair; inspect files before retrying')
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'trusta.localhost')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('trusta.localhost')]), False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                key_encipherment=True, data_encipherment=False, key_agreement=False,
                key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .sign(key, hashes.SHA256()))
    write_secret_atomic(key_path,key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()),create_only=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


class Flow:
    def __init__(self, setup):
        self.setup = setup
        self.cookie, self.csrf, self.state = (secrets.token_urlsafe(32) for _ in range(3))
        self.code = None
        self.code_received_at = 0.0
        self.returned_state = False
        self.used = False
        self.started_at = time.monotonic()
        self.lock = threading.Lock()

    def accept(self, query):
        with self.lock:
            self._accept(query)

    def _accept(self, query):
        if self.used or self.code is not None or time.monotonic() - self.started_at > 1200:
            raise ValueError('flow is expired or already has a code')
        if 'error' in query:
            raise ValueError('authorization declined or failed')
        values = query.get('code', [])
        if len(values) != 1 or not values[0] or len(values[0]) > 4096:
            raise ValueError('invalid authorization code')
        state = query.get('state')
        if state is not None and state != [self.state]:
            raise ValueError('OAuth state mismatch')
        # The guide does not document state echo. If omitted, never auto-exchange:
        # a prepared-browser cookie plus explicit local POST confirmation is required.
        self.returned_state = state is not None
        self.code, self.code_received_at = values[0], time.monotonic()

    def consume(self, csrf):
        with self.lock:
            return self._consume(csrf)

    def _consume(self, csrf):
        if csrf != self.csrf or self.used or not self.code:
            raise ValueError('confirmation is invalid or already used')
        if time.monotonic() - self.code_received_at > 110:
            raise ValueError('code expired; restart authorization')
        self.used = True
        code, self.code = self.code, None
        return code


def handler_for(flow):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(10)
            super().setup()

        def log_message(self, *args):
            pass  # Callback queries contain authorization codes.

        def page(self,body,status=200,*,cookie=False):
            try:self._page(body,status,cookie=cookie)
            except OSError:
                # A browser disconnect does not reverse a completed bank exchange.
                print(json.dumps({'status':'LOCAL_RESPONSE_DISCONNECTED','bank_write_calls':0}),flush=True)

        def _page(self, body, status=200, *, cookie=False):
            raw = ('<!doctype html><meta charset="utf-8"><title>TRUSTA Sandbox</title>'
                   '<style>body{font:18px sans-serif;max-width:750px;margin:70px auto;padding:20px;}'
                   'button,a{font-size:18px;}p{line-height:1.7}</style><h1>TRUSTA Sandbox</h1>' + body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            # HTML form POST under no-referrer sends Origin: null. Keep the
            # same-origin source for CSRF validation, and suppress it externally.
            self.send_header('Referrer-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
            self.send_header('X-Content-Type-Options', 'nosniff')
            if cookie:
                self.send_header('Set-Cookie', '__Host-trusta-oauth=' + flow.cookie + '; Path=/; Secure; HttpOnly; SameSite=Lax')
            self.end_headers()
            self.wfile.write(raw)

        def browser_matches(self):
            try:
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
                item = cookies.get('__Host-trusta-oauth')
                return item is not None and secrets.compare_digest(item.value, flow.cookie)
            except Exception:
                return False

        def host_matches(self):
            return self.headers.get('Host', '').lower() in {'trusta.localhost', 'trusta.localhost:443'}

        def do_GET(self):
            if not self.host_matches():
                self.page('<p>Invalid local host.</p>', 400)
                return
            parsed = urlsplit(self.path)
            if parsed.path == '/start' and not parsed.query:
                from html import escape
                scope=requested_scope(flow.setup)
                message='此次申请账户读取和沙箱付款权限；不申请管理账户或敏感卡信息。尚不执行付款。' if scope=='READ,PAY' else '此次仅申请账户读取权限。'
                permissions='Read your account details'+(' 和 Make payments' if scope=='READ,PAY' else '')
                if scope=='READ,WRITE':
                    message=('此次申请 READ,WRITE，单独保存为操作员测试凭据。WRITE 是银行管理权限，'
                             '可能允许管理收款人等操作；本工具只验证账户读取，不修改银行数据。'
                             '它能否解除模拟结算的 403 仍待验证；不向 Agent 提供这份凭据。'
                             '不申请 PAY 或敏感卡信息。同一证书下的授权是否影响原令牌，将另行核实。')
                    permissions='Read your account details 和 Manage your account details；关闭 Make payments 与 Read sensitive card details'
                self.page('<p>本机接收服务已就绪。'+message+'</p>'
                          '<p>请在授权页核对：只开启 '+permissions+'。</p>'
                          '<p><a href="' + escape(authorization_url(flow.setup, flow.state), quote=True) + '">前往 Revolut Sandbox 授权</a></p>', cookie=True)
            elif parsed.path == '/revolut/callback' and self.browser_matches():
                try:
                    flow.accept(parse_qs(parsed.query, keep_blank_values=True))
                    self.send_response(303)
                    self.send_header('Location', '/confirm')
                    self.send_header('Referrer-Policy', 'no-referrer')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                except ValueError:
                    self.page('<p>授权回调无效。请从本机入口重新发起授权。</p>', 400)
            elif parsed.path == '/confirm' and self.browser_matches() and flow.code:
                self.page('<p>已收到授权码，尚未换取 token。若这是你刚刚发起的授权，请立即确认。</p>'
                          '<form method="post" action="/exchange"><input type="hidden" name="csrf" value="'
                          + flow.csrf + '"><button>确认连接并验证账户读取</button></form>')
            else:
                self.page('<p>请从 /start 进入，并在同一浏览器完成授权。</p>', 400)

        def do_POST(self):
            # Consume only bounded bytes before returning a rejection. Closing a
            # Windows socket with unread POST bytes can discard the 403 response.
            # Parsing/flow.consume/bank requests still require all checks below.
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size < 1024:
                    raise ValueError('invalid confirmation length')
                self.connection.settimeout(10)
                raw = self.rfile.read(size)
                if len(raw)!=size: raise ValueError('incomplete confirmation')
            except (ValueError, OSError):
                self.page('<p>Invalid local confirmation.</p>', 400)
                return
            reason = None
            if not self.host_matches():
                reason = 'HOST_MISMATCH'
            elif self.path != '/exchange':
                reason = 'PATH_MISMATCH'
            elif not self.browser_matches():
                reason = 'BROWSER_SESSION_MISMATCH'
            elif self.headers.get('Origin') != ORIGIN:
                reason = 'ORIGIN_NULL' if self.headers.get('Origin') == 'null' else 'ORIGIN_MISMATCH'
            if reason:
                # Fixed labels only: never log raw headers or browser credentials.
                print(json.dumps({'status':'LOCAL_CONFIRMATION_REJECTED', 'reason':reason,
                                  'bank_requests_made':False}), flush=True)
                self.page('<p>Invalid local confirmation.</p>', 403)
                return
            try:
                params = parse_qs(raw.decode(), keep_blank_values=True)
                if len(params.get('csrf', [])) != 1:
                    raise ValueError('invalid confirmation')
                code = flow.consume(params['csrf'][0])
                result = connect(flow.setup, code, returned_state=flow.returned_state)
                print(json.dumps(result), flush=True)
                self.page('<p>沙箱账户读取验证成功。授权码和 token 未显示在页面中。</p>'
                          '<p>账户数量：' + str(result['account_count']) + '。没有执行银行资金操作。</p>')
                threading.Timer(1, self.server.shutdown).start()
            except Exception as exc:
                # Never render bank responses, codes, JWTs, or exception details.
                status = {'status': 'SANDBOX_CONNECT_FAILED', 'bank_write_calls': 0}
                if isinstance(exc, ValueError) and str(exc).startswith('bank HTTP status '):
                    suffix = str(exc)[len('bank HTTP status '):]
                    if suffix.isdigit() and len(suffix) == 3:
                        status['http_status'] = int(suffix)
                print(json.dumps(status), flush=True)
                self.page('<p>未完成账户读取验证。请告知此页面结果；不要发送 token 或授权码。</p>', 400)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    permissions=parser.add_mutually_exclusive_group()
    permissions.add_argument('--payments',action='store_true',help='Request READ,PAY; store separate credentials. Does not make a payment.')
    permissions.add_argument('--simulation-control',action='store_true',help='Operator-only READ,WRITE trial; separate credentials, no PAY, no simulation or financial write.')
    args = parser.parse_args()
    setup = load_setup()
    if args.payments: setup=dict(setup,oauth_permissions='READ,PAY')
    if args.simulation_control:setup=dict(setup,oauth_permissions='READ,WRITE',operator_simulation_only=True)
    assertion(setup)  # Validate the registered keypair without printing JWT.
    cert, key = tls_files(setup)
    if args.prepare:
        print('LOCAL_KEYPAIR_AND_TLS_PREPARED; no bank request made.')
        return
    server = ThreadingHTTPServer(('127.0.0.1', 443), handler_for(Flow(setup)))
    server.timeout = 5
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    timer = threading.Timer(1200, server.shutdown)
    timer.daemon = True
    timer.start()
    print('LOCAL_HTTPS_READY https://trusta.localhost/start; expires in 20 minutes.', flush=True)
    try:
        server.serve_forever()
    finally:
        timer.cancel()
        server.server_close()


if __name__ == '__main__':
    main()
