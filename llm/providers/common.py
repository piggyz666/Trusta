import time
import os
from urllib.parse import unquote
from backend.domain import canonical
from backend.llm.contracts import InferenceError, ProviderRequest
from backend.llm.validation import strict_json
from llm.base import BaseLLMProvider
from llm.contracts import LLMModelConfig, LLMResponse


def validate_input(messages, schema, tools, config):
    if not isinstance(config, LLMModelConfig):
        raise InferenceError('LLM_INVALID_CONFIG')
    config.validate()
    # This interface carries tool declarations for future protocols. TRUSTA V1
    # explicitly disallows tool invocation by an LLM, including transport tools.
    if tools:
        raise InferenceError('LLM_TOOL_CALL_FORBIDDEN')
    if not isinstance(tools, (list, tuple)):
        raise InferenceError('LLM_INVALID_REQUEST')
    if (not isinstance(messages, (list, tuple)) or not 1 <= len(messages) <= 100 or
            any(not isinstance(m, dict) or set(m) != {'role', 'content'} or
                m['role'] not in ('system', 'user', 'assistant') or
                not isinstance(m['content'], str) for m in messages)):
        raise InferenceError('LLM_INVALID_REQUEST')
    if schema is not None and not isinstance(schema, dict):
        raise InferenceError('LLM_INVALID_SCHEMA')
    try:
        serialized = canonical({'messages': messages, 'schema': schema})
        serialized.encode('utf-8')
        # Match the existing ModelRouter's definition exactly; adding JSON
        # envelope characters must not reject an already eligible boundary input.
        size = len(canonical(list(messages))) + len(canonical(schema or {}))
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise InferenceError('LLM_INVALID_REQUEST') from None
    if size > config.provider.max_input_chars:
        raise InferenceError('LLM_REQUEST_TOO_LARGE')
    if config.provider.locality in {'external', 'private'} and (not config.provider.api_key_env or
            not os.getenv(config.provider.api_key_env)):
        raise InferenceError('LLM_PROVIDER_CREDENTIALS')


def protocol_diagnostics(payload, kind):
    """Only measured counts; reasoning is already included in Chat completions."""
    counts = {}
    if not isinstance(payload, dict):
        return counts
    if kind in {'openai', 'openai_compatible'}:
        usage = payload.get('usage', {})
        if not isinstance(usage, dict):
            return counts
        total = usage.get('completion_tokens')
        details = usage.get('completion_tokens_details', {})
        reasoning = details.get('reasoning_tokens') if isinstance(details, dict) else None
        if type(total) is int and 0 <= total <= 10**9:
            counts['completion_tokens'] = total
            if type(reasoning) is int and 0 <= reasoning <= total:
                # Remaining tokens may include protocol framing; not an exact
                # visible-text token count or an independent billable addition.
                counts.update(reasoning_tokens=reasoning, non_reasoning_tokens=total - reasoning)
    else:
        usage = payload.get('usageMetadata', {})
        if isinstance(usage, dict):
            for source, dest in (('candidatesTokenCount', 'visible_output_tokens'),
                                 ('thoughtsTokenCount', 'reasoning_tokens')):
                value = usage.get(source)
                if type(value) is int and 0 <= value <= 10**9:
                    counts[dest] = value
            if counts:
                counts['completion_tokens'] = sum(counts.values())
    return counts


