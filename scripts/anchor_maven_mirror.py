"""Temporary development download relay for WSL; ONLY public Maven artifacts.

No application configuration or credential is accepted. Not a WeID service.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
from pathlib import Path
import re
import requests

ROOT = Path('data/anchor-integration/toolchain/maven-cache').resolve()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if (not re.fullmatch(r'/[A-Za-z0-9_./+%-]+', self.path)
                or '..' in self.path or '%' in self.path
                or not self.path.endswith(('.pom', '.jar', '.xml', '.sha1', '.md5', '.sha256', '.sha512'))):
            self.send_error(400)
            return
        path = ROOT / self.path.lstrip('/')
        if not path.is_relative_to(ROOT):
            self.send_error(400)
            return
        try:
            if not path.exists():
                response = requests.get('https://repo.maven.apache.org/maven2' + self.path, timeout=45)
                if response.status_code != 200:
                    self.send_error(response.status_code)
                    return
                if len(response.content) > 100_000_000:
                    self.send_error(413)
                    return
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + '.' + hashlib.sha256(response.content).hexdigest() + '.part')
                temporary.write_bytes(response.content)
                temporary.replace(path)
            data = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (OSError, requests.RequestException):
            self.send_error(502)


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 18890), Handler).serve_forever()
