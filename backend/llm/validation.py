"""Small closed JSON schema subset; no coercion, extra fields or non-finite numbers."""
import json
from backend.llm.contracts import InferenceError


def strict_json(text):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    def constant(value):
        raise ValueError()
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, RecursionError):
        raise InferenceError("LLM_INVALID_OUTPUT") from None


def validate(value, schema, depth=0):
    if depth > 12:
        raise InferenceError("LLM_INVALID_OUTPUT")
    kind = schema["type"]
    ok = {"object": type(value) is dict, "array": type(value) is list,
          "string": type(value) is str, "integer": type(value) is int,
          "boolean": type(value) is bool}[kind]
    if not ok or ("enum" in schema and value not in schema["enum"]):
        raise InferenceError("LLM_INVALID_OUTPUT")
    if kind == "object":
        properties = schema["properties"]
        if set(value) - set(properties) or set(schema["required"]) - set(value):
            raise InferenceError("LLM_INVALID_OUTPUT")
        for key, item in value.items():
            validate(item, properties[key], depth + 1)
    elif kind == "array":
        if len(value) > schema.get("maxItems", 30):
            raise InferenceError("LLM_INVALID_OUTPUT")
        for item in value:
            validate(item, schema["items"], depth + 1)
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 2000):
            raise InferenceError("LLM_INVALID_OUTPUT")
    elif kind == "integer":
        if not schema.get("minimum", 0) <= value <= schema.get("maximum", 10**12):
            raise InferenceError("LLM_INVALID_OUTPUT")
