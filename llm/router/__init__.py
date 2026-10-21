"""Deterministic provider selection for already projected LLM inputs.

Package migration preserves the existing llm.router public interface.
"""
from dataclasses import dataclass
import re
from backend.llm.configuration import PURPOSES, valid_number
from backend.llm.contracts import COMPLEXITY, SECURITY, SENSITIVITY, InferenceError
from llm.contracts import LLMModelConfig
from llm.hashing import snapshot


def trust_domain(provider):
    return (provider.locality, provider.max_sensitivity, provider.data_policy_reviewed,
            provider.security_level, frozenset(provider.purposes))


@dataclass(frozen=True)
class RoutingContext:
    task_type: str
    complexity: str = 'standard'
    max_cost_usd: float | None = None  # conservative total across all attempts
    max_latency_ms: int = 10000       # shared wall-clock deadline
    purpose: str = 'plan_proposal'
    sensitivity: str = 'public'
    security_level: str = 'standard'

    def validate(self):
        if (not isinstance(self.task_type, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}', self.task_type)
                or not isinstance(self.complexity, str) or self.complexity not in COMPLEXITY
                or not isinstance(self.purpose, str) or self.purpose not in PURPOSES
                or not isinstance(self.sensitivity, str) or self.sensitivity not in SENSITIVITY
                or not isinstance(self.security_level, str) or self.security_level not in SECURITY
                or type(self.max_latency_ms) is not int or not 1 <= self.max_latency_ms <= 60000
                or self.max_cost_usd is not None and not valid_number(self.max_cost_usd, 0, 1000)):
            raise InferenceError('LLM_INVALID_ROUTE')
        return self


@dataclass(frozen=True)
class ModelRoute:
    config: LLMModelConfig
    task_types: tuple[str, ...] = ('*',)

    def validate(self):
        if not isinstance(self.config, LLMModelConfig):
            raise InferenceError('LLM_INVALID_ROUTE')
        self.config.validate()
        if (not isinstance(self.task_types, tuple) or not self.task_types or
                any(not isinstance(s, str) or s != '*' and not re.fullmatch(
                    r'[A-Za-z][A-Za-z0-9_.-]{0,63}', s) for s in self.task_types) or
                len(set(self.task_types)) != len(self.task_types)):
            raise InferenceError('LLM_INVALID_ROUTE')
        return self


@dataclass(frozen=True)
class ModelSelection:
    configs: tuple[LLMModelConfig, ...]
    estimated_total_cost_usd: float | None
    rejected: tuple[tuple[str, str], ...]


class ModelRouter:
    def __init__(self, routes, *, max_fallbacks=1, enabled=True):
        if not isinstance(routes, (tuple, list)) or any(not isinstance(r, ModelRoute) for r in routes):
            raise InferenceError('LLM_INVALID_ROUTE')
        self.routes = tuple(r.validate() for r in routes)
        if (not self.routes or type(max_fallbacks) is not int or not 0 <= max_fallbacks <= 2
                or type(enabled) is not bool or
                len({r.config.provider.name for r in self.routes}) != len(self.routes)):
            raise InferenceError('LLM_INVALID_ROUTE')
        self.max_fallbacks = max_fallbacks
        self.enabled = enabled

    @classmethod
    def from_config(cls, path, *, task_routes=None):
        from llm.configuration import load_settings
        settings = load_settings(path)
        mapping = {} if task_routes is None else task_routes
        if (not isinstance(mapping, dict) or set(mapping) - {p.name for p in settings.providers}):
            raise InferenceError('LLM_INVALID_ROUTE')
        # Reuse existing route allowlists by restricting provider purposes.
        from dataclasses import replace
        routes = []
        for p in settings.providers:
            if p.name in mapping and (not isinstance(mapping[p.name], (tuple, list)) or not mapping[p.name]):
                raise InferenceError('LLM_INVALID_ROUTE')
            purposes = tuple(s for s in p.purposes if s not in settings.routes or p.name in settings.routes[s])
            if purposes:
                routes.append(ModelRoute(LLMModelConfig(replace(p, purposes=purposes),
                    settings.timeout_seconds, settings.max_output_tokens), tuple(mapping.get(p.name, ('*',)))))
        return cls(routes, max_fallbacks=settings.max_fallbacks, enabled=settings.mode != 'disabled')

    def select(self, context, messages, schema):
        if not self.enabled:
            raise InferenceError('LLM_DISABLED')
        if not isinstance(context, RoutingContext):
            raise InferenceError('LLM_INVALID_ROUTE')
        context.validate()
        wire = snapshot(list(messages)) + snapshot(schema)
        size, input_estimate = len(wire), len(wire.encode())
        eligible, rejected = [], []
        for route in self.routes:
            p, reason = route.config.provider, None
            if '*' not in route.task_types and context.task_type not in route.task_types:
                reason = 'TASK_TYPE'
            elif context.purpose not in p.purposes:
                reason = 'PURPOSE'
            elif SENSITIVITY[context.sensitivity] > SENSITIVITY[p.max_sensitivity]:
                reason = 'SENSITIVITY'
            elif SECURITY[context.security_level] > SECURITY[p.security_level]:
                reason = 'SECURITY_LEVEL'
            elif COMPLEXITY[context.complexity] > COMPLEXITY[p.max_complexity]:
                reason = 'COMPLEXITY'
            elif size > p.max_input_chars:
                reason = 'CONTEXT_LIMIT'
            elif p.expected_latency_ms is not None and p.expected_latency_ms > context.max_latency_ms:
                reason = 'LATENCY_BUDGET'
            cost = None if p.cost_per_1k_tokens_usd is None else (
                input_estimate + route.config.max_output_tokens) * p.cost_per_1k_tokens_usd / 1000
            if not reason and context.max_cost_usd is not None and (cost is None or cost > context.max_cost_usd):
                reason = 'COST_BUDGET'
            if reason:
                rejected.append((p.name, reason))
            else:
                eligible.append((route.config, cost))
        eligible.sort(key=lambda pair: (pair[1] if pair[1] is not None else float('inf'),
            pair[0].provider.expected_latency_ms or float('inf'), pair[0].provider.priority,
            pair[0].provider.name))
        if not eligible:
            raise InferenceError('LLM_NO_ELIGIBLE_PROVIDER')
        selected, total = [eligible[0][0]], eligible[0][1]
        domain = trust_domain(selected[0].provider)
        for config, cost in eligible[1:]:
            if len(selected) > self.max_fallbacks:
                break
            if trust_domain(config.provider) != domain:
                rejected.append((config.provider.name, 'FALLBACK_TRUST_DOMAIN'))
                continue
            if context.max_cost_usd is not None and (cost is None or total + cost > context.max_cost_usd):
                rejected.append((config.provider.name, 'TOTAL_COST_BUDGET'))
                continue
            selected.append(config)
            total = None if total is None or cost is None else total + cost
        return ModelSelection(tuple(selected), total, tuple(rejected))
