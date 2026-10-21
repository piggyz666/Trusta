from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest
from unittest.mock import patch
from backend.llm.configuration import ProviderConfig
from backend.llm.contracts import InferenceError, ProviderRequest
from backend.llm.projection import OUTPUT_SCHEMAS
from backend.llm.providers.http_adapter import HTTPAdapter
from backend.llm.providers.openai_compatible import OpenAICompatibleProvider


class ProviderProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.received.append((self.path, body, self.headers.get("Authorization")))
                output = '{"task_type":"TRANSFER","needs_clarification":false}'
                reply = {"choices": [{"message": {"content": output}, "finish_reason": "stop"}],
                         "model": "fixture-model", "usage": {"prompt_tokens": 20, "completion_tokens": 10}}
                if self.path == "/native":
                    reply = {"result": {"reply": output}}
                elif self.path == "/refusal":
                    reply["choices"][0]["message"]["refusal"] = "rejected"
                elif self.path == "/tool":
                    reply["choices"][0]["message"]["tool_calls"] = [{"function": {"name": "transfer.create"}}]
                elif self.path == "/truncated":
                    reply["choices"][0]["finish_reason"] = "length"
                elif self.path == "/bad":
                    reply = {"unknown": True}
                elif self.path == "/slow":
                    time.sleep(0.15)
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/sink")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/rate":
                    self.send_response(429)
                else:
                    self.send_response(200)
                data = json.dumps(reply).encode()
                if self.path == "/large":
                    data = b" " * (HTTPAdapter.MAX_RESPONSE_BYTES + 1)
                self.send_header("Content-Type", "text/html" if self.path == "/html" else "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.server.received = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(3)
        cls.server.server_close()

    def setUp(self):
        self.server.received.clear()
        self.request = ProviderRequest(({"role": "user", "content": "fictional fixture"},),
                                       OUTPUT_SCHEMAS["intent_normalization"], 100, 1)

    def config(self, path, **changes):
        return ProviderConfig("local", kind="openai_compatible", model="configured-model",
                              endpoint=f"http://127.0.0.1:{self.server.server_port}{path}", **changes)

    def test_openai_protocol_and_opt_in_structured_output(self):
        adapter = OpenAICompatibleProvider(self.config("/openai", structured_outputs=True, api_key_env="TRUSTA_TEST_LLM_KEY"))
        with patch.dict("os.environ", {"TRUSTA_TEST_LLM_KEY": "test-secret"}):
            response = adapter.infer(self.request)
        path, body, authorization = self.server.received[0]
        self.assertEqual(body["model"], "configured-model")
        self.assertFalse(body["stream"])
        self.assertEqual(body["response_format"]["json_schema"]["schema"], self.request.output_schema)
        self.assertEqual(authorization, "Bearer test-secret")
        self.assertEqual(response.model, "fixture-model")
        self.assertEqual(response.usage, {"input_tokens": 20, "output_tokens": 10})

    def test_text_only_compatible_provider_does_not_require_native_schema(self):
        OpenAICompatibleProvider(self.config("/openai")).infer(self.request)
        self.assertNotIn("response_format", self.server.received[0][1])

    def test_native_http_mapping_is_independent_of_openai(self):
        config = replace(self.config("/native"), kind="http", model_field="deployment", messages_field="input",
                         response_path=("result", "reply"))
        response = HTTPAdapter(config).infer(self.request)
        self.assertEqual(json.loads(response.text)["task_type"], "TRANSFER")
        body = self.server.received[0][1]
        self.assertEqual(body["deployment"], "configured-model")
        self.assertEqual(body["input"], list(self.request.messages))
        self.assertNotIn("model", body)

    def test_refusal_tools_and_truncated_response_are_rejected(self):
        for path, code in (("/refusal", "LLM_REFUSED"), ("/tool", "LLM_TOOL_CALL_FORBIDDEN"),
                           ("/truncated", "LLM_INCOMPLETE_OUTPUT"), ("/bad", "LLM_PROVIDER_PROTOCOL")):
            with self.subTest(path=path), self.assertRaises(InferenceError) as caught:
                OpenAICompatibleProvider(self.config(path)).infer(self.request)
            self.assertEqual(caught.exception.code, code)

    def test_redirect_is_not_followed_and_credentials_stay_at_destination(self):
        with patch.dict("os.environ", {"TRUSTA_TEST_LLM_KEY": "test-secret"}), self.assertRaises(InferenceError):
            OpenAICompatibleProvider(self.config("/redirect", api_key_env="TRUSTA_TEST_LLM_KEY")).infer(self.request)
        self.assertEqual([path for path, _, _ in self.server.received], ["/redirect"])

    def test_http_rate_limit_is_retryable_but_bad_content_is_not(self):
        for path, retryable in (("/rate", True), ("/html", False), ("/large", False)):
            with self.subTest(path=path), self.assertRaises(InferenceError) as caught:
                OpenAICompatibleProvider(self.config(path)).infer(self.request)
            self.assertEqual(caught.exception.retryable, retryable)

    def test_transport_timeout(self):
        with self.assertRaises(InferenceError) as caught:
            OpenAICompatibleProvider(self.config("/slow")).infer(replace(self.request, timeout_seconds=0.03))
        self.assertEqual(caught.exception.code, "LLM_TIMEOUT")

    def test_missing_credentials_and_health_never_send_requests(self):
        adapter = OpenAICompatibleProvider(self.config("/openai", api_key_env="TRUSTA_MISSING_TEST_KEY"))
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(InferenceError):
            adapter.infer(self.request)
        self.assertFalse(adapter.health()["network_checked"])
        self.assertEqual(self.server.received, [])