class TransportProvider(BaseLLMProvider):
    """Reuse bounded HTTP, secret handling and fail-closed response parsers.

    Each call owns a new transport object; there is no shared last-response
    field and no implicit retry, model substitution or prompt rewriting.
    """
    transport_class = None
    kind = None

    def generate(self, messages, response_schema, tools, config):
        validate_input(messages, response_schema, tools, config)
        if config.provider.kind != self.kind:
            raise InferenceError('LLM_INVALID_CONFIG')
        started = time.monotonic()
        # A schema-less request must not accidentally activate native JSON mode.
        # The contextual backend always supplies its original schema.
        class RequestTransport(self.transport_class):
            diagnostics = None

            def auth_headers(transport):
                headers = super().auth_headers()
                if getattr(config.provider, 'authentication', 'bearer') == 'api-key':
                    authorization = headers.pop('Authorization', None)
                    if authorization is None:
                        raise InferenceError('LLM_PROVIDER_CREDENTIALS')
                    headers['api-key'] = authorization.removeprefix('Bearer ')
                return headers

            def body(transport, request):
                body = super().body(request)
                token_parameter = getattr(config.provider, 'token_parameter', 'protocol_default')
                if token_parameter != 'protocol_default':
                    body.pop('max_tokens', None)
                    body.pop('max_completion_tokens', None)
                    body[token_parameter] = request.max_output_tokens
                if response_schema is None:
                    body.pop('response_format', None)
                    if 'generationConfig' in body:
                        body['generationConfig'].pop('responseMimeType', None)
                        body['generationConfig'].pop('responseJsonSchema', None)
                return body

            def parse(transport, payload):
                if not isinstance(payload, dict):
                    raise InferenceError('LLM_PROVIDER_PROTOCOL')
                transport.diagnostics = protocol_diagnostics(payload, self.kind)
                choices = payload.get('candidates' if self.kind == 'gemini' else 'choices')
                if not isinstance(choices, list) or len(choices) != 1:
                    raise InferenceError('LLM_PROVIDER_PROTOCOL')
                try:
                    return super().parse(payload)
                except InferenceError as error:
                    error.diagnostics = dict(transport.diagnostics)
                    raise

        transport = RequestTransport(config.provider)
        response = transport.infer(ProviderRequest(
            tuple(messages), response_schema or {}, config.max_output_tokens, config.timeout))
        latency = time.monotonic() - started
        if latency > config.timeout:
            raise InferenceError('LLM_TIMEOUT', retryable=True)
        try:
            too_large = not isinstance(response.text, str) or len(response.text.encode()) > 131072
        except UnicodeError:
            raise InferenceError('LLM_PROVIDER_PROTOCOL') from None
        if too_large:
            raise InferenceError('LLM_RESPONSE_TOO_LARGE')
        usage = response.usage or {}
        if (not isinstance(usage, dict) or any(k not in {'input_tokens', 'output_tokens'} or
                type(v) is not int or not 0 <= v <= 10**9 for k, v in usage.items())):
            raise InferenceError('LLM_PROVIDER_PROTOCOL')
        model = response.model if response.model is not None else config.provider.model
        if not isinstance(model, str) or not 1 <= len(model) <= 128:
            raise InferenceError('LLM_PROVIDER_PROTOCOL')
        # Apply to local/private models as well as the isolated paid evaluator.
        # A provider may echo its Authorization credential in otherwise valid JSON.
        secret = os.getenv(config.provider.api_key_env, '') if config.provider.api_key_env else ''
        diagnostics = transport.diagnostics or {}
        if secret and (secret in response.text or secret == model):
            raise InferenceError('LLM_SENSITIVE_RESPONSE')
        if secret and secret in model:
            # A short local credential can coincide with a normal model-name
            # substring. Never publish contaminated metadata or silently claim
            # that the substituted identifier was reported by the provider.
            model = config.provider.model
            if secret in model:
                raise InferenceError('LLM_SENSITIVE_RESPONSE')
            diagnostics = dict(diagnostics, reported_model_redacted=1)
        return LLMResponse(response.text, usage, 'stop', model, latency,
                           diagnostics=diagnostics)


def check_schema(schema):
    """Schema configuration is local; never fetch provider-supplied remote refs."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
    def walk(value, depth=0):
        if depth > 32:
            raise InferenceError('LLM_INVALID_SCHEMA')
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {'$ref', '$dynamicRef'} and (not isinstance(item, str) or not item.startswith('#/')):
                    raise InferenceError('LLM_REMOTE_SCHEMA_FORBIDDEN')
                if key in {'$ref', '$dynamicRef'}:
                    try:
                        target = schema
                        for part in unquote(item[2:]).split('/'):
                            part = part.replace('~1', '/').replace('~0', '~')
                            target = target[int(part)] if isinstance(target, list) else target[part]
                        if not isinstance(target, (dict, bool)):
                            raise ValueError()
                    except (KeyError, IndexError, TypeError, ValueError):
                        raise InferenceError('LLM_INVALID_SCHEMA') from None
                if key == '$id':
                    raise InferenceError('LLM_REMOTE_SCHEMA_FORBIDDEN')
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
    try:
        if not isinstance(schema, dict) or len(canonical(schema).encode()) > 65536:
            raise InferenceError('LLM_INVALID_SCHEMA')
        walk(schema)
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)
    except (SchemaError, ValueError, TypeError, RecursionError):
        raise InferenceError('LLM_INVALID_SCHEMA') from None


def validate_response(response, schema):
    if schema is None:
        return
    from jsonschema.exceptions import ValidationError
    try:
        check_schema(schema).validate(strict_json(response.content))
    except ValidationError:
        raise InferenceError('LLM_OUTPUT_SCHEMA') from None
    except (RecursionError, ValueError, TypeError):
        raise InferenceError('LLM_OUTPUT_SCHEMA') from None
