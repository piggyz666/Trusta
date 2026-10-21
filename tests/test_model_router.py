from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from backend.llm.configuration import LLMSettings, ProviderConfig
from backend.llm.contracts import InferenceContext, InferenceError, InferenceRequest, ProviderRequest
from backend.llm.router import ModelRouter


class ModelRouterTests(unittest.TestCase):
    def setUp(self):
        self.request = InferenceRequest("req", InferenceContext("task", "owner", "supervisor", "TRANSFER", 0),
                                        "intent_normalization", {"text": "请求"})
        self.outbound = ProviderRequest(({"role": "user", "content": "请求"},), {}, 100, 1)

    def route(self, providers, request=None, **settings):
        return ModelRouter(LLMSettings(providers=tuple(providers), **settings).validate()).select(
            request or self.request, self.outbound)

    def test_sensitive_data_never_routes_to_public_provider(self):
        route = self.route([ProviderConfig("public"), ProviderConfig("private", max_sensitivity="restricted")])
        self.assertEqual([p.name for p in route.providers], ["private"])
        self.assertEqual(route.rejected, {"public": "DATA_SENSITIVITY"})

    def test_hard_security_complexity_context_and_latency_filters(self):
        common = {"max_sensitivity": "restricted", "security_level": "enhanced", "max_complexity": "complex"}
        providers = [ProviderConfig("security", **(common | {"security_level": "standard"})),
                     ProviderConfig("complexity", **(common | {"max_complexity": "simple"})),
                     ProviderConfig("context", **common, max_input_chars=1),
                     ProviderConfig("latency", **common, expected_latency_ms=2000),
                     ProviderConfig("eligible", **common)]
        route = self.route(providers, replace(self.request, security_level="enhanced", complexity="complex"))
        self.assertEqual([p.name for p in route.providers], ["eligible"])
        self.assertEqual(set(route.rejected.values()), {"SECURITY_LEVEL", "COMPLEXITY", "CONTEXT_LIMIT", "LATENCY_BUDGET"})

    def test_unknown_price_does_not_claim_to_meet_budget(self):
        providers = [ProviderConfig("unknown", max_sensitivity="restricted"),
                     ProviderConfig("expensive", max_sensitivity="restricted", cost_per_1k_tokens_usd=10),
                     ProviderConfig("free", max_sensitivity="restricted", cost_per_1k_tokens_usd=0)]
        route = self.route(providers, replace(self.request, max_cost_usd=0))
        self.assertEqual([p.name for p in route.providers], ["free"])
        self.assertIsNone(route.estimated_costs["unknown"])

    def test_task_purpose_and_explicit_route_order(self):
        providers = [ProviderConfig("first", max_sensitivity="restricted", priority=0),
                     ProviderConfig("second", max_sensitivity="restricted", priority=100),
                     ProviderConfig("unused", max_sensitivity="restricted", purposes=("wealth_explanation",))]
        route = self.route(providers, routes={"intent_normalization": ["second", "first", "unused"]})
        self.assertEqual([p.name for p in route.providers], ["second", "first"])
        self.assertEqual(route.rejected["unused"], "PURPOSE_NOT_ALLOWED")
        route = self.route(providers)
        self.assertEqual([p.name for p in route.providers], ["first", "second"])

    def test_configuration_requires_review_and_safe_endpoint(self):
        with self.assertRaises(InferenceError) as caught:
            ProviderConfig("remote", kind="http", endpoint="https://example.invalid/infer", locality="external",
                           max_sensitivity="restricted").validate()
        self.assertEqual(caught.exception.code, "LLM_DATA_POLICY_NOT_REVIEWED")
        for endpoint in ("http://example.invalid/infer", "https://user:secret@example.invalid/infer",
                         "https://example.invalid/infer?api_key=secret", "file:///data/private"):
            with self.subTest(endpoint=endpoint), self.assertRaises(InferenceError):
                ProviderConfig("remote", kind="http", endpoint=endpoint, locality="external").validate()
        ProviderConfig("local", kind="http", endpoint="http://127.0.0.1:1234/infer").validate()

    def test_example_configuration_defaults_disabled(self):
        settings = LLMSettings.load(Path(__file__).resolve().parents[1] / "config" / "llm.example.toml")
        self.assertEqual(settings.mode, "disabled")
        self.assertEqual(settings.providers[0].kind, "mock")

    def test_invalid_and_future_modes_fail_closed(self):
        invalids = ['[gateway]\nmode="autonomous"', '[gateway]\ntimeout_seconds=nan',
                    '[gateway]\ntimeout_seconds=' + '9' * 400,
                    '[gateway]\nmax_output_tokens=true', '[gateway]\nmode="shadow"\nsecret="no"',
                    '[routes]\nintent_normalization=["missing"]', '[gateway]\nroutes=[]',
                    'routes=[]', 'not valid toml']
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "llm.toml"
            for content in invalids:
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(InferenceError):
                        LLMSettings.load(path)
