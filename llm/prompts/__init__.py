"""Explicit, immutable prompt versions. Never rewrites existing banking prompts."""
from dataclasses import dataclass
import json
import re
from string import Formatter
import threading
from backend.llm.contracts import InferenceError
from llm.hashing import digest, snapshot, text_hash


@dataclass(frozen=True, init=False)
class PromptTemplate:
    name: str
    version: str
    _messages: str

    def __init__(self, name, version, messages):
        if any(not isinstance(s, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', s)
               for s in (name, version)):
            raise InferenceError('LLM_INVALID_PROMPT')
        if (not isinstance(messages, (tuple, list)) or not 1 <= len(messages) <= 100 or
                any(not isinstance(m, dict) or set(m) != {'role', 'content'} or
                    m['role'] not in ('system', 'user', 'assistant') or
                    not isinstance(m['content'], str) for m in messages)):
            raise InferenceError('LLM_INVALID_PROMPT')
        encoded = snapshot(list(messages))
        if len(encoded.encode()) > 100000:
            raise InferenceError('LLM_INVALID_PROMPT')
        try:
            for m in messages:
                for _, field, spec, conversion in Formatter().parse(m['content']):
                    if field is not None and (not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', field)
                                              or spec or conversion):
                        raise ValueError()
        except ValueError:
            raise InferenceError('LLM_INVALID_PROMPT') from None
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'version', version)
        object.__setattr__(self, '_messages', encoded)

    @property
    def template_hash(self):
        return digest({'name': self.name, 'version': self.version, 'messages': self.messages})

    @property
    def messages(self):
        return json.loads(self._messages)

    def render(self, variables):
        required = {field for m in self.messages
                    for _, field, _, _ in Formatter().parse(m['content']) if field is not None}
        if (not isinstance(variables, dict) or set(variables) != required or
                any(not isinstance(v, str) for v in variables.values()) or
                len(snapshot(variables).encode()) > 100000):
            raise InferenceError('LLM_PROMPT_VARIABLES')
        messages = [{'role': m['role'], 'content': m['content'].format_map(variables)}
                    for m in self.messages]
        encoded = snapshot(messages)
        if len(encoded.encode()) > 100000:
            raise InferenceError('LLM_PROMPT_TOO_LARGE')
        return RenderedPrompt(self.name, self.version, self.template_hash, encoded)


@dataclass(frozen=True)
class RenderedPrompt:
    name: str
    version: str
    template_hash: str
    _messages: str

    @property
    def messages(self):
        return tuple(json.loads(self._messages))

    @property
    def prompt_hash(self):
        return text_hash(self._messages)


class PromptRegistry:
    def __init__(self):
        self._templates = {}
        self._lock = threading.RLock()

    def register(self, template):
        if not isinstance(template, PromptTemplate):
            raise InferenceError('LLM_INVALID_PROMPT')
        with self._lock:
            key = (template.name, template.version)
            current = self._templates.get(key)
            if current and current.template_hash != template.template_hash:
                raise InferenceError('LLM_PROMPT_VERSION_CONFLICT')
            self._templates[key] = template
        return template.template_hash

    def get(self, name, version):
        with self._lock:
            try:
                return self._templates[(name, version)]
            except (KeyError, TypeError):
                raise InferenceError('LLM_PROMPT_NOT_FOUND') from None

    def load(self, path):
        """Load an operator-owned version file using the strict local YAML loader."""
        from pathlib import Path
        import yaml
        from llm.configuration import ConfigLoader
        try:
            path = Path(path)
            if path.stat().st_size > 100000:
                raise ValueError()
            data = yaml.load(path.read_text(encoding='utf-8'), Loader=ConfigLoader)
            if not isinstance(data, dict) or set(data) != {'name', 'version', 'messages'}:
                raise ValueError()
            template = PromptTemplate(**data)
            self.register(template)
            return template
        except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError, RecursionError):
            raise InferenceError('LLM_INVALID_PROMPT') from None
