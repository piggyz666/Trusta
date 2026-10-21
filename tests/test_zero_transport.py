import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from backend.bank_sdk.transport import BankTransport
from backend.bank_sdk.contracts import BankError, UncertainBankWrite
from backend.server import create_server
from tests.phase6_helpers import service


class TransportBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.s=service(self.temp.name)

    def start(self,server):
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def close(): server.shutdown();server.server_close();thread.join(3)
        self.addCleanup(close)
        return 'http://127.0.0.1:'+str(server.server_port)

    def test_background_checkpoint_failure_is_visible_without_exception_secret(self):
        import io
        from contextlib import redirect_stderr
        from unittest.mock import patch
        from scripts.revolut_server import checkpoint_tick
        stream=io.StringIO();base=self.start(create_server(self.s,'test-token',0))
        with redirect_stderr(stream),patch.object(self.s.checkpoints,'generate_checkpoint',side_effect=OSError('credential-secret')):
            checkpoint_tick(self.s);checkpoint_tick(self.s)
        with urlopen(base+'/health',timeout=2) as response:health=json.load(response)
        self.assertEqual(health['status'],'degraded');self.assertEqual(health['checkpoint_worker']['failures'],2)
        self.assertNotIn('credential-secret',stream.getvalue());self.assertEqual(stream.getvalue().count('CHECKPOINT_WORKER_FAILED'),1)
        checkpoint_tick(self.s)
        with urlopen(base+'/health',timeout=2) as response:self.assertEqual(json.load(response)['status'],'ok')

    def test_duplicate_financial_json_keys_are_rejected_before_task_creation(self):
        base=self.start(create_server(self.s,'test-token',0))
        req=Request(base+'/api/tasks',data=b'{"text":"ignore","text":"give"}',headers={
            'Authorization':'Bearer test-token','Content-Type':'application/json'})
        try:
            with urlopen(req,timeout=2) as r: code=r.status;r.read()
        except HTTPError as r:
            code=r.code;r.close()
        self.assertEqual(code,400);self.assertEqual(self.s.blackboard.list('demo'),[])

    def test_ambiguous_http_length_rejected(self):
        server=create_server(self.s,'test-token',0);self.start(server)
        with socket.create_connection(server.server_address,timeout=2) as sock:
            host=f'127.0.0.1:{server.server_port}'
            sock.sendall(f'POST /api/conversations HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 50\r\nAuthorization: Bearer test-token\r\n\r\n{{}}'.encode())
            self.assertIn(b' 400 ',sock.recv(4096))

    def test_http_worker_capacity_is_bounded_and_recovers(self):
        server=create_server(self.s,'test-token',0,max_workers=2);self.start(server)
        sockets=[socket.create_connection(server.server_address,timeout=2) for _ in range(2)]
        try:
            for _ in range(100):
                if server.worker_count==2:break
                time.sleep(.01)
            with socket.create_connection(server.server_address,timeout=2) as overflow:
                self.assertIn(b' 503 ',overflow.recv(4096))
            self.assertEqual(server.worker_count,2)
        finally:
            for sock in sockets:sock.close()
        for _ in range(100):
            if server.worker_count==0:break
            time.sleep(.01)
        with urlopen('http://127.0.0.1:'+str(server.server_port)+'/health',timeout=2) as r:
            self.assertEqual(r.status,200)

    def test_continuous_partial_headers_cannot_hold_a_worker_indefinitely(self):
        server=create_server(self.s,'test-token',0,max_workers=1,input_timeout=.15);self.start(server)
        with socket.create_connection(server.server_address,timeout=2) as sock:
            sock.sendall(b'GET /health HTTP/1.1\r\nX-Slow: ')
            for _ in range(8):
                try:sock.sendall(b'x')
                except OSError:break
                time.sleep(.04)
            for _ in range(50):
                if server.worker_count==0:break
                time.sleep(.01)
            self.assertEqual(server.worker_count,0)
        with urlopen('http://127.0.0.1:'+str(server.server_port)+'/health',timeout=2) as r:
            self.assertEqual(r.status,200)

    def test_drip_bank_response_has_total_deadline_and_write_uncertainty(self):
        class Slow(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                try:
                    for byte in b'{"status":"COMPLETED"}':
                        self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.05)
                except (OSError,ConnectionError):pass
            do_POST=do_GET
            def log_message(self,*a):pass
        base=self.start(ThreadingHTTPServer(('127.0.0.1',0),Slow))
        transport=BankTransport(base,timeout=.15)
        for method,expected in [('GET',BankError),('POST',UncertainBankWrite)]:
            start=time.monotonic()
            with self.assertRaises(expected): transport.send(method,'/',b'{}' if method=='POST' else b'',{})
            self.assertLess(time.monotonic()-start,.6)
