"""Evaluation-only wall-clock boundary and passive response observation.

DNS/proxy connection can outlive a socket timeout. A separate process may be
terminated without letting a late candidate escape into execution. Observation
never changes provider messages, responses, validation or fallback behavior.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from backend.llm.contracts import InferenceError
from llm.contracts import LLMResponse


def infer(config, trace_path, request, timeout, max_tokens, connection, observation_path, contract_version='2.0.0'):
    from llm_evaluation.adapters.compatible_adapter import CompatibleAdapter
    observed = {}

    def observe(frame, event, value):
        module = frame.f_globals.get('__name__', '')
        if event == 'call' and frame.f_code.co_name == 'parse' and module.startswith('backend.llm.providers.'):
            payload = frame.f_locals.get('payload')
            if isinstance(payload, dict): observed['provider_payload'] = payload
        elif event == 'return' and isinstance(value, LLMResponse):
            observed['response'] = asdict(value)

    sys.setprofile(observe)
    try:
        adapter = CompatibleAdapter(config, trace_path, timeout=timeout, max_output_tokens=max_tokens, contract_version=contract_version)
        validated = adapter.planner.generate_validated(request)
        result = {'plan': validated.plan.as_dict(), 'response':asdict(adapter.runtime.responses[request.request_id])}
    except InferenceError as error:
        result = {'error': error.code}
        observed['diagnostics'] = getattr(error, 'diagnostics', {})
    except Exception:
        result = {'error': 'LLM_EVALUATION_WORKER_FAILURE'}
    finally:
        sys.setprofile(None)
    # A compromised provider may echo its credential. Reject before any raw
    # response is persisted or returned; frozen traces contain hashes only.
    body = json.dumps(observed, ensure_ascii=False, allow_nan=False)
    secret = os.getenv(config.api_key_env, '') if config.api_key_env else ''
    if secret and (secret in body or secret in json.dumps(result, ensure_ascii=False)):
        observed = {'error':'LLM_SENSITIVE_RESPONSE'}
        result = {'error':'LLM_SENSITIVE_RESPONSE'}
        body = json.dumps(observed)
    if observation_path:
        p = Path(observation_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Payload contains only provider output for synthetic requests; NEVER headers or credentials.
        if len(body.encode()) > 262144:
            body = json.dumps({'error':'OBSERVATION_TOO_LARGE'})
        with p.open('x', encoding='utf-8') as f:
            f.write(body); f.flush(); os.fsync(f.fileno())
    connection.send(result)
    connection.close()
