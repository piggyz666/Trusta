import json
import socket
import time
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from backend.bank_sdk.contracts import BankError, UncertainBankWrite
from backend.llm.validation import strict_json
from backend.network import read_bounded

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None

class BankTransport:
    def __init__(self, base_url, timeout=10, response_decoder=None):
        url = urlsplit(base_url)
        if (not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {'','/'}
                or url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'127.0.0.1','localhost'})
                or not 0 < timeout <= 30):
            raise BankError('INVALID_BANK_ENDPOINT')
        self.base_url, self.timeout = base_url.rstrip('/'), timeout
        self.response_decoder = response_decoder or strict_json

    def send(self, method, path, body, headers):
        if not path.startswith('/') or path.startswith('//') or any(c in path for c in ('\\','\r','\n')):
            raise BankError('INVALID_BANK_PATH')
        write = method not in {'GET','HEAD'}
        deadline=time.monotonic()+self.timeout
        try:
            req = Request(self.base_url+path, data=body if body else None, headers=headers, method=method)
            with build_opener(ProxyHandler({}), NoRedirect()).open(req,timeout=self.timeout) as response:
                if response.headers.get_content_type() != 'application/json':
                    raise ValueError()
                data = read_bounded(response,1048576,deadline)
                return self.response_decoder(data.decode())
        except HTTPError as exc:
            status, retry = exc.code, exc.headers.get('Retry-After')
            exc.close()
            if write and status >= 500:
                raise UncertainBankWrite() from None
            code = {401:'BANK_CREDENTIAL_EXPIRED',403:'BANK_SCOPE_DENIED',404:'BANK_RESOURCE_NOT_FOUND',409:'BANK_CONFLICT',429:'BANK_RATE_LIMIT'}.get(status,'BANK_HTTP_ERROR')
            raise BankError(code,status,retry) from None
        except BankError:
            raise
        except Exception:
            if write:
                raise UncertainBankWrite() from None
            raise BankError('BANK_READ_UNAVAILABLE') from None
