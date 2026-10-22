"""Defence-in-depth response headers shared by every HTTP reply (JSON, static and SSE)."""

HEADERS = (
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)


def security_headers(handler):
    for name, value in HEADERS:
        handler.send_header(name, value)
