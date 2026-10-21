import os
import socket
import time
from http.client import RemoteDisconnected
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from urllib.parse import urlsplit
from backend.domain import canonical
from backend.llm.contracts import InferenceError, ProviderResponse
from backend.llm.validation import strict_json
from backend.network import read_bounded, ResponseTooLarge


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPAdapter:
    """Bounded declarative adapter. Other native protocols implement Provider explicitly."""
    MAX_RESPONSE_BYTES = 131072

    def __init__(self, config):
        config.validate()
        self.config = config

    def describe_capabilities(self):
        return {"protocol": "mapped_http_json", "structured_outputs": False, "streaming": False}

    def health(self):
        # A readiness description must not incur a surprise inference/network call.
        return {"status": "configured", "network_checked": False}

    def body(self, request):
        return {self.config.model_field: self.config.model,
                self.config.messages_field: list(request.messages), "max_tokens": request.max_output_tokens}

    def parse(self, payload):
        try:
            value = payload
            for key in self.config.response_path:
                value = value[key]
            if not isinstance(value, str):
                raise ValueError()
            return ProviderResponse(value)
        except (KeyError, TypeError, ValueError):
            raise InferenceError("LLM_PROVIDER_PROTOCOL") from None

    def infer(self, request):
        headers = self.auth_headers()
        outbound = Request(self.config.endpoint, data=canonical(self.body(request)).encode(), headers=headers, method="POST")
        return self._send(outbound,request)

    def auth_headers(self):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.api_key_env:
            secret = os.environ.get(self.config.api_key_env, "")
            if not secret or any(c in secret for c in "\r\n"):
                raise InferenceError("LLM_PROVIDER_CREDENTIALS")
            headers["Authorization"] = "Bearer " + secret
        return headers

    def _send(self, outbound, request):
        deadline = time.monotonic() + request.timeout_seconds
        proxy={}
        if self.config.proxy_env:
            configured=os.getenv(self.config.proxy_env,'')
            url=urlsplit(configured)
            if not url.hostname or url.username or url.password or url.scheme not in {'http','https'} or url.query or url.fragment:
                raise InferenceError('LLM_INVALID_CONFIG')
            proxy={'https':configured,'http':configured}
        try:
            # No implicit redirects or environment proxy: a configured destination is the boundary.
            with build_opener(ProxyHandler(proxy), NoRedirect()).open(outbound, timeout=request.timeout_seconds) as response:
                if response.headers.get_content_type() != "application/json":
                    raise InferenceError("LLM_PROVIDER_PROTOCOL")
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        if not 0 <= int(length) <= self.MAX_RESPONSE_BYTES:
                            raise InferenceError("LLM_RESPONSE_TOO_LARGE")
                    except ValueError:
                        raise InferenceError("LLM_PROVIDER_PROTOCOL") from None
                data=read_bounded(response,self.MAX_RESPONSE_BYTES,deadline)
                return self.parse(strict_json(data.decode("utf-8")))
        except ResponseTooLarge:
            raise InferenceError('LLM_RESPONSE_TOO_LARGE') from None
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code in {408, 429, 502, 503, 504}:
                raise InferenceError("LLM_PROVIDER_UNAVAILABLE", retryable=True) from None
            raise InferenceError("LLM_PROVIDER_HTTP_ERROR") from None
        except (TimeoutError, socket.timeout):
            raise InferenceError("LLM_TIMEOUT", retryable=True) from None
        except URLError:
            raise InferenceError("LLM_PROVIDER_UNAVAILABLE", retryable=True) from None
        except (RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            # A disconnected transport is different from invalid model/schema
            # content. It is eligible only for the gateway's bounded retry.
            raise InferenceError("LLM_PROVIDER_UNAVAILABLE", retryable=True) from None
        except (UnicodeError, OSError, ValueError):
            raise InferenceError("LLM_PROVIDER_PROTOCOL") from None
