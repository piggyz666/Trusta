from dataclasses import replace
import json
import sqlite3
import time
from backend.agents.registry import check_inference
from backend.domain import Rejected, canonical, digest
from backend.llm.configuration import valid_number
from backend.llm.contracts import InferenceError, InferenceResult, ProviderRequest
from backend.llm.projection import OUTPUT_SCHEMAS, project, validate_output
from backend.llm.prompts import messages
from llm.bridge import backend_provider
from backend.llm.providers.http_adapter import HTTPAdapter
from backend.llm.router import ModelRouter
from backend.llm.validation import strict_json


class LLMGateway:
    """Produces untrusted candidates. It owns no bank adapter, auth, contract or tools."""
    def __init__(self, settings, db, audit, providers=None):
        self.settings = settings.validate()
        self.db, self.audit = db, audit
        self.identity_guard = None
        self.router = ModelRouter(settings)
        self.providers = providers if providers is not None else {
            p.name: backend_provider(p) for p in settings.providers}
        if set(self.providers) != {p.name for p in settings.providers}:
            raise InferenceError("LLM_INVALID_CONFIG")
        self.recover_expired()

    def recover_expired(self):
        # Do not invalidate an in-flight call owned by another service instance.
        for row in self.db.rows("SELECT body FROM llm_calls WHERE status='PENDING' UNION ALL SELECT body FROM conversation_llm_calls WHERE status='PENDING'"):
            record = json.loads(row["body"])
            if record["deadline"] <= time.time():
                record.update(status="FAILED", error_code="LLM_DEADLINE_EXPIRED")
                self._finish(record)

    def _claim(self, request):
        now = time.time()
        record = {"request_id": request.request_id, "task_id": request.context.task_id,
                  "user_id": request.context.user_id, "agent": request.context.agent,
                  "task_revision": request.context.revision, "purpose": request.purpose,
                  "config_version": self.settings.version, "mode": self.settings.mode,
                  "sensitivity": request.sensitivity, "security_level": request.security_level,
                  "status": "PENDING", "created_at": now, "deadline": now + self.settings.timeout_seconds,
                  "attempts": [], "rejected_routes": {}, "estimated_costs_usd": {}}
        if request.context.conversation_id:
            record["conversation_id"] = request.context.conversation_id
        table = "conversation_llm_calls" if request.context.conversation_id else "llm_calls"
        scope_id = request.context.conversation_id or request.context.task_id
        try:
            with self.db.transaction() as db:
                if db.execute("SELECT id FROM llm_calls WHERE id=? UNION ALL SELECT id FROM conversation_llm_calls WHERE id=?",
                              (request.request_id, request.request_id)).fetchone():
                    raise InferenceError("LLM_REQUEST_REPLAY")
                db.execute(f"INSERT INTO {table} VALUES(?,?,?,?,?)", (request.request_id, scope_id,
                           request.context.user_id, "PENDING", canonical(record)))
        except sqlite3.IntegrityError:
            raise InferenceError("LLM_REQUEST_REPLAY") from None
        return record

    def _finish(self, record):
        record["finished_at"] = time.time()
        record["latency_ms"] = round(max(0, record["finished_at"] - record["created_at"]) * 1000, 2)
        with self.db.transaction() as db:
            table = "conversation_llm_calls" if record.get("conversation_id") else "llm_calls"
            changed = db.execute(f"UPDATE {table} SET status=?,body=? WHERE id=? AND status='PENDING'",
                                 (record["status"], canonical(record), record["request_id"])).rowcount
            if changed != 1:
                raise InferenceError("LLM_CALL_ALREADY_FINISHED")
            self.audit.append_in(db, record["task_id"], None, "LLM_CALL_FINISHED", record["agent"], record)

    def infer(self, request):
        if self.settings.mode == "disabled":
            return InferenceResult(request.request_id, "DISABLED", error_code="LLM_DISABLED")
        check_inference(request.context.agent, request.purpose, request.context.task_type)
        if self.identity_guard:
            try:
                self.identity_guard(request.context.agent)
            except Rejected:
                raise InferenceError("LLM_CAPABILITY_DENIED") from None
        minimum = "internal" if request.purpose == "plan_proposal" else "restricted"
        from backend.llm.contracts import SENSITIVITY
        if request.sensitivity not in SENSITIVITY or SENSITIVITY[request.sensitivity] < SENSITIVITY[minimum]:
            raise InferenceError("LLM_DATA_CLASSIFICATION_VIOLATION")
        if (not isinstance(request.request_id, str) or not 1 <= len(request.request_id) <= 128
                or type(request.context.revision) is not int or request.context.revision < 0
                or (request.max_cost_usd is not None and not valid_number(request.max_cost_usd, 0, 100000))):
            raise InferenceError("LLM_INVALID_INPUT")
        projected = project(request.purpose, request.payload)
        # Registry check and snapshot ownership come from trusted runtime, never provider content.
        conversation = request.context.conversation_id
        if conversation and (request.context.task_id is not None or request.context.task_type != "CONVERSATION"):
            raise InferenceError("LLM_INVALID_INPUT")
        row = self.db.one("SELECT body FROM conversations WHERE id=?" if conversation else "SELECT body FROM tasks WHERE id=?",
                          (conversation or request.context.task_id,))
        if not row:
            raise InferenceError("LLM_TASK_NOT_FOUND")
        task = json.loads(row["body"])
        if (task["user_id"] != request.context.user_id
                or not conversation and task["task_type"] != request.context.task_type):
            raise InferenceError("LLM_TASK_ACCESS_DENIED")
        if task.get("revision", 0) != request.context.revision:
            raise InferenceError("LLM_STALE_TASK")
        if conversation and (task["status"] != "ACTIVE" or task["expires_at"] <= time.time() or not task.get("active_turn")):
            raise InferenceError("LLM_STALE_TASK")
        schema = OUTPUT_SCHEMAS[request.purpose]
        projected_messages = messages(request.purpose, projected, schema)
        provider_request = ProviderRequest(projected_messages, schema, self.settings.max_output_tokens, self.settings.timeout_seconds)
        if len(canonical(list(projected_messages))) + len(canonical(schema)) > self.settings.max_request_chars:
            raise InferenceError("LLM_INPUT_TOO_LARGE")
        route = self.router.select(request, provider_request)
        record = self._claim(request)
        record.update(rejected_routes=route.rejected, estimated_costs_usd=route.estimated_costs)
        clock_deadline = time.monotonic() + max(0, record["deadline"] - time.time())
        selected, output, error = None, None, "LLM_NO_ELIGIBLE_PROVIDER"
        for config in route.providers[:self.settings.max_fallbacks + 1]:
            remaining = clock_deadline - time.monotonic()
            if remaining <= 0:
                error = "LLM_TIMEOUT"
                break
            attempt = {"provider": config.name, "configured_model": config.model}
            record["attempts"].append(attempt)
            try:
                response = self.providers[config.name].infer(replace(provider_request, timeout_seconds=remaining))
                if time.monotonic() >= clock_deadline:
                    raise InferenceError("LLM_TIMEOUT", retryable=True)
                if not isinstance(response.text, str) or len(response.text.encode()) > HTTPAdapter.MAX_RESPONSE_BYTES:
                    raise InferenceError("LLM_INVALID_OUTPUT")
                candidate = strict_json(response.text)
                validate_output(request.purpose, candidate, projected)
                # Never persist an unsolicited identifier or arbitrary provider string.
                reported_model = response.model if response.model == config.model else None
                usage = {k: v for k, v in (response.usage or {}).items()
                         if k in {"input_tokens", "output_tokens"} and type(v) is int and 0 <= v <= 10**9}
                attempt.update(status="SUCCEEDED", reported_model=reported_model, usage=usage or None)
                # Never retain raw prompts or response bodies in invocation telemetry.
                record["output_hash"] = digest(candidate)
                selected, output, error = config.name, candidate, None
                break
            except InferenceError as exc:
                safe_codes = {"LLM_TIMEOUT", "LLM_PROVIDER_UNAVAILABLE", "LLM_PROVIDER_CREDENTIALS",
                              "LLM_PROVIDER_PROTOCOL", "LLM_PROVIDER_HTTP_ERROR", "LLM_RESPONSE_TOO_LARGE",
                              "LLM_REFUSED", "LLM_TOOL_CALL_FORBIDDEN", "LLM_INCOMPLETE_OUTPUT",
                              "LLM_INVALID_OUTPUT", "LLM_UNGROUNDED_REFERENCE", "LLM_UNGROUNDED_INTENT"}
                error = exc.code if exc.code in safe_codes else "LLM_PROVIDER_ERROR"
                attempt.update(status="FAILED", error_code=error)
                if not exc.retryable or error not in {"LLM_TIMEOUT", "LLM_PROVIDER_UNAVAILABLE"}:
                    break
            except Exception:
                # A custom adapter's exception may contain a credential or a full HTTP body.
                error = "LLM_PROVIDER_ERROR"
                attempt.update(status="FAILED", error_code=error)
                break
        record.update(status="SUCCEEDED" if output is not None else "FAILED", error_code=error, provider=selected)
        self._finish(record)
        return InferenceResult(request.request_id, record["status"], output, selected, error)

    def trace(self, request_id, user_id):
        row = self.db.one("SELECT owner,body FROM llm_calls WHERE id=? UNION ALL SELECT owner,body FROM conversation_llm_calls WHERE id=?",
                          (request_id, request_id))
        if not row or row["owner"] != user_id:
            raise InferenceError("LLM_CALL_ACCESS_DENIED")
        return json.loads(row["body"])

    def propose_graph(self,registry,task,goal,inputs,*,graph_format=None):
        from backend.llm.graph_proposal import propose
        return propose(self,registry,task,goal,inputs,graph_format=graph_format)
