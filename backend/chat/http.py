"""HTTP routes for /api/chat (docs/chat-protocol-v1.md section 2).

Identity, Host/Origin and body-size checks are done by the existing server
handler before these functions are called. Clients never send user ids,
risk levels or structured intents.
"""
import json
from backend.domain import Rejected, canonical

STATUS = {"CHAT_RATE_LIMITED": 429, "TRACE_NOT_FOUND": 404, "FEISHU_UNVERIFIED": 401, "FEISHU_DISABLED": 404, "SECURITY_OPERATOR_REQUIRED": 403, "HANDOFF_NOT_OPEN": 409, "SMS_RATE_LIMITED": 429, "DEMO_MODE_DISABLED": 403, "CHAT_ACCESS_DENIED": 403, "CHAT_NOT_FOUND": 404, "CHAT_BUSY": 409, "ACCOUNT_ACCESS_DENIED": 403,
          "BANKING_SECURITY_LOCKED": 423}


def status_for(code):
    return STATUS.get(code, 400)


def handle_get(handler, chat, path, user_id):
    parts = path.strip("/").split("/")
    if parts == ["api", "chat", "permissions"]:
        from backend.security.permission_matrix import public_matrix
        return handler.reply(200, public_matrix())
    if len(parts) == 4 and parts[:3] == ["api", "chat", "sessions"]:
        return handler.reply(200, chat.get(parts[3], user_id))
    if len(parts) == 6 and parts[:3] == ["api", "chat", "sessions"] and parts[4] == "evidence":
        return handler.reply(200, chat.evidence_bundle(parts[3], user_id, parts[5]))
    if len(parts) == 6 and parts[:3] == ["api", "chat", "sessions"] and parts[4] == "trace":
        return handler.reply(200, chat.trace(parts[3], user_id, parts[5]))
    return handler.reply(404, {"error": "NOT_FOUND"})


def handle_post(handler, chat, path, body, user_id):
    parts = path.strip("/").split("/")
    operator = handler.headers.get("X-TRUSTA-Operator", "")
    if parts == ["api", "chat", "operator", "handoffs"]:
        if body:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.operator_handoffs(operator))
    if len(parts) == 6 and parts[:4] == ["api", "chat", "operator", "handoffs"] and parts[5] == "reply":
        if set(body) - {"text", "close"} or type(body.get("close", False)) is not bool:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.operator_reply(operator, parts[4], body.get("text"), body.get("close", False)))
    if parts == ["api", "chat", "sessions"]:
        if set(body) - {"channel", "default_account_id"}:
            raise Rejected("INVALID_INPUT")
        return handler.reply(201, chat.create_session(user_id, body.get("channel", "app"), body.get("default_account_id", "acct-demo")))
    if len(parts) < 5 or parts[:3] != ["api", "chat", "sessions"]:
        return handler.reply(404, {"error": "NOT_FOUND"})
    session_id, operation = parts[3], parts[4]
    if len(parts) == 5 and operation == "messages":
        if set(body) - {"message_id", "revision", "text", "account_id", "reply_to"} or not isinstance(body.get("text"), str):
            raise Rejected("INVALID_INPUT")
        args = (session_id, user_id, body.get("message_id"), body.get("revision"), body["text"], body.get("account_id"))
        if "text/event-stream" in handler.headers.get("Accept", ""):
            return _stream(handler, chat, args)
        return handler.reply(200, chat.send(*args))
    if len(parts) == 7 and operation == "actions" and parts[6] == "decision":
        if set(body) - {"action_hash", "approve", "verification"} or type(body.get("approve")) is not bool:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.decide(session_id, user_id, parts[5], body.get("action_hash"), body["approve"],
                                              body.get("verification")))
    if len(parts) == 7 and operation == "actions" and parts[6] == "sms":
        if body:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.send_sms(session_id, user_id, parts[5]))
    if len(parts) == 5 and operation == "interrupt":
        if set(body) != {"revision"}:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.interrupt(session_id, user_id, body["revision"]))
    if len(parts) == 5 and operation == "risk-assessment":
        if set(body) != {"answers"}:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.submit_assessment(session_id, user_id, body["answers"]))
    if len(parts) == 6 and operation == "demo" and parts[5] == "advance":
        if set(body) != {"event_id"} or not isinstance(body["event_id"], str):
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.demo_advance(session_id, user_id, body["event_id"]))
    if len(parts) == 5 and operation == "close":
        if body:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.close(session_id, user_id))
    return handler.reply(404, {"error": "NOT_FOUND"})


def _stream(handler, chat, args):
    """Server-Sent Events. Validation errors before the first event still return a normal JSON error."""
    started = {"value": False}

    def write(name, data):
        if not started["value"]:
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.send_header("Connection", "close")
            handler.end_headers()
            started["value"] = True
        payload = canonical(data) if not isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        try:
            handler.wfile.write(f"event: {name}\ndata: {payload}\n\n".encode())
            handler.wfile.flush()
        except OSError:
            pass  # The turn still completes server side; the client recovers with GET /api/chat/sessions/{id}.

    try:
        chat.send(*args, on_event=write)
    except Rejected as exc:
        if not started["value"]:
            raise
        write("turn.failed", {"code": exc.code})
    handler.close_connection = True
