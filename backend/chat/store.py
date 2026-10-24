"""Durable chat sessions: owner scoped, revisioned, idempotent per message_id."""
import json
import re
import time
from backend.domain import Rejected, canonical, digest, uid

SESSION_TTL = 24 * 3600
TURN_DEADLINE = 120
MAX_MESSAGES = 60
MAX_TURNS = 80
RATE_LIMIT_PER_MINUTE = 20
MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class ChatStore:
    def __init__(self, db, audit):
        self.db, self.audit = db, audit
        with db.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS chat_sessions(id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                         "revision INTEGER NOT NULL, body TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS chat_handoffs(id TEXT PRIMARY KEY, owner TEXT NOT NULL, session_id TEXT NOT NULL, "
                         "status TEXT NOT NULL, created_at REAL NOT NULL, body TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS chat_llm_budget(day TEXT NOT NULL, owner TEXT NOT NULL, used INTEGER NOT NULL, "
                         "tokens INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day, owner))")

    @staticmethod
    def _day():
        from datetime import datetime
        from backend.business import CHINA
        return datetime.fromtimestamp(time.time(), CHINA).date().isoformat()

    def reserve_llm_call(self, owner, limit):
        """Atomically reserve one model call for today (Asia/Shanghai). A failed call still consumes its slot,
        so a broken upstream cannot be retried into an unbounded bill."""
        day = self._day()
        with self.db.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO chat_llm_budget(day, owner, used, tokens) VALUES(?,?,0,0)", (day, owner))
            return conn.execute("UPDATE chat_llm_budget SET used=used+1 WHERE day=? AND owner=? AND used<?",
                                (day, owner, limit)).rowcount == 1

    def record_llm_tokens(self, owner, tokens):
        if type(tokens) is int and tokens > 0:
            with self.db.transaction() as conn:
                conn.execute("UPDATE chat_llm_budget SET tokens=tokens+? WHERE day=? AND owner=?", (tokens, self._day(), owner))

    def llm_usage(self, owner):
        with self.db.transaction() as conn:
            row = conn.execute("SELECT used, tokens FROM chat_llm_budget WHERE day=? AND owner=?", (self._day(), owner)).fetchone()
        return {"calls": row["used"], "tokens": row["tokens"]} if row else {"calls": 0, "tokens": 0}

    def create(self, owner, channel, account_id, accounts):
        if channel not in {"app", "web", "feishu", "test"}:
            raise Rejected("INVALID_CHANNEL")
        now = time.time()
        body = {"session_id": "cs_" + uid().replace("-", ""), "owner": owner, "channel": channel, "revision": 0,
                "state": "IDLE", "default_account_id": account_id, "accounts": accounts, "created_at": now,
                "updated_at": now, "expires_at": now + SESSION_TTL, "closed": False, "busy": None,
                "messages": [], "turns": {}, "pending": [], "clarify": None, "strikes": 0, "locked_until": 0,
                "executed": [], "plan": None, "handoff": None, "surfaced_events": []}
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO chat_sessions VALUES(?,?,?,?)", (body["session_id"], owner, 0, canonical(body)))
            self.audit.append_in(conn, None, None, "CHAT_SESSION_CREATED", "chat", {"session_id": body["session_id"], "channel": channel})
        return body

    def _row(self, conn, session_id, owner):
        row = conn.execute("SELECT owner, revision, body FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise Rejected("CHAT_NOT_FOUND")
        if row["owner"] != owner:
            raise Rejected("CHAT_ACCESS_DENIED")
        body = json.loads(row["body"])
        if body["revision"] != row["revision"] or body["owner"] != owner:
            raise Rejected("CHAT_STATE_INVALID")
        return body

    def get(self, session_id, owner):
        with self.db.transaction() as conn:
            return self._row(conn, session_id, owner)

    def claim(self, session_id, owner, message_id, revision, text, account_id):
        """Returns (body, replay). Replay is the stored response for a retried message_id."""
        if not isinstance(message_id, str) or not MESSAGE_ID.fullmatch(message_id):
            raise Rejected("INVALID_MESSAGE_ID")
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 500:
            raise Rejected("INVALID_INPUT")
        fingerprint = digest({"text": text, "account_id": account_id})
        now = time.time()
        with self.db.transaction() as conn:
            body = self._row(conn, session_id, owner)
            previous = body["turns"].get(message_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise Rejected("MESSAGE_ID_REUSED")
                if previous.get("response") is None:
                    raise Rejected("CHAT_BUSY")
                return body, previous["response"]
            if body["closed"] or body["expires_at"] <= now:
                raise Rejected("CHAT_CLOSED")
            if type(revision) is not int or revision != body["revision"]:
                raise Rejected("CHAT_STALE")
            if body["busy"] and body["busy"]["deadline"] > now:
                raise Rejected("CHAT_BUSY")
            if sum(1 for m in body["messages"] if m["role"] == "user" and now - m["at"] < 60) >= RATE_LIMIT_PER_MINUTE:
                raise Rejected("CHAT_RATE_LIMITED")
            body["busy"] = {"message_id": message_id, "deadline": now + TURN_DEADLINE}
            body["turns"][message_id] = {"fingerprint": fingerprint, "response": None, "at": now}
            body["messages"].append({"role": "user", "message_id": message_id, "text": text, "at": now})
            if not body.get("title"):
                body["title"] = " ".join(text.split())[:24]
                body["title_auto"] = True
            self._write(conn, body, bump=False)
            return body, None

    def list(self, owner, limit=100):
        """Conversation history for the sidebar: newest first, hidden ones left out. Titles come from the first message."""
        now, result = time.time(), []
        for row in self.db.rows("SELECT body FROM chat_sessions WHERE owner=?", (owner,)):
            body = json.loads(row["body"])
            if body.get("hidden"):
                continue
            first = next((m["text"] for m in body["messages"] if m["role"] == "user"), "")
            result.append({"session_id": body["session_id"], "title": body.get("title") or " ".join(first.split())[:24] or "新对话",
                           "channel": body["channel"], "created_at": body["created_at"], "updated_at": body["updated_at"],
                           "messages": sum(1 for m in body["messages"] if m["role"] == "user"),
                           "pending": len(body["pending"]) + len(body.get("pending_rules", [])),
                           "active": not body["closed"] and body["expires_at"] > now})
        result.sort(key=lambda s: s["updated_at"], reverse=True)
        return [s for s in result if s["messages"] or s["pending"]][:limit]

    def rename(self, session_id, owner, title):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 40:
            raise Rejected("INVALID_INPUT")
        with self.db.transaction() as conn:
            body = self._row(conn, session_id, owner)
            body["title"] = " ".join(title.split())
            body["title_auto"] = False
            self._write(conn, body, bump=True)
        return {"session_id": session_id, "title": body["title"]}

    def save(self, body, bump=True):
        with self.db.transaction() as conn:
            current = self._row(conn, body["session_id"], body["owner"])
            if current["revision"] != body["revision"]:
                raise Rejected("CHAT_STALE")
            self._write(conn, body, bump)
            return body

    def _write(self, conn, body, bump):
        if bump:
            body["revision"] += 1
        body["updated_at"] = time.time()
        body["messages"] = body["messages"][-MAX_MESSAGES:]
        if len(body["turns"]) > MAX_TURNS:
            for key in sorted(body["turns"], key=lambda k: body["turns"][k]["at"])[: len(body["turns"]) - MAX_TURNS]:
                del body["turns"][key]
        conn.execute("UPDATE chat_sessions SET revision=?, body=? WHERE id=?",
                     (body["revision"], canonical(body), body["session_id"]))
