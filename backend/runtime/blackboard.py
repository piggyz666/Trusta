import json
import time
from backend.domain import Rejected, canonical, digest, uid
from backend.business import TASK_TYPES, business_dag
from backend.runtime.plan_validator import PlanValidator
from backend.runtime.banking_plan import TASKS as BANKING_TASKS


class Blackboard:
    def __init__(self, db):
        self.db = db

    def create(self, user_id, goal, normalized):
        if normalized.get("task_type") not in TASK_TYPES:
            raise Rejected("UNSUPPORTED_TASK_TYPE")
        task_id = uid()
        steps = []  # Planner supplies the graph; runtime validates it before installation.
        task = {"task_id": task_id, "user_id": user_id, "user_goal": goal, "task_type": normalized["task_type"],
                "status": "PENDING", "context": {"normalized": normalized}, "dag": steps, "steps": steps,
                "agent_results": {}, "proposed_actions": [], "policy_decision": None, "authorization": None,
                "execution": None, "verification": None, "receipt": None, "audit_refs": [], "errors": [], "result": None,
                "schema_version": 2, "revision": 0, "llm_call_refs": [], "llm_candidates": {},
                "updated_by": "supervisor", "updated_at": time.time()}
        with self.db.transaction() as db:
            db.execute("INSERT INTO tasks VALUES(?,?)", (task_id, canonical(task)))
            for step in steps:
                db.execute("INSERT INTO task_steps VALUES(?,?,?)", (task_id, step["step_id"], step["status"]))
        return task_id

    def install_plan(self, task_id, plan):
        current = self.get(task_id)
        PlanValidator().validate(task_id, current["task_type"], plan)
        with self.db.transaction() as db:
            task = json.loads(db.execute("SELECT body FROM tasks WHERE id=?", (task_id,)).fetchone()["body"])
            if task["steps"]:
                raise Rejected("PLAN_ALREADY_INSTALLED")
            task.update(dag=plan["steps"], steps=plan["steps"], updated_by="planner", updated_at=time.time(),
                        revision=task.get("revision", 0) + 1)
            db.execute("UPDATE tasks SET body=? WHERE id=?", (canonical(task), task_id))
            for step in plan["steps"]:
                db.execute("INSERT INTO task_steps VALUES(?,?,?)", (task_id, step["step_id"], step["status"]))

    def extend_action_plan(self, task_id, prefix, steps):
        from backend.runtime.banking_plan import validate_extension
        with self.db.transaction() as db:
            task = json.loads(db.execute("SELECT body FROM tasks WHERE id=?",(task_id,)).fetchone()["body"])
            validate_extension(task, steps, prefix)
            task["steps"].extend(steps)
            task.update(dag=task["steps"],revision=task.get("revision",0)+1,updated_by="planner",updated_at=time.time())
            db.execute("UPDATE tasks SET body=? WHERE id=?",(canonical(task),task_id))
            for step in steps:
                db.execute("INSERT INTO task_steps VALUES(?,?,?)",(task_id,step["step_id"],step["status"]))

    def get(self, task_id):
        row = self.db.one("SELECT body FROM tasks WHERE id=?", (task_id,))
        if not row:
            raise Rejected("TASK_NOT_FOUND")
        return json.loads(row["body"])

    def install_graph(self, task_id, graph, inputs, registry):
        from backend.runtime.taskgraph import SecurityExpansionCompiler
        compiled=SecurityExpansionCompiler(registry).compile(graph,inputs)
        with self.db.transaction() as conn:
            task=json.loads(conn.execute("SELECT body FROM tasks WHERE id=?",(task_id,)).fetchone()["body"])
            if task["task_type"]!="GENERAL_GRAPH" or task["steps"]: raise Rejected("GRAPH_INSTALL_DENIED")
            task.update(steps=compiled["steps"],dag=compiled["steps"],revision=task.get("revision",0)+1)
            conn.execute("UPDATE tasks SET body=? WHERE id=?",(canonical(task),task_id))
            for step in compiled["steps"]: conn.execute("INSERT INTO task_steps VALUES(?,?,?)",(task_id,step["step_id"],step["status"]))

    def update(self, task_id, actor, **changes):
        immutable = {"task_id", "user_id", "user_goal", "task_type", "schema_version", "revision",
                     "llm_call_refs", "llm_candidates", "steps", "dag"}
        owners = {"proposed_actions": {"transaction"}, "policy_decision": {"policy", "authorization"},
                  "authorization": {"authorization"}, "execution": {"executor"},
                  "verification": {"verifier"}, "receipt": {"receipts"}, "result": {"runtime"}, "presentation": {"runtime"}}
        if set(changes) & {"action_records","authorizations","verifications","receipts","scheduled_job_refs","graph_state"} and actor not in {"runtime", "recovery"}:
            raise Rejected("BLACKBOARD_FIELD_ACCESS_DENIED")
        if "proposed_actions" in changes and actor == "runtime" and self.get(task_id)["task_type"] in BANKING_TASKS|{"GENERAL_GRAPH"}:
            owners["proposed_actions"] = {"runtime"}
        if set(changes) & immutable or any(key in owners and actor not in owners[key] for key in changes):
            raise Rejected("BLACKBOARD_FIELD_ACCESS_DENIED")
        with self.db.transaction() as db:
            row = db.execute("SELECT body FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise Rejected("TASK_NOT_FOUND")
            task = json.loads(row["body"])
            task.update(changes)
            task.update(updated_by=actor, updated_at=time.time(), revision=task.get("revision", 0) + 1)
            db.execute("UPDATE tasks SET body=? WHERE id=?", (canonical(task), task_id))
        return task

    def attach_candidate(self, task_id, request_id, user_id, expected_revision, output, audit):
        """CAS submission only into the candidate area, never financial execution fields."""
        with self.db.transaction() as db:
            row = db.execute("SELECT body FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise Rejected("TASK_NOT_FOUND")
            task = json.loads(row["body"])
            call = db.execute("SELECT body FROM llm_calls WHERE id=? AND task_id=? AND owner=?",
                              (request_id, task_id, user_id)).fetchone()
            record = json.loads(call["body"]) if call else {}
            if (task["user_id"] != user_id or record.get("status") != "SUCCEEDED"
                    or record.get("task_revision") != expected_revision
                    or record.get("output_hash") != digest(output)):
                raise Rejected("LLM_CANDIDATE_ACCESS_DENIED")
            if request_id in task.get("llm_candidates", {}) or record.get("candidate_status"):
                raise Rejected("LLM_CANDIDATE_ALREADY_ATTACHED")
            reason = None
            if (task.get("revision", 0) != expected_revision
                    or task["status"] in {"CANCELLED", "BLOCKED", "FAILED", "MANUAL_REVIEW", "EXECUTING", "VERIFYING"}):
                reason = "LLM_STALE_TASK"
            elif record["deadline"] <= time.time():
                reason = "LLM_DEADLINE_EXPIRED"
            elif task.get("authorization") and task["authorization"]["expires_at"] <= time.time():
                reason = "LLM_AUTHORIZATION_EXPIRED"
            if reason:
                record.update(candidate_status="DISCARDED", candidate_reason=reason)
                db.execute("UPDATE llm_calls SET body=? WHERE id=?", (canonical(record), request_id))
                audit.append_in(db, task_id, None, "LLM_CANDIDATE_DISCARDED", "runtime", {"request_id": request_id, "reason": reason})
                return False
            task.setdefault("llm_candidates", {})[request_id] = {
                "purpose": record["purpose"], "schema_version": 1, "output": output, "authority": "NONE"}
            task.setdefault("llm_call_refs", []).append(request_id)
            task.update(revision=expected_revision + 1, updated_by="llm_candidate_guard", updated_at=time.time())
            record["candidate_status"] = "ATTACHED"
            db.execute("UPDATE tasks SET body=? WHERE id=?", (canonical(task), task_id))
            db.execute("UPDATE llm_calls SET body=? WHERE id=?", (canonical(record), request_id))
            audit.append_in(db, task_id, None, "LLM_CANDIDATE_RECORDED", "runtime", {"request_id": request_id, "output_hash": digest(output)})
            return True

    def list(self, user_id):
        return [json.loads(r["body"]) for r in self.db.rows("SELECT body FROM tasks")
                if json.loads(r["body"])["user_id"] == user_id]
