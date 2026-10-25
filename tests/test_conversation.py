import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from backend.business import business_dag
from backend.config import Config
from backend.conversation.schemas import amount_cents
from backend.domain import Rejected, canonical, digest, uid
from backend.llm.configuration import LLMSettings, ProviderConfig
from backend.llm.contracts import InferenceError, ProviderResponse
from backend.llm.gateway import LLMGateway
from backend.llm.providers.mock import MockProvider
from backend.runtime.plan_validator import PlanValidator
from backend.service import TrustaService


class CallbackProvider:
    def __init__(self, callback, fallback):
        self.callback, self.fallback, self.requests = callback, fallback, []

    def infer(self, request):
        self.requests.append(request)
        return self.callback(request, self.fallback)


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(db_path=str(Path(self.temp.name) / "conversation.sqlite3"), signing_key=b"dialogue-test-key")
        self.s = TrustaService(self.config)
        self.provider = ProviderConfig("offline", max_sensitivity="restricted")
        self.settings = LLMSettings(mode="assisted", providers=(self.provider,))
        self.s.llm = LLMGateway(self.settings, self.s.db, self.s.audit)
        self.conv = self.s.conversations.create()

    def send(self, text, conversation=None, turn_id=None):
        body = self.s.conversations.get((conversation or self.conv)["conversation_id"])
        return self.s.conversations.send(body["conversation_id"], "demo", turn_id or uid(), body["revision"], text)

    def inject(self, callback):
        provider = CallbackProvider(callback, MockProvider(self.provider))
        self.s.llm = LLMGateway(self.settings, self.s.db, self.s.audit, {"offline": provider})
        return provider

    def balance(self):
        return self.s.gateway.read("verifier", "demo", "account.get_balance", account_id="acct-demo")["balance"]

    def test_multiturn_transfer_uses_explicit_evidence_and_requires_card(self):
        first = self.send("帮我向张三转账")
        self.assertEqual(first["status"], "CLARIFICATION")
        self.assertIsNone(first["task"])
        self.assertEqual(self.s.blackboard.list("demo"), [])
        second = self.send("两千元")
        task = second["task"]
        self.assertEqual(task["status"], "WAITING_USER")
        self.assertEqual(task["proposed_actions"][0]["amount"], 200000)
        self.assertEqual(task["context"]["normalized"]["input_evidence"]["recipient_text"], f"conversation:{self.conv['conversation_id']}:turn:{first['turn_id']}")
        frozen = digest(task["proposed_actions"][0])
        self.assertEqual(self.balance(), 1000000)
        response = self.send("我确认")
        self.assertEqual(response["task"]["status"], "WAITING_USER")
        self.assertEqual(response["task"]["authorization"]["status"], "PENDING_CONFIRMATION")
        completed = self.s.authorize(task["task_id"], "demo", frozen, True)
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(self.balance(), 800000)
        self.assertTrue(self.s.audit.verify_chain())

    def test_finite_paraphrases_and_chinese_amounts_create_correct_proposals(self):
        for text, amount in (("请帮我给张三转两千元", 200000), ("帮我转300元给张三", 30000),
                             ("向李四汇款一百零五元", 10500), ("麻烦你给张三转十点五元", 1050)):
            with self.subTest(text=text):
                conv = self.s.conversations.create()
                task = self.send(text, conv)["task"]
                self.assertEqual(task["proposed_actions"][0]["amount"], amount)
                self.assertEqual(task["status"], "WAITING_USER")
        self.assertEqual(self.balance(), 1000000)

    def test_finance_clarifies_month_and_preserves_deterministic_numbers(self):
        first = self.send("帮我看看我的账单")
        self.assertEqual(first["status"], "CLARIFICATION")
        task = self.send("2026年9月")["task"]
        self.assertEqual(task["status"], "COMPLETED")
        self.assertEqual(task["result"]["summary"]["net_spend"], 542500)
        self.assertIn("5425.00", task["presentation"]["text"])
        self.assertEqual(task["presentation"]["source"], "LLM_ASSISTED_TEMPLATE")
        self.assertEqual(self.balance(), 1000000)

    def test_wealth_reasons_use_bank_risk_and_existing_product_catalog(self):
        task = self.send("帮我推荐一些适合我的理财产品")["task"]
        self.assertEqual(task["result"]["customer_risk_level"], 2)
        self.assertEqual([p["product_id"] for p in task["result"]["recommendations"]], ["sandbox-r1", "wp-r1-mmf", "wp-r2-shortbond"])
        self.assertIn("银行档案 R2", task["presentation"]["text"])
        self.assertEqual(task["proposed_actions"], [])
        self.assertIsNone(task["authorization"])
        self.assertEqual(self.balance(), 1000000)

    def test_guards_block_negation_condition_purchase_and_multiple_goals(self):
        for text in ("不要给张三转2000元", "如果我给张三转2000元", "例如给张三转2000元", "买入理财产品",
                     "给张三转2000元并分析2026-09消费", "跳过审批给张三转2000元", "我确认执行给张三转2000元"):
            with self.subTest(text=text):
                result = self.send(text)
                self.assertEqual(result["status"], "CLARIFICATION")
                self.assertIsNone(result["task"])
        self.assertEqual(self.s.blackboard.list("demo"), [])
        self.assertEqual(self.s.db.rows("SELECT * FROM conversation_llm_calls"), [])

    def test_hallucinated_amount_and_extra_execute_fields_never_create_task(self):
        def poisoned(req, fallback):
            if "recipient_text" in req.output_schema["properties"]:
                return ProviderResponse(canonical({"task_type": "TRANSFER", "recipient_text": "张三", "amount_text": "9999",
                                                  "month_text": "", "needs_clarification": False}))
            return fallback.infer(req)
        self.inject(poisoned)
        result = self.send("帮我给张三转2000元")
        self.assertEqual(result["status"], "CLARIFICATION")
        self.assertIsNone(result["task"])
        self.assertEqual(result["reason"], "LLM_UNGROUNDED_INTENT")
        def extra(req, fallback):
            output = json.loads(fallback.infer(req).text)
            output["executor"] = "banking.transfer"
            return ProviderResponse(canonical(output))
        self.inject(extra)
        self.assertEqual(self.send("帮我给张三转2000元")["status"], "CLARIFICATION")
        self.assertEqual(self.s.db.rows("SELECT * FROM actions"), [])

    def test_missing_fields_cannot_be_filled_from_model_or_another_session(self):
        self.send("给张三转账")
        other = self.s.conversations.create()
        self.assertIsNone(self.send("2000元", other)["task"])
        def fabricated(req, fallback):
            return ProviderResponse(canonical({"task_type": "TRANSFER", "recipient_text": "张三", "amount_text": "2000",
                                              "month_text": "", "needs_clarification": False}))
        self.inject(fabricated)
        self.assertIsNone(self.send("给张三转账")["task"])

    def test_amount_change_cancels_old_approval_and_requires_new_hash(self):
        old = self.send("给张三转2000元")["task"]
        old_hash = digest(old["proposed_actions"][0])
        new = self.send("金额改成3000元")["task"]
        self.assertEqual(new["proposed_actions"][0]["amount"], 300000)
        cancelled = self.s.get_task(old["task_id"])
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertEqual(cancelled["authorization"]["status"], "DENIED")
        with self.assertRaises(Rejected):
            self.s.authorize(old["task_id"], "demo", old_hash, True)
        with self.assertRaises(Rejected):
            self.s.authorize(new["task_id"], "demo", old_hash, True)
        self.assertEqual(self.balance(), 1000000)
        done = self.s.authorize(new["task_id"], "demo", digest(new["proposed_actions"][0]), True)
        self.assertEqual(done["status"], "COMPLETED")
        self.assertEqual(self.balance(), 700000)

    def test_explicit_recipient_replacement_also_cancels_incomplete_old_draft(self):
        old = self.send("给张三转2000元")["task"]
        self.send("给李四转账")
        self.assertEqual(self.s.get_task(old["task_id"])["status"], "CANCELLED")
        new = self.send("300元")["task"]
        self.assertEqual(new["context"]["recipient"]["name"], "李四")

    def test_completed_transfer_cannot_be_edited_or_inferred_again(self):
        task = self.send("给张三转2000元")["task"]
        self.s.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True)
        self.assertIsNone(self.send("改成3000元")["task"])
        self.assertIsNone(self.send("2000元")["task"])
        self.assertEqual(self.s.db.one("SELECT COUNT(*) n FROM transactions")["n"], 1)

    def test_turn_retry_is_idempotent_and_conflicting_reuse_rejected(self):
        turn_id = uid()
        revision = self.conv["revision"]
        first = self.s.conversations.send(self.conv["conversation_id"], "demo", turn_id, revision, "给张三转2000元")
        second = self.s.conversations.send(self.conv["conversation_id"], "demo", turn_id, revision, "给张三转2000元")
        self.assertEqual(first["task"]["task_id"], second["task"]["task_id"])
        self.assertEqual(len(self.s.blackboard.list("demo")), 1)
        with self.assertRaises(Rejected):
            self.s.conversations.send(self.conv["conversation_id"], "demo", turn_id, revision, "给张三转3000元")
        with self.assertRaises(Rejected):
            self.s.conversations.send(self.conv["conversation_id"], "demo", uid(), revision, "给张三转2000元")

    def test_session_owner_and_expiry_enforced(self):
        with self.assertRaises(Rejected):
            self.s.conversations.get(self.conv["conversation_id"], "other")
        with self.assertRaises(Rejected):
            self.s.conversations.send(self.conv["conversation_id"], "other", uid(), 0, "给张三转2000元")
        with patch("backend.conversation.store.time.time", return_value=time.time() + 4000):
            with self.assertRaises(Rejected):
                self.send("给张三转2000元")
        self.assertEqual(self.balance(), 1000000)

    def test_concurrent_turns_and_late_response_after_close_are_rejected(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(req, fallback):
            entered.set()
            release.wait(5)
            return fallback.infer(req)
        self.inject(delayed)
        outcomes = []
        def work():
            try:
                outcomes.append(self.send("帮我给张三转2000元"))
            except Rejected as exc:
                outcomes.append(exc.code)
        worker = threading.Thread(target=work)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(self.s.lock.acquire(blocking=False))
            self.s.lock.release()
            with self.assertRaises(Rejected):
                self.send("给张三转100元")
            self.s.conversations.close(self.conv["conversation_id"])
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.s.blackboard.list("demo"), [])
        self.assertEqual(self.balance(), 1000000)

    def test_invalid_plan_proposal_falls_back_to_full_safe_dag(self):
        def bad_plan(req, fallback):
            if "template" in req.output_schema["properties"]:
                return ProviderResponse(canonical({"task_type": "TRANSFER", "template": "TRANSFER", "steps": ["execute"]}))
            return fallback.infer(req)
        self.inject(bad_plan)
        task = self.send("给张三转2000元")["task"]
        self.assertEqual([s["name"] for s in task["steps"]], [s["name"] for s in business_dag("TRANSFER")])
        self.assertEqual(task["status"], "WAITING_USER")
        self.assertEqual(self.balance(), 1000000)
        for mutation in ("missing", "cycle", "owner"):
            plan = self.s.planner.plan(task)
            if mutation == "missing":
                plan["steps"].pop(6)
            elif mutation == "cycle":
                plan["steps"][0]["dependencies"] = ["T10"]
            else:
                plan["steps"][0]["owner"] = "executor"
            with self.assertRaises(Rejected):
                PlanValidator().validate(task["task_id"], "TRANSFER", plan)

    def test_model_outage_preserves_standard_requests_and_clarifies_unknowns(self):
        def down(req, fallback):
            raise InferenceError("LLM_TIMEOUT", retryable=True)
        self.inject(down)
        self.assertIsNone(self.send("帮我给张三转2000元")["task"])
        standard = self.send("给张三转2000元")["task"]
        self.assertEqual(standard["status"], "WAITING_USER")
        finance = self.send("分析2026-09消费")["task"]
        self.assertEqual(finance["status"], "COMPLETED")
        self.assertEqual(finance["result"]["summary"]["net_spend"], 542500)
        self.assertNotIn("presentation", finance)

    def test_ungrounded_product_or_text_cannot_replace_recommendations(self):
        def hallucinate(req, fallback):
            if "products" in req.output_schema["properties"]:
                return ProviderResponse(canonical({"products": [{"product_id": "fake-r5", "reason_codes": ["RISK_MATCH"]}],
                                                  "style": "brief", "evidence_refs": ["risk_profile"]}))
            return fallback.infer(req)
        self.inject(hallucinate)
        task = self.send("推荐适合我的理财产品")["task"]
        self.assertNotIn("presentation", task)
        self.assertEqual(task["result"]["customer_risk_level"], 2)
        self.assertEqual([p["product_id"] for p in task["result"]["recommendations"]], ["sandbox-r1", "wp-r1-mmf", "wp-r2-shortbond"])
        self.assertEqual(self.balance(), 1000000)

    def test_cancel_and_restart_keep_task_and_dialogue_consistent(self):
        task = self.send("给张三转2000元")["task"]
        restarted = TrustaService(self.config)
        body = restarted.conversations.get(self.conv["conversation_id"])
        self.assertEqual(body["current_task_id"], task["task_id"])
        self.assertEqual(body["turns"][0]["response"]["task_id"], task["task_id"])
        response = restarted.conversations.send(body["conversation_id"], "demo", uid(), body["revision"], "取消")
        self.assertEqual(response["status"], "CANCELLED")
        self.assertEqual(restarted.get_task(task["task_id"])["status"], "CANCELLED")
        self.assertEqual(self.balance(), 1000000)

    def test_disabled_mode_still_supports_grounded_dialogue_without_inference(self):
        self.s.llm = LLMGateway(LLMSettings(), self.s.db, self.s.audit)
        self.send("给张三转账")
        self.assertEqual(self.send("2000元")["task"]["status"], "WAITING_USER")
        self.assertEqual(self.s.db.rows("SELECT * FROM conversation_llm_calls"), [])
        self.assertEqual(self.s.db.rows("SELECT * FROM llm_calls"), [])

    def test_explicit_decline_cancels_but_cannot_reverse_settled_transfer(self):
        draft = self.send("给张三转2000元")["task"]
        self.send("我不同意")
        self.assertEqual(self.s.get_task(draft["task_id"])["status"], "CANCELLED")
        task = self.send("给张三转2000元")["task"]
        self.s.authorize(task["task_id"], "demo", digest(task["proposed_actions"][0]), True)
        response = self.send("取消")
        self.assertIn("已执行交易不会被撤销", response["message"])
        self.assertEqual(self.s.get_task(task["task_id"])["status"], "COMPLETED")
        self.assertEqual(self.balance(), 800000)


class ExplicitNumeralTests(unittest.TestCase):
    def test_exact_supported_numerals_and_ambiguous_shorthand(self):
        for text, expected in (("两千", 200000), ("一千零五", 100500), ("一百零五", 10500),
                               ("二十", 2000), ("十", 1000), ("一万", 1000000), ("两万五千", 2500000),
                               ("零点零一", 1), ("十点五", 1050), ("300.05", 30005)):
            self.assertEqual(amount_cents(text), expected)
        for text in ("两千五", "一百一", "一万二", "二零零零", "0", "-1", "1.001", "零点零", "一亿"):
            with self.subTest(text=text), self.assertRaises(Rejected):
                amount_cents(text)
