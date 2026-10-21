from dataclasses import replace
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from backend.agents.registry import REGISTRY
from backend.config import Config
from backend.domain import Rejected, canonical, digest, uid
from backend.llm.configuration import LLMSettings, ProviderConfig
from backend.llm.contracts import InferenceContext, InferenceError, InferenceRequest, ProviderResponse
from backend.llm.gateway import LLMGateway
from backend.llm.projection import project
from backend.service import TrustaService


class StubProvider:
    def __init__(self, fn):
        self.fn, self.calls, self.requests = fn, 0, []

    def infer(self, request):
        self.calls += 1
        self.requests.append(request)
        return self.fn(request)


class LLMGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(db_path=str(Path(self.temp.name) / "bank.sqlite3"), signing_key=b"llm-test-key")
        self.service = TrustaService(self.config)
        self.task = self.service.create_task("给张三转 2000 元")

    def gateway(self, providers=None, settings=None):
        settings = settings or LLMSettings(mode="shadow", providers=(ProviderConfig("fixture", max_sensitivity="restricted"),))
        gateway = LLMGateway(settings, self.service.db, self.service.audit, providers)
        self.service.llm = gateway
        return gateway

    def request(self, **changes):
        task = self.service.blackboard.get(self.task["task_id"])
        return replace(InferenceRequest(uid(), InferenceContext(task["task_id"], "demo", "supervisor", "TRANSFER", task["revision"]),
                                        "intent_normalization", {"text": task["user_goal"]}), **changes)

    def response(self, output=None):
        return ProviderResponse(canonical(output or {"task_type": "TRANSFER", "needs_clarification": False}))

    def bank_snapshot(self):
        return {t: self.service.db.rows(f"SELECT * FROM {t} ORDER BY rowid") for t in
                ("accounts", "transfers", "transactions", "ledger_entries", "processed_idempotency_keys",
                 "consumption_records", "risk_profiles", "wealth_products")}

    def test_disabled_does_not_call_provider_or_create_invocations(self):
        stub = StubProvider(lambda req: self.fail("disabled invoked provider"))
        gateway = self.gateway({"fixture": stub}, LLMSettings(providers=(ProviderConfig("fixture"),)))
        result = gateway.infer(self.request())
        self.assertEqual(result.status, "DISABLED")
        self.assertEqual(self.service.db.rows("SELECT * FROM llm_calls"), [])

    def test_valid_candidate_has_no_authority_and_preserves_protected_fields(self):
        before, bank = copy.deepcopy(self.task), self.bank_snapshot()
        self.gateway({"fixture": StubProvider(lambda req: self.response())})
        result = self.service.shadow_task(self.task["task_id"])
        current = self.service.get_task(self.task["task_id"])
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(current["llm_candidates"][result.request_id]["authority"], "NONE")
        for key in ("context", "steps", "proposed_actions", "policy_decision", "authorization", "execution", "receipt"):
            self.assertEqual(before[key], current[key])
        self.assertEqual(bank, self.bank_snapshot())
        self.assertTrue(self.service.audit.verify_chain())

    def test_call_id_replay_and_cross_owner_access_are_rejected(self):
        stub = StubProvider(lambda req: self.response())
        gateway = self.gateway({"fixture": stub})
        request = self.request()
        gateway.infer(request)
        with self.assertRaises(InferenceError) as caught:
            gateway.infer(request)
        self.assertEqual(caught.exception.code, "LLM_REQUEST_REPLAY")
        self.assertEqual(stub.calls, 1)
        with self.assertRaises(InferenceError):
            gateway.trace(request.request_id, "other")
        with self.assertRaises(InferenceError):
            gateway.infer(replace(self.request(), context=replace(request.context, user_id="other")))

    def test_registry_and_data_classification_cannot_be_spoofed(self):
        self.gateway()
        for agent in ("executor", "service", "wealth", "unknown"):
            request = self.request()
            with self.subTest(agent=agent), self.assertRaises(InferenceError):
                self.service.llm.infer(replace(request, context=replace(request.context, agent=agent)))
        with patch.dict(REGISTRY["supervisor"], {"active": False}), self.assertRaises(InferenceError):
            self.service.llm.infer(self.request())
        with self.assertRaises(InferenceError):
            self.service.llm.infer(self.request(sensitivity="public"))
        self.assertEqual(self.service.db.rows("SELECT * FROM llm_calls"), [])

    def test_schema_extra_fields_duplicates_nan_and_invalid_semantics_stop(self):
        invalid = ['{"task_type":"TRANSFER","needs_clarification":false,"authorization":true}',
                   '{"task_type":"TRANSFER","task_type":"TRANSFER","needs_clarification":false}',
                   '{"task_type":"TRANSFER","needs_clarification":NaN}',
                   '{"task_type":"UNKNOWN","needs_clarification":false}', '[]', 'not JSON',
                   '{"task_type":"TRANSFER","needs_clarification":0}']
        bank = self.bank_snapshot()
        for text in invalid:
            with self.subTest(text=text):
                gateway = self.gateway({"fixture": StubProvider(lambda req, text=text: ProviderResponse(text))})
                result = gateway.infer(self.request())
                self.assertEqual(result.status, "FAILED")
        self.assertEqual(bank, self.bank_snapshot())

    def test_retryable_failure_only_falls_back_to_eligible_provider(self):
        def unavailable(req):
            raise InferenceError("LLM_PROVIDER_UNAVAILABLE", retryable=True)
        first = StubProvider(unavailable)
        public = StubProvider(lambda req: self.fail("restricted data escaped"))
        second = StubProvider(lambda req: self.response())
        settings = LLMSettings(mode="shadow", providers=(ProviderConfig("first", max_sensitivity="restricted"),
                              ProviderConfig("public"), ProviderConfig("second", max_sensitivity="restricted")),
                              routes={"intent_normalization": ["first", "public", "second"]})
        gateway = self.gateway({"first": first, "public": public, "second": second}, settings)
        result = gateway.infer(self.request())
        self.assertEqual(result.provider, "second")
        self.assertEqual(public.calls, 0)
        trace = gateway.trace(result.request_id, "demo")
        self.assertEqual(trace["rejected_routes"]["public"], "DATA_SENSITIVITY")
        self.assertEqual(len(trace["attempts"]), 2)

    def test_invalid_output_never_retries_or_falls_back(self):
        first, second = StubProvider(lambda req: ProviderResponse("bad")), StubProvider(lambda req: self.response())
        settings = LLMSettings(mode="shadow", providers=(ProviderConfig("a", max_sensitivity="restricted"),
                              ProviderConfig("b", max_sensitivity="restricted")))
        gateway = self.gateway({"a": first, "b": second}, settings)
        self.assertEqual(gateway.infer(self.request()).status, "FAILED")
        self.assertEqual(second.calls, 0)

    def test_late_response_and_provider_exception_are_safe(self):
        def delayed(req):
            time.sleep(0.07)
            return self.response()
        settings = LLMSettings(mode="shadow", timeout_seconds=0.05,
                               providers=(ProviderConfig("fixture", max_sensitivity="restricted"),))
        gateway = self.gateway({"fixture": StubProvider(delayed)}, settings)
        self.assertEqual(gateway.infer(self.request()).error_code, "LLM_TIMEOUT")
        def leaking(req):
            raise RuntimeError("secret-api-key and private bank payload")
        gateway = self.gateway({"fixture": StubProvider(leaking)})
        result = gateway.infer(self.request())
        self.assertEqual(result.error_code, "LLM_PROVIDER_ERROR")
        self.assertNotIn("secret-api-key", canonical(gateway.trace(result.request_id, "demo")))

    def test_telemetry_excludes_prompts_responses_and_provider_secret_fields(self):
        output = {"task_type": "TRANSFER", "needs_clarification": False}
        stub = StubProvider(lambda req: ProviderResponse(canonical(output), "SECRET_MODEL_VALUE", {"input_tokens": 2, "secret": "DO_NOT_LOG"}))
        gateway = self.gateway({"fixture": stub})
        request = self.request(payload={"text": "Bearer SECRET_TOKEN 密码:SECRET_PASSWORD 1234567890123456"})
        result = gateway.infer(request)
        trace = canonical(gateway.trace(result.request_id, "demo"))
        outbound = canonical(list(stub.requests[0].messages))
        for secret in ("SECRET_TOKEN", "SECRET_PASSWORD", "1234567890123456", "DO_NOT_LOG", "SECRET_MODEL_VALUE"):
            self.assertNotIn(secret, trace)
            self.assertNotIn(secret, outbound)
        self.assertNotIn("needs_clarification", trace)

    def test_projection_rejects_execution_fields_and_unknown_evidence(self):
        self.gateway()
        with self.assertRaises(InferenceError):
            self.service.llm.infer(self.request(payload={"text": "request", "api_key": "secret"}))
        with self.assertRaises(InferenceError):
            project("wealth_explanation", {"risk_level": 5, "products": [], "evidence_refs": [], "authorization": True})
        task = self.service.create_task("分析2026-09消费")
        before = self.service.get_task(task["task_id"])["llm_candidates"]
        self.gateway({"fixture": StubProvider(lambda req: self.response({"summary": "说明", "evidence_refs": ["fabricated"]}))})
        result = self.service.shadow_task(task["task_id"])
        self.assertEqual(result.error_code, "LLM_UNGROUNDED_REFERENCE")
        self.assertEqual(self.service.get_task(task["task_id"])["llm_candidates"], before)

    def test_candidate_cas_rejects_changed_task_and_wrong_payload(self):
        gateway = self.gateway({"fixture": StubProvider(lambda req: self.response())})
        request = self.request()
        result = gateway.infer(request)
        with self.assertRaises(Rejected):
            self.service.blackboard.attach_candidate(self.task["task_id"], result.request_id, "demo", request.context.revision,
                                                     {"task_type": "UNKNOWN", "needs_clarification": True}, self.service.audit)
        self.service.blackboard.update(self.task["task_id"], "runtime", clarification="changed")
        attached = self.service.blackboard.attach_candidate(self.task["task_id"], result.request_id, "demo", request.context.revision,
                                                           result.output, self.service.audit)
        self.assertFalse(attached)
        self.assertEqual(gateway.trace(result.request_id, "demo")["candidate_status"], "DISCARDED")
        with self.assertRaises(Rejected):
            self.service.blackboard.attach_candidate(self.task["task_id"], result.request_id, "demo", request.context.revision,
                                                     result.output, self.service.audit)

    def test_candidate_expiry_and_duplicate_submission(self):
        gateway = self.gateway({"fixture": StubProvider(lambda req: self.response())})
        request = self.request()
        result = gateway.infer(request)
        args = (self.task["task_id"], result.request_id, "demo", request.context.revision, result.output, self.service.audit)
        self.assertTrue(self.service.blackboard.attach_candidate(*args))
        with self.assertRaises(Rejected):
            self.service.blackboard.attach_candidate(*args)
        request = self.request()
        result = gateway.infer(request)
        with patch("backend.runtime.blackboard.time.time", return_value=time.time() + 100):
            self.assertFalse(self.service.blackboard.attach_candidate(self.task["task_id"], result.request_id, "demo",
                             request.context.revision, result.output, self.service.audit))

    def test_model_wait_does_not_lock_runtime_or_change_frozen_action(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(req):
            entered.set()
            release.wait(5)
            return self.response()
        gateway = self.gateway({"fixture": StubProvider(delayed)})
        thread = threading.Thread(target=self.service.shadow_task, args=(self.task["task_id"],))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.service.lock.acquire(blocking=False), "LLM held global lock")
            self.service.lock.release()
            action_hash = digest(self.task["proposed_actions"][0])
            completed = self.service.authorize(self.task["task_id"], "demo", action_hash, True)
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(digest(completed["proposed_actions"][0]), action_hash)
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.service.get_task(self.task["task_id"])["llm_candidates"], {})
        self.assertEqual(self.service.db.one("SELECT COUNT(*) AS n FROM transactions")["n"], 1)
        record = json.loads(self.service.db.one("SELECT body FROM llm_calls")["body"])
        self.assertEqual(record["candidate_status"], "DISCARDED")

    def test_blackboard_financial_fields_are_owned_and_old_tasks_survive(self):
        for key in ("authorization", "execution", "policy_decision", "proposed_actions", "receipt", "revision", "llm_candidates"):
            with self.subTest(key=key), self.assertRaises(Rejected):
                self.service.blackboard.update(self.task["task_id"], "supervisor", **{key: None})
        old = copy.deepcopy(self.task)
        for key in ("schema_version", "revision", "llm_call_refs", "llm_candidates"):
            old.pop(key)
        with self.service.db.transaction() as db:
            db.execute("UPDATE tasks SET body=? WHERE id=?", (canonical(old), old["task_id"]))
        restarted = TrustaService(self.config)
        result = restarted.authorize(old["task_id"], "demo", digest(old["proposed_actions"][0]), True)
        self.assertEqual(result["status"], "COMPLETED")

    def test_signed_handoff_commits_only_after_validation(self):
        original = copy.deepcopy(self.service.blackboard.get(self.task["task_id"])["agent_results"])
        with patch.object(self.service.messages, "verify", side_effect=Rejected("INVALID_SIGNATURE")):
            with self.assertRaises(Rejected):
                self.service._agent_message("transaction", "supervisor", self.task["task_id"], "T05", "ACTION_PROPOSED", {"amount": 1})
        self.assertEqual(self.service.blackboard.get(self.task["task_id"])["agent_results"], original)

    def test_signed_but_illegal_handoff_is_rejected(self):
        message = self.service.messages.sign("wealth", "supervisor", self.task["task_id"], "T05", "ACTION_PROPOSED", "ref", {})
        with self.assertRaises(Rejected) as caught:
            self.service.accept_message(message, {}, "supervisor")
        self.assertEqual(caught.exception.code, "MESSAGE_CONTEXT_DENIED")

    def test_restart_retains_candidates_and_recovers_only_expired_calls(self):
        gateway = self.gateway()
        result = self.service.shadow_task(self.task["task_id"])
        pending = gateway._claim(self.request())
        restarted = TrustaService(self.config)
        self.assertIn(result.request_id, restarted.get_task(self.task["task_id"])["llm_candidates"])
        self.assertEqual(restarted.db.one("SELECT status FROM llm_calls WHERE id=?", (pending["request_id"],))["status"], "PENDING")
        with patch("backend.llm.gateway.time.time", return_value=time.time() + 100):
            gateway.recover_expired()
        self.assertEqual(gateway.trace(pending["request_id"], "demo")["status"], "FAILED")
        self.assertTrue(self.service.audit.verify_chain())
