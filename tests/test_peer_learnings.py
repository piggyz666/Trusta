"""Improvements adopted after reviewing a peer implementation: sequential dependent proposals, grounded knowledge
answers with sources, navigate deep links, LLM gate/budget/truncation, model-side redaction, security headers."""
import json
import threading
import unittest
from backend.chat.llm import LLMUnavailable, parse_completion, redact_for_model
from backend.chat.rules import rule_calls
from tests.test_enhancements import Harness, call, transfer


def buy(amount):
    return call("propose_investment", op="PURCHASE", product={"id": "sandbox-r1", "span": "低风险"},
                amount={"value": amount, "span": amount})


class SequentialPlanTests(Harness):
    def balance(self):
        return self.s.db.one("SELECT balance FROM accounts WHERE id='acct-demo'")["balance"]

    def test_second_money_step_waits_for_first(self):
        turn = self.say("先给张三转100，再买200低风险", [transfer("张三", "100", "100"), buy("200")])
        confirms = self.cards(turn, "confirm")
        self.assertEqual([c["action_type"] for c in confirms], ["TRANSFER"])
        self.assertEqual([n["status"] for n in turn["plan"]["nodes"]], ["AWAITING_CONFIRMATION", "QUEUED"])
        self.assertIn("上一步完成后", turn["reply"]["text"])
        before = self.balance()
        done = self.decide(confirms[0])
        self.assertEqual(self.balance(), before - 10000)
        follow = [c for c in done["reply"]["cards"] if c["type"] == "confirm"]
        self.assertEqual([c["action_type"] for c in follow], ["INVESTMENT_PURCHASE"])
        self.assertIn("下一步", done["reply"]["text"])
        self.assertEqual(done["plan"]["nodes"][1]["status"], "AWAITING_CONFIRMATION")
        self.decide(follow[0], verification=self.secret())
        self.assertEqual(self.balance(), before - 30000)

    def test_declined_first_step_cancels_follow_up(self):
        turn = self.say("给张三转100然后买200低风险", [transfer("张三", "100", "100"), buy("200")])
        done = self.decide(self.cards(turn, "confirm")[0], approve=False)
        self.assertIn("后续操作也已取消", done["reply"]["text"])
        self.assertEqual(done["plan"]["nodes"][1]["status"], "SKIPPED")
        self.assertEqual(self.chat.get(self.sid, "demo")["pending"], [])

    def test_independent_steps_are_not_queued(self):
        turn = self.say("冻结卡，再给张三转100", [call("propose_card_operation", op="FREEZE"), transfer("张三", "100", "100")])
        self.assertEqual(len(self.cards(turn, "confirm")), 2)

    def test_interrupt_drops_queue(self):
        self.say("给张三转100再买200低风险", [transfer("张三", "100", "100"), buy("200")])
        result = self.chat.interrupt(self.sid, "demo", self.rev)
        self.assertIn("取消了 2 项", result["reply"]["text"])


class KnowledgeAndNavigateTests(Harness):
    def test_knowledge_comes_from_config_with_sources(self):
        turn = self.say("转账限额是多少", [call("knowledge", topic="TRANSFER_LIMITS")])
        card = self.cards(turn, "knowledge")[0]
        self.assertIn("¥1,000.00", card["text"])
        self.assertTrue(card["sources"] and all("ref" in s for s in card["sources"]))
        self.assertEqual(turn["reply"]["text"].rstrip("。"), card["text"].rstrip("。"))

    def test_unknown_topic_is_rejected_by_schema(self):
        turn = self.say("限额", [call("knowledge", topic="MAKE_UP_A_RULE")])
        self.assertFalse(self.cards(turn, "knowledge"))

    def test_navigate_builds_fixed_link(self):
        turn = self.say("打开理财页面", [call("navigate", page="wealth")])
        card = self.cards(turn, "navigate")[0]
        self.assertEqual(card["deep_link"], "trusta://app/wealth")
        self.assertFalse(self.cards(self.say("去个网站", [call("navigate", page="https://evil.example")]), "navigate"))

    def test_rules_cover_knowledge_and_navigate(self):
        self.assertEqual(rule_calls("冻结和挂失有什么区别", {}), [call("knowledge", topic="CARD_LOSS")])
        self.assertEqual(rule_calls("打开心愿金页面", {}), [call("navigate", page="wish_funds")])

    def test_report_cards_carry_sources(self):
        card = self.cards(self.say("余额", [call("get_balance")]), "report")[0]
        self.assertEqual(card["sources"][-1], {"label": "签名证据包（可离线验证）", "kind": "EVIDENCE", "ref": card["task_id"]})


