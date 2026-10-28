"""Feishu (Lark) bot channel: the same ChatService as the APP.

Two transports, same event handling:
* longconn (default): an outbound WebSocket opened with the app's own credentials (scripts/feishu_worker.py, official
  SDK). No public URL, no tunnel, no server -- like OpenClaw. Events arrive already authenticated by that connection.
* webhook: Feishu POSTs to /api/channels/feishu/webhook (needs a public URL, e.g. a tunnel).

Security model
* Every webhook callback is verified (verification token, and X-Lark-Signature when an encrypt key is set);
  encrypted payloads are decrypted with AES-256-CBC as specified by the open platform.
* A Feishu user acts as a TRUSTA customer only through an explicit binding: a one-time 6-digit code generated on the
  customer's computer and sent to the bot as “绑定 123456” (or the FEISHU_USER_BINDINGS list for operators).
* YELLOW actions can be confirmed with card buttons by the bound user. RED actions are never completed
  in IM: the card points to the APP for TOTP / SMS verification (an IM account alone is not a second factor).
* Event ids are de-duplicated (Feishu retries), and replies are sent asynchronously so the webhook
  answers within the platform's 3 s window.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler
from backend.domain import Rejected, uid

API = "https://open.feishu.cn/open-apis"


# What the app needs on 飞书开放平台, shown by the setup wizard, the settings page and the docs (one source).
SCOPES = (
    ("im:message.p2p_msg:readonly", "读取用户发给机器人的单聊消息", "收消息"),
    ("im:message:send_as_bot", "以应用的身份发消息", "回消息、发确认卡片"),
)
SETUP_STEPS = (
    "飞书开放平台 → 创建企业自建应用 → 添加“机器人”能力",
    "权限管理 → 开通 " + "、".join(s for s, _, _ in SCOPES),
    "事件与回调 → 事件配置：订阅方式选“使用长连接接收事件”，添加事件 im.message.receive_v1",
    "事件与回调 → 回调配置：订阅方式选“使用长连接接收回调”，添加卡片回调 card.action.trigger",
    "版本管理与发布 → 可用范围包含要使用的人 → 发布版本（权限和事件在发布后才生效）",
    "凭证与基础信息 → 复制 App ID（cli_ 开头）和 App Secret",
)
APP_ID_OK = lambda v: isinstance(v, str) and v.startswith("cli_") and 10 <= len(v) <= 64 and v.isascii() and v.replace("_", "").isalnum()
APP_SECRET_OK = lambda v: isinstance(v, str) and 20 <= len(v) <= 128 and v.isascii() and v.isprintable() and " " not in v


class FeishuConfig:
    def __init__(self, app_id="", app_secret="", verification_token="", encrypt_key="", bindings=None, app_url="", mode=None):
        self.app_id, self.app_secret = app_id, app_secret
        self.mode = (mode or ("webhook" if verification_token else "longconn")).lower()
        self.verification_token, self.encrypt_key = verification_token, encrypt_key
        self.bindings = dict(bindings or {})
        self.app_url = app_url

    @classmethod
    def from_env(cls):
        bindings = {}
        for pair in filter(None, os.environ.get("FEISHU_USER_BINDINGS", "").split(",")):
            open_id, _, owner = pair.partition(":")
            if open_id.strip() and owner.strip():
                bindings[open_id.strip()] = owner.strip()
        return cls(os.environ.get("FEISHU_APP_ID", ""), os.environ.get("FEISHU_APP_SECRET", ""),
                   os.environ.get("FEISHU_VERIFICATION_TOKEN", ""), os.environ.get("FEISHU_ENCRYPT_KEY", ""), bindings,
                   os.environ.get("TRUSTA_APP_URL", ""), os.environ.get("FEISHU_MODE") or None)

    @property
    def enabled(self):
        if self.mode == "longconn":
            return bool(self.app_id and self.app_secret)
        return bool(self.verification_token)


class FeishuSender:
    """Outbound Open API calls (tenant token cached)."""
    def __init__(self, config):
        self.config = config
        self._token, self._token_expiry = None, 0
        self._lock = threading.Lock()

    def _post(self, path, body, token=None):
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(API + path, data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method="POST")
        proxy = os.environ.get("TRUSTA_CHAT_LLM_PROXY", "")
        with build_opener(ProxyHandler({"https": proxy} if proxy else {})).open(request, timeout=10) as response:
            return json.loads(response.read(65536))

    def token(self):
        with self._lock:
            if self._token and time.time() < self._token_expiry - 60:
                return self._token
            result = self._post("/auth/v3/tenant_access_token/internal", {"app_id": self.config.app_id, "app_secret": self.config.app_secret})
            if result.get("code") != 0:
                raise Rejected("FEISHU_AUTH_FAILED", f"飞书返回 {result.get('code')}：{str(result.get('msg', ''))[:120]}")
            self._token, self._token_expiry = result["tenant_access_token"], time.time() + int(result.get("expire", 3600))
            return self._token

    def send(self, chat_id, msg_type, content):
        result = self._post("/im/v1/messages?receive_id_type=chat_id",
                            {"receive_id": chat_id, "msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False)},
                            self.token())
        if result.get("code") != 0:
            raise Rejected("FEISHU_SEND_FAILED")
        return result


def decrypt(encrypt_key, encrypted):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = hashlib.sha256(encrypt_key.encode()).digest()
    raw = base64.b64decode(encrypted)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(raw[:16])).decryptor()
    padded = decryptor.update(raw[16:]) + decryptor.finalize()
    return json.loads(padded[: -padded[-1]].decode())


def encrypt(encrypt_key, payload, iv=None):
    """Used by tests to build platform-shaped encrypted callbacks."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = iv or os.urandom(16)
    data = json.dumps(payload).encode()
    pad = 16 - len(data) % 16
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + encryptor.update(data + bytes([pad]) * pad) + encryptor.finalize()).decode()


