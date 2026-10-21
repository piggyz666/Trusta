"""Canonical snapshots for LLM metadata; no dependency on business audit state."""
import hashlib
import json
from backend.llm.contracts import InferenceError


def snapshot(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False)
        encoded.encode('utf-8')
        return encoded
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise InferenceError('LLM_INVALID_INPUT') from None


def digest(value):
    return hashlib.sha256(snapshot(value).encode()).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()
