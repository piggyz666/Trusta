"""Strict, secret-reference-only YAML; reuses existing routing policy config."""
from pathlib import Path
import re
import yaml
from backend.llm.configuration import LLMSettings, ProviderConfig
from llm.contracts import ProtocolProviderConfig
from backend.llm.contracts import InferenceError

ALIASES = {'deepseek': 'openai_compatible', 'glm': 'openai_compatible',
           'kimi': 'openai_compatible', 'mimo': 'openai_compatible',
           'openai_compatible': 'openai_compatible', 'openai': 'openai', 'gemini': 'gemini'}


class ConfigLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError('aliases forbidden')
        if len(self.compose_stack) >= 32:
            raise ValueError('nested config')
        self.compose_stack.append(None)
        try:
            return super().compose_node(parent, index)
        finally:
            self.compose_stack.pop()

    def __init__(self, stream):
        super().__init__(stream)
        self.compose_stack = []

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result or key == '<<':
                raise ValueError('duplicate or non-string key')
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def provider_config(entry):
    entry = dict(entry)
    allowed = (set(ProtocolProviderConfig.__dataclass_fields__) - {'kind', 'api_key_env'}) | {'provider', 'api_key'}
    if set(entry) - allowed:
        raise ValueError('unknown provider option')
    alias = entry.pop('provider')
    if alias not in ALIASES:
        raise ValueError('unsupported protocol')
    secret = entry.pop('api_key', '')
    if not isinstance(secret, str) or secret and not re.fullmatch(r'\$\{[A-Z][A-Z0-9_]{0,100}\}', secret):
        raise InferenceError('LLM_SECRET_REFERENCE_REQUIRED')
    # No environment interpolation of model/URL/policy; credentials are resolved
    # only in the bounded transport, never held in configuration repr or logs.
    entry.setdefault('name', alias)
    if 'model' not in entry or 'endpoint' not in entry:
        raise ValueError('model and endpoint required')
    for field in ('purposes', 'response_path'):
        if field in entry:
            if not isinstance(entry[field], list):
                raise ValueError('list required')
            entry[field] = tuple(entry[field])
    cls = ProtocolProviderConfig if {'token_parameter', 'authentication'} & set(entry) else ProviderConfig
    config = cls(kind=ALIASES[alias], api_key_env=secret[2:-1] if secret else '', **entry)
    config.validate()
    return config


def load_settings(path):
    try:
        path = Path(path)
        if path.stat().st_size > 65536:
            raise ValueError('config too large')
        raw = yaml.load(path.read_text(encoding='utf-8'), Loader=ConfigLoader)
        if not isinstance(raw, dict):
            raise ValueError('mapping required')
        provider_fields = (set(ProtocolProviderConfig.__dataclass_fields__) - {'kind', 'api_key_env'}) | {'provider', 'api_key'}
        if set(raw) - provider_fields - {'timeout', 'gateway', 'fallbacks', 'routes'}:
            raise ValueError('unknown option')
        gateway = raw.get('gateway', {})
        if not isinstance(gateway, dict) or set(gateway) - (set(LLMSettings.__dataclass_fields__) -
                {'providers', 'routes', 'timeout_seconds'}):
            raise ValueError('invalid gateway')
        fallback = raw.get('fallbacks', [])
        if not isinstance(fallback, list) or len(fallback) > 2 or any(not isinstance(p, dict) for p in fallback):
            raise ValueError('invalid fallback')
        entries = [{k: v for k, v in raw.items() if k in provider_fields}] + fallback
        providers = tuple(provider_config(entry) for entry in entries)
        return LLMSettings(**gateway, timeout_seconds=raw.get('timeout', 10),
                           providers=providers, routes=raw.get('routes', {})).validate()
    except InferenceError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError, RecursionError):
        raise InferenceError('LLM_INVALID_CONFIG') from None