class LLMBoundaryTests(Harness):
    def test_redaction_keeps_phone_masks_card(self):
        text = redact_for_model("卡号6222 0212 3456 7890丢了，给13800138000转100，身份证11010119900101123X")
        self.assertNotIn("6222", text)
        self.assertIn("尾号7890", text)
        self.assertIn("13800138000", text)
        self.assertIn("尾号123X", text)

    def test_model_never_sees_card_number(self):
        self.say("我的卡6222021234567890丢了", [call("propose_card_operation", op="REPORT_LOST")])
        self.assertTrue(self.llm.prompts)  # scripted miss is fine; what matters is what was sent
        sent = json.dumps(self.llm.prompts[-1][1], ensure_ascii=False)
        self.assertNotIn("6222021234567890", sent)

    def test_truncated_completion_is_rejected(self):
        raw = json.dumps({"choices": [{"finish_reason": "length", "message": {"tool_calls": [
            {"function": {"name": "propose_transfer", "arguments": "{\"payee\":"}}]}}]})
        with self.assertRaises(LLMUnavailable) as caught:
            parse_completion(raw)
        self.assertEqual(caught.exception.code, "LLM_TRUNCATED")

    def test_daily_budget_falls_back_to_rules(self):
        self.chat.LLM_DAILY_CALLS = 2
        for _ in range(2):
            self.assertEqual(self.say("余额", [call("get_balance")])["understanding"], "deepseek")
        turn = self.say("余额")
        self.assertEqual(self.chat.store.llm_usage("demo")["calls"], 2)
        self.assertEqual((turn["understanding"], turn["fallback_reason"]), ("rules", "LLM_BUDGET_EXHAUSTED"))
        self.assertTrue(self.cards(turn, "report"))  # the bank keeps working

    def test_busy_gate_falls_back(self):
        for _ in range(4):
            self.chat._llm_gate.acquire()
        self.chat.LLM_GATE_WAIT = 0.01
        try:
            self.assertEqual(self.say("余额", [call("get_balance")])["fallback_reason"], "LLM_BUSY")
        finally:
            for _ in range(4):
                self.chat._llm_gate.release()


class HeaderTests(unittest.TestCase):
    def test_headers(self):
        from backend.security.http_headers import HEADERS
        names = {k for k, _ in HEADERS}
        self.assertTrue({"Referrer-Policy", "X-Frame-Options", "Permissions-Policy"} <= names)


if __name__ == "__main__":
    unittest.main()


class SplitTests(Harness):
    def test_split_includes_organiser_by_default(self):
        turn = self.say("和张三、李四AA 300块", [call("propose_split", total={"value": "300", "span": "300块"},
                                                       participants=[{"value": "张三", "span": "张三"}, {"value": "李四", "span": "李四"}])])
        card = self.cards(turn, "report")[0]
        self.assertEqual((card["report_type"], card["self_share_minor"]), ("SPLIT_REQUEST", 10000))
        self.assertEqual([i["amount_minor"] for i in card["items"]], [10000, 10000])
        self.assertFalse(self.cards(turn, "confirm"))
        self.assertIn("不会从任何人的账户扣款", turn["reply"]["text"])

    def test_split_without_organiser_keeps_exact_total(self):
        turn = self.say("让张三李四平摊301", [call("propose_split", total={"value": "301", "span": "301"}, include_me=False,
                                                    participants=[{"value": "张三", "span": "张三"}, {"value": "李四", "span": "李四"}])])
        self.assertEqual(sum(i["amount_minor"] for i in self.cards(turn, "report")[0]["items"]), 30100)

    def test_rules_understand_colloquial_split(self):
        self.assertTrue(rule_calls("我跟张三AA了120", {})[0]["arguments"]["include_me"])
        self.assertFalse(rule_calls("让张三、李四平摊300元", {})[0]["arguments"]["include_me"])
