from dataclasses import dataclass, field
from backend.llm.configuration import ProviderConfig, valid_number
from backend.llm.contracts import InferenceError


@dataclass(frozen=True)
class ProtocolProviderConfig(ProviderConfig):
    """Operator-declared wire differences, never inferred from a model brand."""
    token_parameter: str = 'protocol_default'
    authentication: str = 'bearer'

    def validate(self):
        super().validate()
        if self.authentication not in {'bearer', 'api-key'} or (self.authentication != 'bearer' and self.kind != 'openai_compatible'):
            raise InferenceError('LLM_INVALID_CONFIG')
        if (not isinstance(self.token_parameter, str) or
                self.token_parameter not in {'protocol_default', 'max_tokens', 'max_completion_tokens'}
                or self.kind not in {'openai', 'openai_compatible'} and self.token_parameter != 'protocol_default'):
            raise InferenceError('LLM_INVALID_CONFIG')


@dataclass(frozen=True)
class LLMModelConfig:
    provider: ProviderConfig
    timeout: float = 10
    max_output_tokens: int = 512

    def validate(self):
        if not isinstance(self.provider, ProviderConfig):
            raise InferenceError('LLM_INVALID_CONFIG')
        try:
            self.provider.validate()
        except (TypeError, ValueError, AttributeError):
            raise InferenceError('LLM_INVALID_CONFIG') from None
        if (not valid_number(self.timeout, 0, 60) or self.timeout <= 0 or
                type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 4096):
            raise InferenceError('LLM_INVALID_CONFIG')
        return self


@dataclass(frozen=True)
class LLMRequest:
    messages: tuple[dict, ...]
    schema: dict | None = None
    tools: tuple[dict, ...] = ()
    model_config: LLMModelConfig | None = None


@dataclass(frozen=True)
class LLMResponse:
    content: str
    usage: dict = field(default_factory=dict)
    finish_reason: str = 'stop'
    model: str = ''
    latency: float = 0  # seconds, measured using a monotonic clock
    provider: str = ''  # filled by Gateway, never taken from provider payload
    diagnostics: dict = field(default_factory=dict)  # bounded numeric protocol metadata only
