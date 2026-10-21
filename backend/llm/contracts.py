from dataclasses import dataclass
from typing import Any


SENSITIVITY = {"public": 0, "internal": 1, "restricted": 2}
SECURITY = {"standard": 0, "enhanced": 1}
COMPLEXITY = {"simple": 0, "standard": 1, "complex": 2}


class InferenceError(Exception):
    """Only stable codes cross the Gateway; provider bodies and secrets never do."""
    def __init__(self, code, retryable=False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


@dataclass(frozen=True)
class InferenceContext:
    task_id: str | None
    user_id: str
    agent: str
    task_type: str
    revision: int
    conversation_id: str | None = None


@dataclass(frozen=True)
class InferenceRequest:
    request_id: str
    context: InferenceContext
    purpose: str
    payload: dict
    sensitivity: str = "restricted"
    security_level: str = "standard"
    complexity: str = "standard"
    max_cost_usd: float | None = None


@dataclass(frozen=True)
class ProviderRequest:
    """Provider receives projected messages, never identity credentials or bank handles."""
    messages: tuple[dict, ...]
    output_schema: dict
    max_output_tokens: int
    timeout_seconds: float


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    model: str | None = None
    usage: dict | None = None


@dataclass(frozen=True)
class InferenceResult:
    request_id: str
    status: str
    output: dict[str, Any] | None = None
    provider: str | None = None
    error_code: str | None = None