class FeishuChannel:
    def __init__(self, chat, config=None, sender=None, synchronous=False):
        self.chat, self.s = chat, chat.s
        self.config = config or FeishuConfig.from_env()
        self.sender = sender or FeishuSender(self.config)
        self.synchronous = synchronous
        with self.s.db.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS channel_sessions(channel TEXT NOT NULL, external_id TEXT NOT NULL, "
                         "owner TEXT NOT NULL, session_id TEXT NOT NULL, PRIMARY KEY(channel, external_id))")
            conn.execute("CREATE TABLE IF NOT EXISTS channel_events(channel TEXT NOT NULL, event_id TEXT NOT NULL, "
                         "received_at REAL NOT NULL, PRIMARY KEY(channel, event_id))")

    # ---- verification ------------------------------------------------------------------------
    def verify(self, headers, raw):
        """Returns the decoded payload or raises FEISHU_UNVERIFIED."""
        if not self.config.enabled:
            raise Rejected("FEISHU_DISABLED")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise Rejected("FEISHU_UNVERIFIED") from None
        if self.config.encrypt_key:
            timestamp, nonce = headers.get("X-Lark-Request-Timestamp", ""), headers.get("X-Lark-Request-Nonce", "")
            signature = headers.get("X-Lark-Signature", "")
            expected = hashlib.sha256((timestamp + nonce + self.config.encrypt_key).encode() + raw).hexdigest()
            # url_verification may arrive unsigned; everything else must carry a valid signature.
            if signature and not hmac.compare_digest(signature, expected):
                raise Rejected("FEISHU_UNVERIFIED")
            if "encrypt" in payload:
                try:
                    payload = decrypt(self.config.encrypt_key, payload["encrypt"])
                except Exception:
                    raise Rejected("FEISHU_UNVERIFIED") from None
            if not signature and payload.get("type") != "url_verification":
                raise Rejected("FEISHU_UNVERIFIED")
        token = payload.get("token") or (payload.get("header") or {}).get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token, self.config.verification_token):
            raise Rejected("FEISHU_UNVERIFIED")
        return payload

    # ---- entry -------------------------------------------------------------------------------
    def handle(self, headers, raw):
        payload = self.verify(headers, raw)
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}
        return self.process(payload)

    def process(self, payload):
        """Shared by both transports once the payload is authenticated (webhook signature / long-connection session)."""
        header = payload.get("header") or {}
        event_type, event_id = header.get("event_type"), header.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise Rejected("FEISHU_UNVERIFIED")
        try:
            with self.s.db.transaction() as conn:
                conn.execute("INSERT INTO channel_events VALUES('feishu',?,?)", (event_id, time.time()))
        except Exception:
            return {"code": 0, "duplicate": True}  # platform retry of an event we already accepted
        event = payload.get("event") or {}
        if event_type == "im.message.receive_v1":
            self._dispatch(self._on_message, event)
            return {"code": 0}
        if event_type == "card.action.trigger":
            return self._on_card_action(event)
        return {"code": 0, "ignored": event_type}

    def _dispatch(self, fn, *args):
        if self.synchronous:
            return fn(*args)
        threading.Thread(target=self._safe, args=(fn,) + args, daemon=True).start()

    def _safe(self, fn, *args):
        try:
            fn(*args)
        except Exception as exc:  # never leak exception text to the platform or logs
            self.s.audit.append(None, None, "FEISHU_DISPATCH_FAILED", "channel", {"code": getattr(exc, "code", "ERROR")})

    # ---- messages ----------------------------------------------------------------------------
    def _access(self):
        from backend.access import AccessControl
        access = getattr(self.s, "access", None)
        if access is None:
            access = self.s.access = AccessControl(self.s)  # same TRUSTA_ACCESS_MODE / TRUSTA_PUBLIC_HOST as the server
        return access

    def app_link(self, session_id=None):
        """Where "去 APP 验证" goes: an explicit TRUSTA_APP_URL, else the address a phone can actually reach
        (tunnel → public host, LAN → this computer's LAN address), opening the very conversation that holds the card."""
        base = self.config.app_url
        if not base:
            urls = self._access().urls(int(os.environ.get("TRUSTA_PORT", "8000")))
            base = urls.get("public") or urls.get("lan") or urls["local"]
        base = base if base.endswith("/") else base + "/"
        return base + (f"?s={session_id}" if session_id else "")

    def _owner(self, open_id):
        return self.config.bindings.get(open_id) or (self._access().channel_owner("feishu", open_id) if open_id else None)

    def _session(self, chat_id, owner):
        row = self.s.db.one("SELECT owner, session_id FROM channel_sessions WHERE channel='feishu' AND external_id=?", (chat_id,))
        if row and row["owner"] == owner:
            try:
                view = self.chat.get(row["session_id"], owner)
                if view["state"] != "LOCKED" and not view.get("closed"):
                    return view
            except Rejected:
                pass
        view = self.chat.create_session(owner, "feishu")
        with self.s.db.transaction() as conn:
            conn.execute("INSERT INTO channel_sessions VALUES('feishu',?,?,?) ON CONFLICT(channel, external_id) DO UPDATE SET "
                         "owner=excluded.owner, session_id=excluded.session_id", (chat_id, owner, view["session_id"]))
        return view

    def _on_message(self, event):
        message, sender = event.get("message") or {}, (event.get("sender") or {}).get("sender_id") or {}
        chat_id, open_id = message.get("chat_id"), sender.get("open_id")
        if not chat_id:
            return
        owner = self._owner(open_id)
        text = ""
        if message.get("message_type") == "text":
            try:
                text = json.loads(message.get("content") or "{}").get("text", "")
            except ValueError:
                text = ""
        bind = re.fullmatch(r"\s*绑定\s*([0-9]{6})\s*", " ".join(p for p in text.split() if not p.startswith("@_user")))
        if bind and open_id and message.get("chat_type", "p2p") == "p2p":
            try:
                self._access().bind_channel("feishu", open_id, bind.group(1))
                self.sender.send(chat_id, "text", {"text": "绑定成功，现在可以直接在这里和我对话了。转账等操作仍需确认，高风险操作请回到 APP 验证。"})
            except Rejected:
                self.sender.send(chat_id, "text", {"text": "绑定码无效或已过期，请在电脑上重新生成（5 分钟内有效）。"})
            return
        if not owner:
            self.sender.send(chat_id, "text", {"text": "你还没有绑定 TRUSTA 账户：请在电脑上的 TRUSTA 页面点“绑定飞书”（或运行 python -m scripts.pair --feishu）"
                                                       "获取 6 位绑定码，然后在这里私聊发送“绑定 123456”。"})
            return
        if message.get("message_type") != "text":
            self.sender.send(chat_id, "text", {"text": "目前只支持文字消息。"})
            return
        text = " ".join(part for part in text.split() if not part.startswith("@_user"))[:500].strip()
        if not text:
            return
        view = self._session(chat_id, owner)
        turn = self.chat.send(view["session_id"], owner, "fs-" + (message.get("message_id") or uid())[-40:], view["revision"], text)
        self._reply(chat_id, view["session_id"], turn)

    def _reply(self, chat_id, session_id, turn):
        self.sender.send(chat_id, "text", {"text": turn["reply"]["text"]})
        for card in turn["reply"]["cards"]:
            if card["type"] == "confirm":
                self.sender.send(chat_id, "interactive", self.confirm_card(session_id, card))
            elif card["type"] == "automation_confirm":
                self.sender.send(chat_id, "interactive", self.rule_card(session_id, card))

    def rule_card(self, session_id, card):
        lines = "\n".join(f"**{r['name']}**：{r['trigger']}，" + "、".join(r["actions"]) for r in card["rules"])
        lines += "\n\n" + "；".join(card["notes"])
        value = {"session_id": session_id, "bundle_id": card["bundle_id"], "bundle_hash": card["bundle_hash"]}
        return {"config": {"wide_screen_mode": True},
                "header": {"template": "blue", "title": {"tag": "plain_text", "content": card["title"]}},
                "elements": [{"tag": "markdown", "content": lines}, {"tag": "action", "actions": [
                    {"tag": "button", "type": "primary", "text": {"tag": "plain_text", "content": "启用规则"}, "value": value | {"approve": True}},
                    {"tag": "button", "type": "default", "text": {"tag": "plain_text", "content": "不用了"}, "value": value | {"approve": False}}]}]}

    def confirm_card(self, session_id, card):
        red = card["risk_level"] == "RED"
        lines = "\n".join(f"**{f['label']}**：{f['value']}" for f in card["fields"])
        if card.get("risk_reasons"):
            lines += "\n\n" + "；".join(card["risk_reasons"])
        elements = [{"tag": "markdown", "content": lines}]
        if red:
            elements.append({"tag": "markdown", "content": "🔒 该操作属于高风险操作，需要在 APP 中完成动态码 / 短信验证，飞书内无法直接确认。"})
            elements.append({"tag": "action", "actions": [{"tag": "button", "type": "primary", "text": {"tag": "plain_text", "content": "去 APP 验证"},
                                                           "url": self.app_link(session_id)}]})
        else:
            value = {"session_id": session_id, "action_id": card["action_id"], "action_hash": card["action_hash"]}
            elements.append({"tag": "action", "actions": [
                {"tag": "button", "type": "primary", "text": {"tag": "plain_text", "content": "确认"}, "value": value | {"approve": True}},
                {"tag": "button", "type": "default", "text": {"tag": "plain_text", "content": "拒绝"}, "value": value | {"approve": False}}]})
        return {"config": {"wide_screen_mode": True},
                "header": {"template": "red" if red else "orange", "title": {"tag": "plain_text", "content": card["title"]}},
                "elements": elements}

    # ---- card buttons ------------------------------------------------------------------------
    def _on_rule_action(self, owner, value):
        row = self.s.db.one("SELECT owner FROM channel_sessions WHERE channel='feishu' AND session_id=?", (value["session_id"],))
        if not row or row["owner"] != owner:
            return {"toast": {"type": "error", "content": "无权操作"}}
        try:
            result = self.chat.decide_rule(value["session_id"], owner, value["bundle_id"], value["bundle_hash"], value["approve"])
        except Rejected as exc:
            return {"toast": {"type": "info", "content": "这条规则已经处理过或已过期" if exc.code in {"ACTION_NOT_PENDING", "AUTOMATION_NOT_PENDING",
                                                                                    "AUTOMATION_DRAFT_EXPIRED"} else "处理失败：" + exc.code}}
        message = result["reply"]["text"]
        return {"toast": {"type": "success" if value["approve"] else "info", "content": message[:60]},
                "card": {"type": "raw", "data": _rule_result("联动规则", message, value["approve"])}}

    def _on_card_action(self, event):
        open_id = ((event.get("operator") or {}).get("open_id"))
        value = ((event.get("action") or {}).get("value")) or {}
        owner = self._owner(open_id)
        if owner and set(value) == {"session_id", "bundle_id", "bundle_hash", "approve"} and type(value["approve"]) is bool:
            return self._on_rule_action(owner, value)
        if not owner or set(value) != {"session_id", "action_id", "action_hash", "approve"} or type(value["approve"]) is not bool:
            return {"toast": {"type": "error", "content": "无权操作"}}
        row = self.s.db.one("SELECT owner FROM channel_sessions WHERE channel='feishu' AND session_id=?", (value["session_id"],))
        if not row or row["owner"] != owner:  # someone else in a group chat cannot click another customer's card
            return {"toast": {"type": "error", "content": "无权操作"}}
        pending = {p["action_id"]: p for p in self.chat.store.get(value["session_id"], owner)["pending"]}
        entry = pending.get(value["action_id"])
        if not entry:
            return {"toast": {"type": "info", "content": "这项操作已经处理过了"}}
        if entry["card"]["risk_level"] == "RED":
            return {"toast": {"type": "error", "content": "高风险操作请在 APP 中验证"}}
        result = self.chat.decide(value["session_id"], owner, value["action_id"], value["action_hash"], value["approve"])
        message = result["reply"]["text"]
        return {"toast": {"type": "success" if value["approve"] else "info", "content": message[:60]},
                "card": {"type": "raw", "data": {"config": {"wide_screen_mode": True},
                         "header": {"template": "green" if value["approve"] else "grey",
                                    "title": {"tag": "plain_text", "content": entry["card"]["title"]}},
                         "elements": [{"tag": "markdown", "content": message}]}}}


def _rule_result(title, message, approved):
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "green" if approved else "grey", "title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "markdown", "content": message}]}


def channel_status(channel):
    return {"enabled": channel.config.enabled, "mode": channel.config.mode, "bound_users": len(channel.config.bindings),
            "encrypted": bool(channel.config.encrypt_key)}

