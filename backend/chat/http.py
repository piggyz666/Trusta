"""HTTP routes for /api/chat (docs/chat-protocol-v1.md section 2).

Identity, Host/Origin and body-size checks are done by the existing server
handler before these functions are called. Clients never send user ids,
risk levels or structured intents.
"""
import json
from backend.domain import Rejected, canonical

STATUS = {"CHAT_RATE_LIMITED": 429, "TRACE_NOT_FOUND": 404, "FEISHU_UNVERIFIED": 401, "FEISHU_DISABLED": 404, "SECURITY_OPERATOR_REQUIRED": 403, "HANDOFF_NOT_OPEN": 409, "SMS_RATE_LIMITED": 429, "DEMO_MODE_DISABLED": 403, "CHAT_ACCESS_DENIED": 403, "CHAT_NOT_FOUND": 404, "CHAT_BUSY": 409, "ACCOUNT_ACCESS_DENIED": 403,
          "BANKING_SECURITY_LOCKED": 423, "SETTINGS_LOCAL_ONLY": 403, "FEATURE_LOCKED": 409, "FEATURE_UNAVAILABLE": 409, "AUTOMATION_NOT_FOUND": 404, "PROTECTION_NOT_ACTIVE": 409, "ACCOUNT_PROTECTION_ACTIVE": 423}


def status_for(code):
    return STATUS.get(code, 400)


def _local(handler):
    """True only for a request from this computer with the master token (not a paired phone)."""
    return bool(getattr(handler, "is_local", lambda: False)()) and not getattr(handler, "via_device", True)


def market(chat):
    """One quote cache per running server (shared by every customer: market data is not personal)."""
    if getattr(chat, "market", None) is None:
        from backend.market import MarketQuotes
        chat.market = MarketQuotes(chat.s.db)
    return chat.market


def handle_get(handler, chat, path, user_id):
    parts = path.strip("/").split("/")
    if parts==["api","chat","todos"]:
        from backend.banking.todos import view
        return handler.reply(200,view(chat,user_id))
    if parts==["api","chat","advanced"]:
        from backend.banking.advanced import Advanced
        return handler.reply(200,Advanced(chat).view(user_id))
    if len(parts)==3 and parts[:2]==['api','chat'] and parts[2] in {'extensions','gold-savings','support'}:
        from backend.banking.extensions import Extensions,GoldSavings
        from backend.banking.member_support import Support
        cls={'extensions':Extensions,'gold-savings':GoldSavings,'support':Support}[parts[2]]
        return handler.reply(200,cls(chat).view(user_id))
    if parts == ['api','chat','plan-center']:
        from backend.chat.plan_bridge import PlanBridge
        return handler.reply(200,PlanBridge(chat).view(user_id))
    if parts in [["api","chat","profile"],["api","chat","plans"]]:
        from backend.banking.personal import Personal
        p=Personal(chat.s)
        return handler.reply(200,p.profile(user_id) if parts[-1]=="profile" else p.plans(user_id))
    if parts == ["api","chat","finance"]:
        from backend.banking.finance import Finance
        return handler.reply(200,Finance(chat).view(user_id))
    if parts == ["api","chat","retail"]:
        from backend.banking.retail import Retail
        return handler.reply(200,Retail(chat).view(user_id)|{"catalog":[]})
    if parts == ["api", "chat", "permissions"]:
        from backend.security.permission_matrix import public_matrix
        return handler.reply(200, public_matrix())
    if parts == ["api", "chat", "capabilities"]:
        return handler.reply(200, chat.capabilities.public(user_id))
    if parts == ["api", "chat", "sessions"]:
        return handler.reply(200, {"sessions": chat.store.list(user_id)})
    if parts == ["api", "chat", "activity"]:
        from urllib.parse import parse_qs, urlparse
        from backend.chat.activity import ActivityFeed
        query = parse_qs(urlparse(handler.path).query)
        kind = (query.get("kind") or [None])[0]
        if kind not in {None, "transfer", "card", "wealth", "wish", "subscription", "payee", "income", "automation", "retail", "credit"}:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, {"items": ActivityFeed(chat).list(user_id, kind)})
    if parts == ["api", "chat", "overview"]:
        from backend.chat.activity import ActivityFeed
        return handler.reply(200, ActivityFeed(chat).overview(user_id))
    if parts == ["api", "chat", "bills"]:
        from urllib.parse import parse_qs, urlparse
        from backend.chat.pages import BillsPage
        from backend.chat.query_consent import card as query_card
        # 订阅 / 代扣查询是黄色操作：未确认时只返回确认说明，确认后（confirm=1）才读取并显示
        if not chat.query_consent.active(user_id, "subscriptions"):
            if (parse_qs(urlparse(handler.path).query).get("confirm") or ["0"])[0] != "1":
                return handler.reply(200, {"requires_confirmation": True, "confirm": query_card("q-page", "subscriptions")})
            chat.query_consent.grant(user_id, "subscriptions", {"page": "bills"})
        return handler.reply(200, BillsPage(chat).view(user_id))
    if parts == ["api", "chat", "wealth"]:
        from backend.chat.pages import WealthPage
        return handler.reply(200, WealthPage(chat, market(chat)).view(user_id))
    if parts == ["api", "chat", "market"]:
        from urllib.parse import parse_qs, urlparse
        force = (parse_qs(urlparse(handler.path).query).get("refresh") or ["0"])[0] == "1"
        return handler.reply(200, market(chat).view(force))
    if parts == ["api", "chat", "settings"]:
        from backend.chat.settings_api import SettingsAPI
        return handler.reply(200, SettingsAPI(chat).read(user_id, _local(handler), handler.server.server_port))
    if parts == ["api", "chat", "llm-usage"]:
        usage = chat.store.llm_usage(user_id)
        return handler.reply(200, usage | {"daily_call_limit": chat.LLM_DAILY_CALLS})
    if len(parts) == 4 and parts[:3] == ["api", "chat", "sessions"]:
        return handler.reply(200, chat.get(parts[3], user_id))
    if len(parts) == 6 and parts[:3] == ["api", "chat", "sessions"] and parts[4] == "evidence":
        return handler.reply(200, chat.evidence_bundle(parts[3], user_id, parts[5]))
    if len(parts) == 6 and parts[:3] == ["api", "chat", "sessions"] and parts[4] == "trace":
        return handler.reply(200, chat.trace(parts[3], user_id, parts[5]))
    return handler.reply(404, {"error": "NOT_FOUND"})


