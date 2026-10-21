"""Prompt -> deterministic route -> Gateway -> validated candidate -> LLM Trace.

No Agent, Bank API, executor, Validator override or banking authorization hook.
"""
from dataclasses import asdict, replace
import json
import time
from backend.llm.contracts import InferenceError
from llm.contracts import LLMRequest
from llm.gateway import LLMGateway
from llm.hashing import digest, snapshot, text_hash
from llm.structured import LLMStructuredResponse
from llm.tools import tool_envelope
from llm.providers.common import check_schema, validate_input


class LLMRuntime:
    def __init__(self, prompts, router, trace_store):
        self.prompts, self.router, self.trace_store = prompts, router, trace_store

    def generate(self, *, request_id, prompt_name, prompt_version, variables,
                 response_schema, routing, tool_definitions=(), candidate_validator=None):
        if candidate_validator is not None and not callable(candidate_validator):
            raise InferenceError('LLM_INVALID_REQUEST')
        prompt = self.prompts.get(prompt_name, prompt_version).render(variables)
        tools = tuple(tool_definitions)
        schema = json.loads(snapshot(response_schema))
        effective = tool_envelope(schema, tools) if tools else schema
        check_schema(effective)
        messages = prompt.messages
        selected = self.router.select(routing, messages, effective)
        timeout = min(selected.configs[0].timeout, routing.max_latency_ms / 1000)
        configs = tuple(replace(c, timeout=min(c.timeout, timeout)) for c in selected.configs)
        # Reserve time for an eligible fallback without extending the total deadline.
        gateway = LLMGateway(configs[0], configs[1:], attempt_timeout=timeout / len(configs))
        for config in configs:
            validate_input(messages, effective, (), config)
        self.trace_store.claim(request_id, prompt.prompt_hash, digest({
            'messages': messages, 'schema': effective,
            'providers': [asdict(c) for c in configs], 'attempt_timeout': gateway.attempt_timeout,
            'routing': asdict(routing)}), prompt.template_hash)
        started, attempts = time.monotonic(), []

        def observe(config, response, code):
            attempts.append({'provider': config.provider.name,
                'model': config.provider.model,
                'response_hash': text_hash(response.content) if response else None, 'error_code': code})

        response, failure = None, None
        validation_result = {'schema': 'NOT_RUN', 'plan': 'NOT_RUN'}
        try:
            response = gateway.infer(LLMRequest(messages, effective, (), configs[0]), observer=observe)
            result = LLMStructuredResponse.parse(response, schema, tools)
            validation_result['schema'] = 'PASS'
            if candidate_validator is not None:
                validation_result['plan'] = 'FAIL'
                candidate_validator(result)
                validation_result['plan'] = 'PASS'
        except InferenceError as error:
            failure = error
        except Exception:
            # Runtime implementation failures never return an unchecked candidate.
            failure = InferenceError('LLM_RUNTIME_FAILURE')
        code = failure.code if failure else None
        if code in {'LLM_OUTPUT_SCHEMA', 'LLM_INVALID_OUTPUT'} and validation_result['schema'] != 'PASS':
            validation_result['schema'] = 'FAIL'
        last = attempts[-1] if attempts else {'provider': configs[0].provider.name,
            'model': configs[0].provider.model, 'response_hash': None, 'error_code': code}
        last_response = next((a['response_hash'] for a in reversed(attempts)
                              if a['provider'] == last['provider'] and a['response_hash']), None)
        if not attempts:
            attempts.append(last)
        self.trace_store.finish(request_id, status='SUCCEEDED' if not failure else (
            'FAILED' if code in {'LLM_TIMEOUT', 'LLM_PROVIDER_UNAVAILABLE', 'LLM_RUNTIME_FAILURE'} else 'REJECTED'),
            provider=last['provider'], model=last['model'],
            latency=time.monotonic() - started, prompt_hash=prompt.prompt_hash,
            response_hash=last_response, attempts=attempts, error_code=code,
            validation_result=validation_result)
        if failure:
            raise failure
        return result
