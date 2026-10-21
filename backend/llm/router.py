from dataclasses import dataclass
from backend.domain import canonical
from backend.llm.contracts import COMPLEXITY, SECURITY, SENSITIVITY, InferenceError


@dataclass(frozen=True)
class Route:
    providers: tuple
    rejected: dict
    estimated_costs: dict


class ModelRouter:
    def __init__(self, settings):
        self.settings = settings

    def select(self, request, provider_request):
        if (request.sensitivity not in SENSITIVITY or request.security_level not in SECURITY
                or request.complexity not in COMPLEXITY):
            raise InferenceError("LLM_INVALID_INPUT")
        size = len(canonical(list(provider_request.messages))) + len(canonical(provider_request.output_schema))
        # UTF-8 bytes are a conservative routing estimate, not measured billing tokens.
        input_estimate = len(canonical(list(provider_request.messages)).encode()) + len(canonical(provider_request.output_schema).encode())
        rejected, eligible, estimates = {}, [], {}
        ordered = self.settings.routes.get(request.purpose, [p.name for p in self.settings.providers])
        for provider in self.settings.providers:
            reason = None
            if provider.name not in ordered or request.purpose not in provider.purposes:
                reason = "PURPOSE_NOT_ALLOWED"
            elif SENSITIVITY[request.sensitivity] > SENSITIVITY[provider.max_sensitivity]:
                reason = "DATA_SENSITIVITY"
            elif SECURITY[request.security_level] > SECURITY[provider.security_level]:
                reason = "SECURITY_LEVEL"
            elif COMPLEXITY[request.complexity] > COMPLEXITY[provider.max_complexity]:
                reason = "COMPLEXITY"
            elif size > provider.max_input_chars:
                reason = "CONTEXT_LIMIT"
            elif provider.expected_latency_ms is not None and provider.expected_latency_ms > provider_request.timeout_seconds * 1000:
                reason = "LATENCY_BUDGET"
            price = provider.cost_per_1k_tokens_usd
            estimate = None if price is None else (input_estimate + provider_request.max_output_tokens) * price / 1000
            estimates[provider.name] = estimate
            if not reason and request.max_cost_usd is not None and (estimate is None or estimate > request.max_cost_usd):
                reason = "COST_BUDGET"
            if reason:
                rejected[provider.name] = reason
            else:
                eligible.append(provider)
        if request.purpose in self.settings.routes:
            eligible.sort(key=lambda p: ordered.index(p.name))
        else:
            eligible.sort(key=lambda p: (p.priority, p.expected_latency_ms or float("inf"),
                                         estimates[p.name] if estimates[p.name] is not None else float("inf"), p.name))
        return Route(tuple(eligible), rejected, estimates)
