"""Deliberate model fixture; the bank and audit are NEVER mocked."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from backend.llm.configuration import ProviderConfig
from llm_evaluation.adapters.compatible_adapter import CompatibleAdapter


class MockAdapter(CompatibleAdapter):
    def __init__(self, output, trace_path, *, contract_version='2.0.0'):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
                body = json.dumps({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
                                   'model': 'explicit-offline-fixture'}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        super().__init__(ProviderConfig('offline_fixture', kind='openai_compatible', model='explicit-offline-fixture',
            endpoint=f'http://127.0.0.1:{self.server.server_port}/', expected_latency_ms=1, cost_per_1k_tokens_usd=0), trace_path,
            contract_version=contract_version)

    def close(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
