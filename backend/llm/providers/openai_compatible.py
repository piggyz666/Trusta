from backend.llm.contracts import InferenceError, ProviderResponse
from backend.llm.providers.http_adapter import HTTPAdapter


class OpenAICompatibleProvider(HTTPAdapter):
    """Chat Completions protocol only. Compatibility is configured, never inferred."""
    def describe_capabilities(self):
        return {"protocol": "chat_completions", "structured_outputs": self.config.structured_outputs,
                "json_output": self.config.json_output,
                "streaming": False}

    def body(self, request):
        body = {"model": self.config.model, "messages": list(request.messages),
                "max_tokens": request.max_output_tokens, "stream": False}
        if self.config.structured_outputs:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "trusta_candidate", "strict": True, "schema": request.output_schema}}
        elif self.config.json_output:
            body["response_format"] = {"type": "json_object"}
        if self.config.thinking_mode != "default":
            body["thinking"] = {"type": self.config.thinking_mode}
        return body

    def parse(self, payload):
        try:
            choice = payload["choices"][0]
            message = choice["message"]
            if message.get("refusal") or choice.get("finish_reason") == "content_filter":
                raise InferenceError("LLM_REFUSED")
            if message.get("tool_calls") or message.get("function_call"):
                raise InferenceError("LLM_TOOL_CALL_FORBIDDEN")
            if choice.get("finish_reason") != "stop":
                raise InferenceError("LLM_INCOMPLETE_OUTPUT")
            text = message["content"]
            if not isinstance(text, str):
                raise ValueError()
            usage = payload.get("usage")
            measured = None
            if isinstance(usage, dict):
                measured = {}
                for source, dest in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
                    value = usage.get(source)
                    if type(value) is int and 0 <= value <= 10**9:
                        measured[dest] = value
            model = payload.get("model")
            if model is not None and (not isinstance(model, str) or len(model) > 128):
                raise ValueError()
            return ProviderResponse(text, model, measured)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise InferenceError("LLM_PROVIDER_PROTOCOL") from None
