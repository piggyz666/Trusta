from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit
from backend.llm.contracts import COMPLEXITY, SECURITY, SENSITIVITY, InferenceError


PURPOSES = {"intent_normalization", "plan_proposal", "finance_explanation", "wealth_explanation",
            "conversation_intent", "finance_presentation", "wealth_presentation"}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    kind: str = "mock"
    model: str = "mock"
    endpoint: str = ""
    api_key_env: str = ""
    locality: str = "local"
    max_sensitivity: str = "public"
    data_policy_reviewed: bool = False
    security_level: str = "standard"
    max_complexity: str = "complex"
    priority: int = 100
    max_input_chars: int = 20000
    cost_per_1k_tokens_usd: float | None = None
    expected_latency_ms: int | None = None
    purposes: tuple[str, ...] = tuple(sorted(PURPOSES))
    structured_outputs: bool = False
    model_field: str = "model"
    messages_field: str = "messages"
    response_path: tuple[str, ...] = ("output", "text")
    proxy_env: str = ""
    json_output: bool = False
    thinking_mode: str = "default"

    def validate(self):
        if self.thinking_mode not in {"default", "enabled", "disabled"} or (self.thinking_mode != "default" and self.kind != "openai_compatible"):
            raise InferenceError("LLM_INVALID_CONFIG")
        if (not isinstance(self.name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.name)
                or self.kind not in {"mock", "openai", "openai_compatible", "http", "gemini"}
                or not isinstance(self.model, str) or not 1 <= len(self.model) <= 128
                or self.locality not in {"local", "private", "external"}
                or self.max_sensitivity not in SENSITIVITY or self.security_level not in SECURITY
                or self.max_complexity not in COMPLEXITY):
            raise InferenceError("LLM_INVALID_CONFIG")
        if (type(self.data_policy_reviewed) is not bool or type(self.structured_outputs) is not bool
                or type(self.json_output) is not bool
                or type(self.priority) is not int or not 0 <= self.priority <= 10000
                or type(self.max_input_chars) is not int or not 1 <= self.max_input_chars <= 100000
                or not self.purposes or any(p not in PURPOSES for p in self.purposes)
                or len(set(self.purposes)) != len(self.purposes)):
            raise InferenceError("LLM_INVALID_CONFIG")
        # JSON syntax shaping is separate from provider-enforced JSON Schema.
        if self.json_output and (self.kind not in {"openai", "openai_compatible"} or self.structured_outputs):
            raise InferenceError("LLM_INVALID_CONFIG")
        if self.cost_per_1k_tokens_usd is not None and not valid_number(self.cost_per_1k_tokens_usd, 0, 1000):
            raise InferenceError("LLM_INVALID_CONFIG")
        if self.expected_latency_ms is not None and (type(self.expected_latency_ms) is not int
                                                   or not 1 <= self.expected_latency_ms <= 60000):
            raise InferenceError("LLM_INVALID_CONFIG")
        if self.kind != "mock" and SENSITIVITY[self.max_sensitivity] > 0 and not self.data_policy_reviewed:
            raise InferenceError("LLM_DATA_POLICY_NOT_REVIEWED")
        if (not isinstance(self.api_key_env, str) or
                (self.api_key_env and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", self.api_key_env))):
            raise InferenceError("LLM_INVALID_CONFIG")
        if not isinstance(self.proxy_env,str) or self.proxy_env and (self.locality!='external' or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,100}',self.proxy_env)):
            raise InferenceError('LLM_INVALID_CONFIG')
        if self.kind == "mock":
            if self.endpoint or self.api_key_env:
                raise InferenceError("LLM_INVALID_CONFIG")
            return
        if not isinstance(self.endpoint, str):
            raise InferenceError("LLM_INVALID_ENDPOINT")
        try:
            url = urlsplit(self.endpoint)
            port = url.port
        except ValueError:
            raise InferenceError("LLM_INVALID_ENDPOINT") from None
        loopback = url.hostname in {"127.0.0.1", "localhost", "::1"}
        if (not url.hostname or url.username or url.password or url.query or url.fragment
                or url.scheme not in {"http", "https"} or (url.scheme == "http" and not loopback)
                or (self.locality == "local" and not loopback) or (port is not None and port == 0)):
            raise InferenceError("LLM_INVALID_ENDPOINT")
        fields = (self.model_field, self.messages_field)
        if (any(not isinstance(f, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", f) for f in fields)
                or len(set(fields)) != 2 or set(fields) & {"max_tokens", "response_format"}
                or not self.response_path or len(self.response_path) > 8
                or any(not isinstance(p, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", p)
                       for p in self.response_path)):
            raise InferenceError("LLM_INVALID_CONFIG")
        if self.kind == "http" and self.structured_outputs:
            raise InferenceError("LLM_INVALID_CONFIG")


def valid_number(value, minimum, maximum):
    try:
        return type(value) in {int, float} and math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        return False


@dataclass(frozen=True)
class LLMSettings:
    mode: str = "disabled"
    version: str = "phase3.1"
    timeout_seconds: float = 10
    max_output_tokens: int = 512
    max_request_chars: int = 20000
    max_fallbacks: int = 1
    providers: tuple[ProviderConfig, ...] = ()
    routes: dict = field(default_factory=dict)
    graph_format: str = 'full_v1'
    graph_repair_attempts: int = 0

    def validate(self):
        if type(self.graph_repair_attempts) is not int or not 0 <= self.graph_repair_attempts <= 1:
            raise InferenceError('LLM_INVALID_CONFIG')
        if self.graph_format not in {'full_v1','compact_v2','semantic_v3'}:
            raise InferenceError('LLM_INVALID_CONFIG')
        if not isinstance(self.routes, dict) or not isinstance(self.providers, (list, tuple)):
            raise InferenceError("LLM_INVALID_CONFIG")
        if (self.mode not in {"disabled", "shadow", "assisted"} or not isinstance(self.version, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", self.version)
                or not valid_number(self.timeout_seconds, 0.05, 60)
                or type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 4096
                or type(self.max_request_chars) is not int or not 1 <= self.max_request_chars <= 100000
                or type(self.max_fallbacks) is not int or not 0 <= self.max_fallbacks <= 2):
            raise InferenceError("LLM_INVALID_CONFIG")
        names = set()
        for provider in self.providers:
            provider.validate()
            if provider.name in names:
                raise InferenceError("LLM_INVALID_CONFIG")
            names.add(provider.name)
        for purpose, route in self.routes.items():
            if (purpose not in PURPOSES or not isinstance(route, (list, tuple)) or not route
                    or len(set(route)) != len(route) or any(n not in names for n in route)):
                raise InferenceError("LLM_INVALID_CONFIG")
        return self

    @classmethod
    def load(cls, path):
        if Path(path).suffix.lower() in {'.yaml', '.yml'}:
            from llm.configuration import load_settings
            return load_settings(path)
        try:
            with Path(path).open("rb") as stream:
                raw = tomllib.load(stream)
            if set(raw) - {"gateway", "providers", "routes"}:
                raise ValueError()
            gateway = raw.get("gateway", {})
            if set(gateway) - (set(cls.__dataclass_fields__) - {"providers", "routes"}):
                raise ValueError()
            providers = []
            for entry in raw.get("providers", []):
                entry = dict(entry)
                for name in ("purposes", "response_path"):
                    if name in entry:
                        entry[name] = tuple(entry[name])
                providers.append(ProviderConfig(**entry))
            return cls(**gateway, providers=tuple(providers), routes=raw.get("routes", {})).validate()
        except InferenceError:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise InferenceError("LLM_INVALID_CONFIG") from None
