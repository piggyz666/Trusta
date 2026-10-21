"""Tool proposals only. This module has no executor or authorization capability."""
from dataclasses import dataclass
import json
import re
from backend.llm.contracts import InferenceError
from llm.hashing import snapshot
from llm.providers.common import check_schema


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}', value)


def embedded_schema(schema, prefix):
    # Local refs in a schema retain their meaning when placed inside an envelope.
    if isinstance(schema, dict):
        return {k: ('#/' + prefix + v[2:] if k in {'$ref', '$dynamicRef'} else
                    embedded_schema(v, prefix)) for k, v in schema.items()}
    if isinstance(schema, list):
        return [embedded_schema(v, prefix) for v in schema]
    return schema


@dataclass(frozen=True, init=False)
class ToolDefinition:
    name: str
    permission_scope: str
    _schema: str

    def __init__(self, name, arguments_schema, permission_scope):
        if not identifier(name) or not identifier(permission_scope):
            raise InferenceError('LLM_INVALID_TOOL_CONTRACT')
        check_schema(arguments_schema)
        if arguments_schema.get('type') != 'object':
            raise InferenceError('LLM_INVALID_TOOL_CONTRACT')
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'permission_scope', permission_scope)
        object.__setattr__(self, '_schema', snapshot(arguments_schema))

    @property
    def arguments_schema(self):
        return json.loads(self._schema)


@dataclass(frozen=True, init=False)
class ToolCall:
    name: str
    permission_scope: str
    _arguments: str

    def __init__(self, name, arguments, permission_scope):
        if not identifier(name) or not identifier(permission_scope) or not isinstance(arguments, dict):
            raise InferenceError('LLM_INVALID_TOOL_REQUEST')
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'permission_scope', permission_scope)
        object.__setattr__(self, '_arguments', snapshot(arguments))

    @property
    def arguments(self):
        return json.loads(self._arguments)

    def as_dict(self):
        return {'name': self.name, 'arguments': self.arguments, 'permission_scope': self.permission_scope}


def tool_envelope(data_schema, definitions):
    check_schema(data_schema)
    definitions = tuple(definitions)
    if (not 1 <= len(definitions) <= 32 or any(not isinstance(t, ToolDefinition) for t in definitions)
            or len({t.name for t in definitions}) != len(definitions)):
        raise InferenceError('LLM_INVALID_TOOL_CONTRACT')
    variants = []
    for index, tool in enumerate(definitions):
        variants.append({'type': 'object', 'additionalProperties': False,
            'required': ['name', 'arguments', 'permission_scope'], 'properties': {
                'name': {'const': tool.name}, 'permission_scope': {'const': tool.permission_scope},
                'arguments': embedded_schema(tool.arguments_schema,
                    f'properties/tool_requests/items/oneOf/{index}/properties/arguments/')}})
    result = {'type': 'object', 'additionalProperties': False, 'required': ['data', 'tool_requests'],
        'properties': {'data': embedded_schema(data_schema, 'properties/data/'),
                       'tool_requests': {'type': 'array', 'maxItems': 16, 'items': {'oneOf': variants}}}}
    check_schema(result)
    return result
