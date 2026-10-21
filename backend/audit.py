import json
import time
from backend.domain import canonical, digest, uid


class AuditEngine:
    def __init__(self, db):
        self.db = db

    def append(self, task_id, action_id, kind, actor, payload):
        with self.db.transaction() as db:
            return self.append_in(db, task_id, action_id, kind, actor, payload)

    def append_in(self, db, task_id, action_id, kind, actor, payload):
        """Append using an existing transaction, preserving the same audit chain format."""
        prior = db.execute("SELECT body FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
        event = {"audit_event_id": uid(), "task_id": task_id, "action_id": action_id, "event_type": kind,
                 "actor": actor, "timestamp": time.time(), "payload_hash": digest(payload),
                 "previous_hash": json.loads(prior["body"])["event_hash"] if prior else "0" * 64}
        event["event_hash"] = digest(event)
        db.execute("INSERT INTO audit_events(body) VALUES(?)", (canonical(event),))
        return event["audit_event_id"]

    def verify_chain(self):
        previous = "0" * 64
        try:
            for row in self.db.rows("SELECT body FROM audit_events ORDER BY seq"):
                event = json.loads(row["body"])
                recorded = event.pop("event_hash")
                if event["previous_hash"] != previous or digest(event) != recorded:
                    return False
                previous = recorded
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            return False
        return True
