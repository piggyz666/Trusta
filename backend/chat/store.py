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
            self._write(conn, body, bump=False)
            return body, None

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
