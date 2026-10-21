from dataclasses import replace
import time
from backend.llm.contracts import InferenceError
from llm.contracts import LLMRequest
from llm.providers import create_provider
from llm.providers.common import check_schema, validate_input, validate_response


class LLMGateway:
    """Transport facade for projected inputs, not the banking trust boundary.

    Business code continues to use backend.llm.gateway.LLMGateway, which binds
    this abstraction to identity, data policy, audit and grounded validation.
    """
    def __init__(self, model_config, fallbacks=(), *, enabled=True, attempt_timeout=None):
        if type(enabled) is not bool:
            raise InferenceError('LLM_INVALID_CONFIG')
        self.enabled = enabled
        self.model_config = model_config.validate()
        from backend.llm.configuration import valid_number
        if attempt_timeout is not None and (not valid_number(attempt_timeout, 0, model_config.timeout)
                                             or attempt_timeout <= 0):
            raise InferenceError('LLM_INVALID_CONFIG')
        self.attempt_timeout = attempt_timeout
        self.fallbacks = tuple(c.validate() for c in fallbacks)
        if len(self.fallbacks) > 2:
            raise InferenceError('LLM_INVALID_CONFIG')
        primary = model_config.provider
        for item in self.fallbacks:
            target = item.provider
            # A low-level fallback cannot silently move data into a different
            # trust domain. Cross-domain routing remains the contextual router.
            if (target.locality != primary.locality or target.max_sensitivity != primary.max_sensitivity or
                    target.data_policy_reviewed != primary.data_policy_reviewed or
                    target.security_level != primary.security_level or
                    frozenset(target.purposes) != frozenset(primary.purposes)):
                raise InferenceError('LLM_FALLBACK_POLICY_MISMATCH')

    @classmethod
    def from_config(cls, path):
        from llm.configuration import load_settings
        from llm.contracts import LLMModelConfig
        settings = load_settings(path)
        return cls(LLMModelConfig(settings.providers[0], settings.timeout_seconds, settings.max_output_tokens),
                   [LLMModelConfig(p, settings.timeout_seconds, settings.max_output_tokens)
                    for p in settings.providers[1:1 + settings.max_fallbacks]],
                   enabled=settings.mode != 'disabled')

    def generate(self, messages, response_schema=None, tools=(), config=None):
        if not self.enabled:
            raise InferenceError('LLM_DISABLED')
        validate_input(messages, response_schema, tools, config or self.model_config)
        request = LLMRequest(tuple(messages), response_schema, tuple(tools), config or self.model_config)
        return self.infer(request)

    def infer(self, request, *, observer=None):
        if not self.enabled:
            raise InferenceError('LLM_DISABLED')
        if not isinstance(request, LLMRequest):
            raise InferenceError('LLM_INVALID_REQUEST')
        config = request.model_config or self.model_config
        if config != self.model_config and self.fallbacks:
            raise InferenceError('LLM_FALLBACK_POLICY_MISMATCH')
        validate_input(request.messages, request.schema, request.tools, config)
        if request.schema is not None:
            check_schema(request.schema)
        started = time.monotonic()
        deadline = started + config.timeout
        candidates = (config,) + self.fallbacks
        for index, candidate in enumerate(candidates):
            remaining = min(candidate.timeout, deadline - time.monotonic())
            if self.attempt_timeout is not None:
                remaining = min(remaining, self.attempt_timeout)
            if remaining <= 0:
                raise InferenceError('LLM_TIMEOUT', retryable=True)
            try:
                response = create_provider(candidate.provider.kind).generate(request.messages,
                    request.schema, request.tools, replace(candidate, timeout=remaining))
                response = replace(response, provider=candidate.provider.name)
                if observer is not None:
                    observer(candidate, response, None)
                validate_response(response, request.schema)
                if time.monotonic() > deadline:
                    raise InferenceError('LLM_TIMEOUT', retryable=True)
                return replace(response, latency=time.monotonic() - started)
            except InferenceError as error:
                if observer is not None:
                    observer(candidate, None, error.code)
                if (not error.retryable or error.code not in {'LLM_TIMEOUT', 'LLM_PROVIDER_UNAVAILABLE'} or
                        index == len(candidates) - 1):
                    raise
        raise InferenceError('LLM_PROVIDER_UNAVAILABLE')
