import json
import re
import time
from backend.domain import Rejected, canonical, digest, uid


def empty_slots():
    return {"task_type": "UNKNOWN", "recipient_text": "", "amount_text": "", "month_text": "", "needs_clarification": True}


class ConversationStore:
    TTL = 3600
    TURN_TTL = 120
    MAX_TURNS = 20

    def __init__(self, db, audit):
        self.db, self.audit = db, audit

    def create(self, user_id):
        now = time.time()
        body = {"conversation_id": uid(), "user_id": user_id, "status": "ACTIVE", "revision": 0,
                "created_at": now, "expires_at": now + self.TTL, "active_turn": None,
                "slots": empty_slots(), "slot_sources": {}, "current_task_id": None, "turn_ids": []}
        with self.db.transaction() as db:
            db.execute("INSERT INTO conversations VALUES(?,?,?)", (body["conversation_id"], user_id, canonical(body)))
            self.audit.append_in(db, None, None, "CONVERSATION_CREATED", "runtime", {"conversation_id": body["conversation_id"]})
        return body

    def _owned(self, db, conversation_id, user_id, live=True):
        row = db.execute("SELECT owner,body FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if not row or row["owner"] != user_id:
            raise Rejected("CONVERSATION_ACCESS_DENIED")
        body = json.loads(row["body"])
        if live and (body["status"] != "ACTIVE" or time.time() >= body["expires_at"]):
            raise Rejected("CONVERSATION_EXPIRED")
        return body

    def get(self, conversation_id, user_id):
        with self.db.transaction() as db:
            body = self._owned(db, conversation_id, user_id, live=False)
            turns = [json.loads(row["body"]) for row in db.execute("SELECT body FROM conversation_turns WHERE conversation_id=? ORDER BY rowid", (conversation_id,))]
        if body["expires_at"] <= time.time() and body["status"] == "ACTIVE":
            body["status"] = "EXPIRED"
        return body | {"turns": turns}

    def claim(self, conversation_id, user_id, turn_id, expected_revision, text, source_account_id):
        if (not isinstance(turn_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", turn_id)
                or type(expected_revision) is not int or expected_revision < 0
                or not isinstance(text, str) or not 0 < len(text.strip()) <= 1000
                or not isinstance(source_account_id, str) or not 0 < len(source_account_id) <= 128):
            raise Rejected("INVALID_INPUT")
        request_hash = digest({"text": text, "source_account_id": source_account_id})
        with self.db.transaction() as db:
            body = self._owned(db, conversation_id, user_id)
            row = db.execute("SELECT body FROM conversation_turns WHERE conversation_id=? AND id=?", (conversation_id, turn_id)).fetchone()
            if row:
                turn = json.loads(row["body"])
                if turn["request_hash"] != request_hash:
                    raise Rejected("TURN_ID_REUSED")
                if turn["status"] == "PROCESSING":
                    raise Rejected("TURN_IN_PROGRESS")
                return body, turn["response"]
            if body["active_turn"]:
                raise Rejected("CONVERSATION_BUSY")
            if body["revision"] != expected_revision:
                raise Rejected("CONVERSATION_STALE")
            if len(body["turn_ids"]) >= self.MAX_TURNS:
                raise Rejected("CONVERSATION_LIMIT")
            body.update(revision=body["revision"] + 1, active_turn=turn_id)
            body["turn_ids"].append(turn_id)
            turn = {"turn_id": turn_id, "text": text, "request_hash": request_hash, "status": "PROCESSING",
                    "created_at": time.time(), "deadline": time.time() + self.TURN_TTL, "task_id": None}
            db.execute("INSERT INTO conversation_turns VALUES(?,?,?)", (conversation_id, turn_id, canonical(turn)))
            db.execute("UPDATE conversations SET body=? WHERE id=?", (canonical(body), conversation_id))
            self.audit.append_in(db, None, None, "CONVERSATION_TURN_STARTED", "runtime", {"conversation_id": conversation_id, "turn_id": turn_id})
        return body, None

    def assert_current(self, conversation_id, user_id, turn_id, revision):
        with self.db.transaction() as db:
            return self._current(db, conversation_id, user_id, turn_id, revision)[0]

    def _current(self, db, conversation_id, user_id, turn_id, revision):
        body = self._owned(db, conversation_id, user_id)
        row = db.execute("SELECT body FROM conversation_turns WHERE conversation_id=? AND id=?", (conversation_id, turn_id)).fetchone()
        turn = json.loads(row["body"]) if row else {}
        if (body["revision"] != revision or body["active_turn"] != turn_id
                or turn.get("status") != "PROCESSING" or turn.get("deadline", 0) <= time.time()):
            raise Rejected("CONVERSATION_STALE")
        return body, turn

    def bind_task(self, conversation_id, user_id, turn_id, revision, task_id):
        with self.db.transaction() as db:
            body, turn = self._current(db, conversation_id, user_id, turn_id, revision)
            task = db.execute("SELECT body FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or json.loads(task["body"])["user_id"] != user_id:
                raise Rejected("TASK_ACCESS_DENIED")
            body["current_task_id"], turn["task_id"] = task_id, task_id
            db.execute("UPDATE conversations SET body=? WHERE id=?", (canonical(body), conversation_id))
            db.execute("UPDATE conversation_turns SET body=? WHERE conversation_id=? AND id=?", (canonical(turn), conversation_id, turn_id))

    def consume(self, conversation_id, user_id, turn_id, revision, call):
        with self.db.transaction() as db:
            self._current(db, conversation_id, user_id, turn_id, revision)
            row = db.execute("SELECT body FROM conversation_llm_calls WHERE id=? AND owner=? AND conversation_id=?",
                             (call.request_id, user_id, conversation_id)).fetchone()
            record = json.loads(row["body"]) if row else {}
            if (record.get("status") != "SUCCEEDED" or record.get("task_revision") != revision
                    or record.get("output_hash") != digest(call.output) or record.get("candidate_status")
                    or record.get("deadline", 0) <= time.time()):
                raise Rejected("LLM_STALE_TASK")
            record["candidate_status"] = "CONSUMED"
            db.execute("UPDATE conversation_llm_calls SET body=? WHERE id=?", (canonical(record), call.request_id))
            self.audit.append_in(db, None, None, "CONVERSATION_INTENT_ACCEPTED", "runtime", {"request_id": call.request_id})

    def finish(self, conversation_id, user_id, turn_id, revision, response, slots, sources=None):
        with self.db.transaction() as db:
            body, turn = self._current(db, conversation_id, user_id, turn_id, revision)
            body.update(revision=revision + 1, active_turn=None, slots=slots, slot_sources=sources or {})
            response = response | {"conversation_id": conversation_id, "turn_id": turn_id, "revision": body["revision"]}
            turn.update(status="COMPLETED", response=response)
            db.execute("UPDATE conversations SET body=? WHERE id=?", (canonical(body), conversation_id))
            db.execute("UPDATE conversation_turns SET body=? WHERE conversation_id=? AND id=?", (canonical(turn), conversation_id, turn_id))
            self.audit.append_in(db, turn["task_id"], None, "CONVERSATION_TURN_FINISHED", "runtime", response)
        return response

    def abort(self, conversation_id, user_id, turn_id, reason):
        with self.db.transaction() as db:
            body = self._owned(db, conversation_id, user_id, live=False)
            row = db.execute("SELECT body FROM conversation_turns WHERE conversation_id=? AND id=?", (conversation_id, turn_id)).fetchone()
            turn = json.loads(row["body"]) if row else {}
            if turn.get("status") != "PROCESSING":
                return
            if body["active_turn"] == turn_id:
                body.update(revision=body["revision"] + 1, active_turn=None, slots=empty_slots(), slot_sources={})
                db.execute("UPDATE conversations SET body=? WHERE id=?", (canonical(body), conversation_id))
            response = {"conversation_id": conversation_id, "turn_id": turn_id, "revision": body["revision"],
                        "status": "FAILED", "message": "本轮未完成，请查看任务状态后再提交完整请求。", "reason": reason, "task_id": turn.get("task_id")}
            turn.update(status="FAILED", response=response)
            db.execute("UPDATE conversation_turns SET body=? WHERE conversation_id=? AND id=?", (canonical(turn), conversation_id, turn_id))
            self.audit.append_in(db, turn.get("task_id"), None, "CONVERSATION_TURN_FAILED", "runtime", {"reason": reason})

    def close(self, conversation_id, user_id):
        with self.db.transaction() as db:
            body = self._owned(db, conversation_id, user_id, live=False)
            body.update(status="CLOSED", revision=body["revision"] + 1, slots=empty_slots(), slot_sources={})
            db.execute("UPDATE conversations SET body=? WHERE id=?", (canonical(body), conversation_id))
            self.audit.append_in(db, None, None, "CONVERSATION_CLOSED", "runtime", {"conversation_id": conversation_id})
        return body
