import time
from backend.domain import digest, uid


EVENTS = {"TASK_CREATED", "STEP_READY", "STEP_STARTED", "STEP_COMPLETED", "STEP_FAILED", "ACTION_PROPOSED",
          "AUTHORIZATION_REQUIRED", "AUTHORIZATION_GRANTED", "AUTHORIZATION_DENIED", "AUTHORIZATION_EXPIRED",
          "EXECUTION_STARTED", "EXECUTION_COMPLETED", "EXECUTION_FAILED", "VERIFICATION_COMPLETED",
          "VERIFICATION_FAILED", "MANUAL_REVIEW_REQUIRED", "POLICY_BLOCKED"}


class MessageBus:
    """In-process reference events; persistent task state remains in Blackboard."""
    def __init__(self, audit):
        self.audit = audit
        self.subscribers = []

    def subscribe(self, handler):
        self.subscribers.append(handler)

    def publish(self, kind, task_id, step_id, sender, payload_ref, payload):
        if kind not in EVENTS:
            raise ValueError(kind)
        event = {"event_id": uid(), "event_type": kind, "task_id": task_id, "step_id": step_id,
                 "sender": sender, "receiver": "runtime", "timestamp": time.time(),
                 "payload_ref": payload_ref, "payload_hash": digest(payload)}
        self.audit.append(task_id, None, kind, sender, event)
        for handler in self.subscribers:
            handler(event)
        return event
