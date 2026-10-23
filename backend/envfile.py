"""Local .env read/write (shared by the setup wizard and the settings page). Unmanaged lines are kept verbatim."""
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / ".env"


def read_env(path=ENV):
    values, lines = {}, []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values, lines


def write_env(updates, path=ENV):
    """updates: {KEY: value or None}; None removes the key."""
    if any(not isinstance(k, str) or not k.isupper() or (v is not None and ("\n" in str(v) or "\r" in str(v))) for k, v in updates.items()):
        raise ValueError("invalid .env update")
    _, lines = read_env(path)
    out, seen = [], set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in updates:
            seen.add(key)
            if updates[key] is not None:
                out.append(f"{key}={updates[key]}")
            continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen and value is not None:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        from backend.security.local_files import protect_secret
        protect_secret(path)
    except Exception:
        pass
