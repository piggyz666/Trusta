"""Explicit process-local refresh of configured Windows credential references.

Never enumerates or exports the registry, rewrites .env, prints values, or
overwrites a process credential. Call only at operator preflight/startup.
"""
import os
import re
from backend.llm.contracts import InferenceError


def _windows_user_value(name):
    if os.name != 'nt':
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            value, kind = winreg.QueryValueEx(key, name)
        return value if kind == winreg.REG_SZ and isinstance(value, str) else None
    except OSError:
        return None


def refresh_credentials(names):
    names = tuple(names)
    if len(names) > 10 or any(not isinstance(n, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,100}', n) for n in names):
        raise InferenceError('LLM_INVALID_CONFIG')
    status = {}
    for name in names:
        source = 'process'
        value = os.getenv(name)
        if not value:
            value = _windows_user_value(name)
            source = 'windows_user' if value else 'missing'
            if value and '\r' not in value and '\n' not in value:
                os.environ[name] = value
        status[name] = {'recognized': bool(os.getenv(name)), 'source': source}
    return status