def handle_post(handler, chat, path, body, user_id):
    parts = path.strip("/").split("/")
    if parts == ['api','chat','plans','open']:
        from backend.chat.plan_bridge import PlanBridge
        return handler.reply(200,PlanBridge(chat).open(user_id,body))
    if parts == ['api','chat','plans','release']:
        from backend.chat.plan_bridge import PlanBridge
        return handler.reply(200,PlanBridge(chat).release(user_id,body))
    if parts == ['api','chat','plans','prepare']:
        from backend.chat.plan_bridge import PlanBridge
        return handler.reply(200,PlanBridge(chat).prepare(user_id,body))
    if len(parts)==4 and parts[:2]==['api','chat'] and parts[2] in {'extensions','gold-savings','support'}:
        from backend.banking.extensions import Extensions,GoldSavings
        from backend.banking.member_support import Support
        cls,allowed={'extensions':(Extensions,{'create'}),'gold-savings':(GoldSavings,{'create','update','prepare'}),'support':(Support,{'create','update'})}[parts[2]]
        if parts[3] not in allowed:raise Rejected('INVALID_INPUT')
        return handler.reply(200,getattr(cls(chat),parts[3])(user_id,body))
    operator = handler.headers.get("X-TRUSTA-Operator", "")
    if parts[:3]==["api","chat","finance"]:
        from backend.banking.finance import Finance
        f=Finance(chat)
        methods={'create':f.create,'proposal':f.propose,'decide':f.decide,'sms':f.sms,'webauthn':f.webauthn,'archive':f.archive}
        if len(parts)!=4 or parts[-1] not in methods:raise Rejected('INVALID_INPUT')
        return handler.reply(200,methods[parts[-1]](user_id,body))
    if parts in [["api","chat","profile"],["api","chat","plans"],["api","chat","plans","update"]]:
        from backend.banking.personal import Personal
        p=Personal(chat.s)
        return handler.reply(200,p.save_profile(user_id,body) if parts[-1]=="profile" else p.update(user_id,body) if parts[-1]=="update" else p.create(user_id,body))
    if parts[:3]==["api","chat","retail"]:
        from backend.banking.retail import Retail
        r=Retail(chat)
        if parts==["api","chat","retail","design"]:return handler.reply(200,r.design(user_id,body))
        if parts==["api","chat","retail","orders"]:raise Rejected("FEATURE_UNAVAILABLE")
        if parts==["api","chat","retail","proposal"] and set(body)=={"order_id","refund"}:
            if body["refund"] is not True:raise Rejected("FEATURE_UNAVAILABLE")
            return handler.reply(200,r.propose(user_id,body["order_id"],True))
        if parts==["api","chat","retail","decide"]:
            if body.get("approve"):
                task=chat.s.get_task(body.get("task_id"),user_id)
                if any(a["action"]["action_type"]=="RETAIL_PAY" for a in task.get("action_records",[])):raise Rejected("FEATURE_UNAVAILABLE")
            return handler.reply(200,r.decide(user_id,body))
        raise Rejected("INVALID_INPUT")
    if parts == ["api", "chat", "operator", "handoffs"]:
        if body:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.operator_handoffs(operator))
    if len(parts) == 6 and parts[:4] == ["api", "chat", "operator", "handoffs"] and parts[5] == "reply":
        if set(body) - {"text", "close"} or type(body.get("close", False)) is not bool:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.operator_reply(operator, parts[4], body.get("text"), body.get("close", False)))
    if parts == ["api", "chat", "bills", "mark"]:
        if set(body) != {"key", "state"}:
            raise Rejected("INVALID_INPUT")
        from backend.chat.pages import BillsPage
        return handler.reply(200, BillsPage(chat).mark(user_id, body["key"], body["state"]))
    if parts == ["api", "chat", "bills", "remove"]:
        if set(body) != {"id"} or not isinstance(body["id"], str):
            raise Rejected("INVALID_INPUT")
        from backend.chat.pages import BillsPage
        from backend.chat.subscriptions_chat import remove_user_subscription
        if not remove_user_subscription(chat.s.db, user_id, body["id"]):
            raise Rejected("NOT_FOUND")
        return handler.reply(200, BillsPage(chat).view(user_id))
    if parts == ["api", "chat", "capabilities", "probe"]:
        if set(body) - {"targets"}:
            raise Rejected("INVALID_INPUT")
        targets = body.get("targets") or ["llm", "bank", "feishu"]
        if not isinstance(targets, list) or set(targets) - {"llm", "bank", "feishu"}:
            raise Rejected("INVALID_INPUT")
        results = {t: getattr(chat.capabilities, "probe_" + t)() for t in targets}
        return handler.reply(200, {"probes": {k: {x: y for x, y in v.items() if x != "detail"} for k, v in results.items()},
                                   "capabilities": chat.capabilities.public()})
    if parts == ["api", "chat", "operator", "protection", "lift"]:
        if set(body) != {"owner", "reason"} or not isinstance(body["owner"], str):
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.s.safety.lift_protection(body["owner"], operator, body["reason"]))
    if parts == ["api", "chat", "protection"]:
        # Settings page: the customer turns on the cooling-off period without a conversation. Can only extend.
        if set(body) - {"hours"} or type(body.get("hours", 24)) is not int:
            raise Rejected("INVALID_INPUT")
        state = chat.s.safety.enable_protection(user_id, body.get("hours", 24), "USER_REQUEST", "settings")
        return handler.reply(200, {"until": state["until"], "reason": state["reason"]})
    if len(parts) == 5 and parts[:3] == ["api", "chat", "automations"] and parts[4] == "cancel":
        if body:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, {"cancelled": chat.s.automation.cancel(user_id, parts[3])})
    if parts == ["api", "chat", "capabilities", "toggle"]:
        if set(body) != {"feature", "enabled"}:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.capabilities.set_pref(user_id, body["feature"], body["enabled"]))
    if parts == ["api", "chat", "settings", "llm"]:
        if set(body) - {"model", "api_key"}:
            raise Rejected("INVALID_INPUT")
        from backend.chat.settings_api import SettingsAPI
        return handler.reply(200, SettingsAPI(chat).update_llm(_local(handler), body.get("model"), body.get("api_key")))
    if parts == ["api", "chat", "settings", "access"]:
        if set(body) - {"mode", "public_host"}:
            raise Rejected("INVALID_INPUT")
        from backend.chat.settings_api import SettingsAPI
        return handler.reply(200, SettingsAPI(chat).update_access(_local(handler), body.get("mode"), body.get("public_host")))
    if parts == ["api", "chat", "sessions"]:
        if set(body) - {"channel", "default_account_id"}:
            raise Rejected("INVALID_INPUT")
        return handler.reply(201, chat.create_session(user_id, body.get("channel", "app"), body.get("default_account_id")))
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
    if len(parts) == 7 and operation == "actions" and parts[6] == "webauthn":
        if body:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.webauthn_options(session_id, user_id, parts[5]))
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
    if len(parts) == 7 and operation == "automations" and parts[6] == "decision":
        if set(body) != {"bundle_hash", "approve"} or type(body["approve"]) is not bool:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.decide_rule(session_id, user_id, parts[5], body["bundle_hash"], body["approve"]))
    if len(parts) == 7 and operation == "queries" and parts[6] == "decision":
        if set(body) != {"approve"} or type(body["approve"]) is not bool:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.decide_query(session_id, user_id, parts[5], body["approve"]))
    if len(parts) == 5 and operation == "protection":
        if set(body) - {"hours"} or type(body.get("hours", 24)) is not int:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.protect(session_id, user_id, body.get("hours", 24)))
    if len(parts) == 6 and operation == "demo" and parts[5] == "income":
        if set(body) - {"amount_minor", "payer", "memo"} or type(body.get("amount_minor")) is not int:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.demo_income(session_id, user_id, body["amount_minor"], body.get("payer", "演示公司"), body.get("memo", "")))
    if len(parts) == 6 and operation == "demo" and parts[5] == "purchase":
        if set(body) != {"merchant", "category", "amount_minor"} or type(body["amount_minor"]) is not int:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.demo_purchase(session_id, user_id, body["merchant"], body["category"], body["amount_minor"]))
    if len(parts) == 6 and operation == "demo" and parts[5] == "fire-rule":
        if set(body) != {"rule_id"} or not isinstance(body["rule_id"], str):
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.demo_fire_rule(session_id, user_id, body["rule_id"]))
    if len(parts) == 5 and operation == "rename":
        if set(body) != {"title"}:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.store.rename(session_id, user_id, body["title"]))
    if len(parts) == 5 and operation == "delete":
        if body:
            raise Rejected("INVALID_INPUT")
        return handler.reply(200, chat.hide(session_id, user_id))
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
            from backend.security.http_headers import security_headers
            security_headers(handler)
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
