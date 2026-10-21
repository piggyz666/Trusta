import json
import time
from backend.domain import Rejected, canonical


TRANSITIONS = {
    "PENDING": {"READY", "BLOCKED", "CANCELLED"},
    "READY": {"RUNNING", "WAITING_TIME", "BLOCKED", "CANCELLED"},
    "RUNNING": {"COMPLETED", "WAITING_USER", "WAITING_TIME", "FAILED", "BLOCKED", "MANUAL_REVIEW", "CANCELLED"},
    "WAITING_USER": {"READY", "CANCELLED", "BLOCKED"},
    "WAITING_TIME": {"READY", "CANCELLED", "BLOCKED", "MANUAL_REVIEW"},
    "COMPLETED": set(), "FAILED": set(), "BLOCKED": set(), "MANUAL_REVIEW": set(), "CANCELLED": set(),
}


class Scheduler:
    def __init__(self, db, bus):
        self.db, self.bus = db, bus

    def transition(self, task_id, step_id, target):
        with self.db.transaction() as db:
            task = json.loads(db.execute("SELECT body FROM tasks WHERE id=?", (task_id,)).fetchone()["body"])
            step = next(s for s in task["steps"] if s["step_id"] == step_id)
            if target not in TRANSITIONS[step["status"]]:
                raise Rejected("ILLEGAL_STEP_TRANSITION", f'{step["status"]} -> {target}')
            if target == "READY" and any(next(s for s in task["steps"] if s["step_id"] == dep)["status"] != "COMPLETED"
                                         for dep in step["dependencies"]):
                raise Rejected("DEPENDENCY_NOT_COMPLETED")
            step["status"] = target
            task["dag"] = task["steps"]
            task.update(updated_by="scheduler", updated_at=time.time(), revision=task.get("revision", 0) + 1)
            db.execute("UPDATE tasks SET body=? WHERE id=?", (canonical(task), task_id))
            db.execute("UPDATE task_steps SET status=? WHERE task_id=? AND step_id=?", (target, task_id, step_id))
        event = {"READY": "STEP_READY", "RUNNING": "STEP_STARTED", "COMPLETED": "STEP_COMPLETED", "FAILED": "STEP_FAILED"}.get(target)
        if event:
            self.bus.publish(event, task_id, step_id, "scheduler", f"task:{task_id}:step:{step_id}", step)

    def start(self, task_id, step_id):
        self.transition(task_id, step_id, "READY")
        self.transition(task_id, step_id, "RUNNING")

    def ready_steps(self, task_id):
        task = json.loads(self.db.one("SELECT body FROM tasks WHERE id=?",(task_id,))["body"])
        completed = {step["step_id"] for step in task["steps"] if step["status"]=="COMPLETED"}
        return [step for step in task["steps"] if step["status"]=="PENDING" and set(step["dependencies"])<=completed]
