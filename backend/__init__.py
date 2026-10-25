"""TRUSTA V1 local sandbox."""
import sys as _sys


def _utf8_console():
    """Chinese status lines must never crash the program on a console that isn't UTF-8 (e.g. Windows set to an
    English code page, or output redirected to a file). Re-encode stdout/stderr as UTF-8 and replace anything
    unprintable instead of raising."""
    for stream in (_sys.stdout, _sys.stderr):
        try:
            if stream is not None and (stream.encoding or "").lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_utf8_console()
