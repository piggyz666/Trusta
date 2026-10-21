"""Validated JSON is still an untrusted candidate, never bank authorization."""
from dataclasses import dataclass
import json
from jsonschema import FormatChecker
from jsonschema.exceptions import ValidationError
from backend.llm.contracts import InferenceError
from backend.llm.validation import strict_json
from llm.contracts import LLMResponse
from llm.hashing import digest, snapshot, text_hash
from llm.providers.common import check_schema
from llm.tools import ToolCall, tool_envelope


@dataclass(frozen=True)
class LLMStructuredResponse:
    response: LLMResponse
    _data: str
    schema_hash: str
    response_hash: str
    tool_calls: tuple[ToolCall, ...] = ()

    @property
    def data(self):
        return json.loads(self._data)

    @classmethod
    def parse(cls, response, schema, tools=()):
        effective = tool_envelope(schema, tools) if tools else schema
        validator = check_schema(effective).evolve(format_checker=FormatChecker())
        if not isinstance(response, LLMResponse) or response.finish_reason != 'stop':
            raise InferenceError('LLM_PROVIDER_PROTOCOL')
        try:
            if len(response.content.encode()) > 131072:
                raise InferenceError('LLM_RESPONSE_TOO_LARGE')
            output = strict_json(response.content)
            validator.validate(output)
        except (ValidationError, ValueError, TypeError, RecursionError, UnicodeError):
            raise InferenceError('LLM_OUTPUT_SCHEMA') from None
        data = output['data'] if tools else output
        calls = tuple(ToolCall(**item) for item in output['tool_requests']) if tools else ()
        return cls(response, snapshot(data), digest(effective), text_hash(response.content), calls)
