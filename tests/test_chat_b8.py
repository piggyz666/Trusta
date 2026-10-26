"""B8 business coverage: wish-fund cross-scenario plan, recurring transfers, card recovery / PIN reset,
risk questionnaire, transfer memo. All through the chat entry and the unchanged per-action authorization."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from backend.chat.llm import ScriptedToolClient
from backend.chat.service import ChatService
from backend.config import Config
from backend.domain import Rejected, digest
from backend.service import TrustaService


def call(tool, **arguments):
    return {"name": tool, "arguments": arguments}


class ChatHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = TrustaService(Config(db_path=str(Path(self.tmp.name) / "b8.db"), signing_key=b"b8-tests", communication_version=3,
                                      banking_version=5, strong_auth_secret="demo-secret"))
        self.script = {}
        self.chat = ChatService(self.s, ScriptedToolClient(self.script))
        session = self.chat.create_session("demo", "test")
        self.sid, self.rev, self.n = session["session_id"], session["revision"], 0
        patcher = patch.dict(os.environ, {"TRUSTA_DEMO_MODE": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def say(self, text, calls=None):
        if calls is not None:
            self.script[text] = calls
        self.n += 1
        turn = self.chat.send(self.sid, "demo", f"m{self.n}", self.rev, text)
        self.rev = turn["revision"]
        return turn

    def cards(self, turn, kind):
        return [c for c in turn["reply"]["cards"] if c["type"] == kind]

    def decide(self, card, approve=True, secret=None):
        result = self.chat.decide(self.sid, "demo", card["action_id"], card["action_hash"], approve,
                                  {"method": "DEMO_SECRET", "code": secret} if secret else None)
        self.rev = result["revision"]
        return result

    def balance(self, account="acct-demo"):
        return self.s.db.one("SELECT balance FROM accounts WHERE id=?", (account,))["balance"]

    def total_money(self):
        return self.s.db.one("SELECT SUM(balance) AS total FROM accounts")["total"]

    def pending_event(self, event_type):
        return next(e for e in self.s.events.list("demo") if e["status"] == "PENDING" and e["intent"]["event_type"] == event_type)

    def advance(self, event_type):
        view = self.chat.demo_advance(self.sid, "demo", self.pending_event(event_type)["id"])
        self.rev = view["revision"]
        return view



class B8ChatTests(ChatHarness):
    BIRTHDAY = "我爱人生日快到了，锁1000块，生日前两天订束花和蛋糕"
    BIRTHDAY_CALLS = [call("propose_wish_plan", name="爱人生日", lock_amount={"value": "1000", "span": "1000块"},
                           event={"id": "le-spouse-birthday", "span": "我爱人生日"},
                           items=[{"id": "m-flower", "span": "花"}, {"id": "m-cake", "span": "蛋糕"}], order_days_before=2)]

    def test_birthday_wish_plan_end_to_end(self):
        money = self.total_money()
        turn = self.say(self.BIRTHDAY, self.BIRTHDAY_CALLS)
        lock = self.cards(turn, "confirm")[0]
        self.assertEqual(lock["action_type"], "FUND_LOCK")
        self.assertEqual(lock["risk_level"], "YELLOW")
        self.assertEqual([n["status"] for n in turn["plan"]["nodes"]], ["AWAITING_CONFIRMATION", "SCHEDULED", "SCHEDULED"])
        self.assertEqual(turn["plan"]["nodes"][1]["amount_minor"], 49800)  # catalogue prices, not model output
        self.assertEqual(self.balance(), 1000000)  # nothing moves before consent
        self.assertEqual(self.decide(lock)["reply"]["cards"][0]["status"], "SUCCEEDED")
        self.assertEqual(self.balance(), 900000)
        self.assertEqual(self.balance("wish-pool"), 100000)
        view = self.advance("WISH_ORDER")
        order = view["pending"][0]
        self.assertEqual(order["action_type"], "WISH_ORDER")
        self.assertIn("心愿金下单到时间了", view["messages"][-1]["text"])
        self.assertEqual([f["amount_minor"] for f in order["fields"] if "amount_minor" in f], [49800])
        self.assertEqual(self.decide(order)["reply"]["cards"][0]["status"], "SUCCEEDED")
        self.assertEqual(self.balance("wish-pool"), 50200)
        release = self.advance("WISH_RELEASE")["pending"][0]
        self.assertEqual(release["action_type"], "FUND_RELEASE")
        self.decide(release)
        self.assertEqual(self.balance(), 950200)
        self.assertEqual(self.balance("wish-pool"), 0)
        self.assertEqual(self.total_money(), money)  # double-entry: money only moved, never created
        self.assertTrue(self.s.audit.verify_chain())

    def test_declined_lock_cancels_follow_up_events(self):
        lock = self.cards(self.say(self.BIRTHDAY, self.BIRTHDAY_CALLS), "confirm")[0]
        self.decide(lock, approve=False)
        self.assertEqual({e["status"] for e in self.s.events.list("demo")}, {"CANCELLED"})
        self.assertEqual(self.balance(), 1000000)

    def test_order_exceeding_lock_is_a_question(self):
        calls = [call("propose_wish_plan", name="生日", lock_amount={"value": "300", "span": "300"},
                      event={"id": "le-spouse-birthday", "span": "爱人生日"},
                      items=[{"id": "m-flower", "span": "花"}, {"id": "m-cake", "span": "蛋糕"}])]
        turn = self.say("爱人生日锁300订花和蛋糕", calls)
        self.assertEqual(self.cards(turn, "clarify")[0]["slot"], "amount")
        self.assertEqual(self.s.events.list("demo"), [])

    def test_recurring_transfer_creates_proposals_each_period(self):
        # Relative to "now": a hard-coded date would silently turn into a past date and break the suite later.
        from datetime import datetime
        from backend.business import CHINA
        today = datetime.now(CHINA)
        first = today.replace(year=today.year + today.month // 12, month=today.month % 12 + 1, day=1, hour=9, minute=0, second=0, microsecond=0)
        second_month = "%02d" % (first.month % 12 + 1)
        calls = [call("propose_transfer", payee={"value": "房东", "span": "房东"}, amount={"value": "3000", "span": "3000"},
                      when={"iso": first.isoformat(), "span": "每月1号上午9点"},
                      recurrence={"frequency": "MONTHLY", "count": 12, "span": "每月"})]
        turn = self.say("每月1号上午9点给房东转3000", calls)
        plan = self.cards(turn, "report")[0]
        self.assertEqual(plan["report_type"], "STANDING_PLAN")
        self.assertFalse(self.cards(turn, "confirm"))
        view = self.advance("RECURRING_TRANSFER")
        card = view["pending"][0]
        self.assertEqual((card["action_type"], card["fields"][0]["value"]), ("TRANSFER", "张三"))
        nxt = self.pending_event("RECURRING_TRANSFER")
        self.assertEqual(nxt["intent"]["parameters"]["remaining"], 11)
        self.assertEqual(datetime.fromtimestamp(nxt["execute_at"], CHINA).strftime("%m"), second_month)
        self.decide(card)
        self.assertEqual(self.balance(), 700000)

    def test_recurrence_needs_first_run_time(self):
        calls = [call("propose_transfer", payee={"value": "张三", "span": "张三"}, amount={"value": "100", "span": "100"},
                      recurrence={"frequency": "WEEKLY", "count": 4, "span": "每周"})]
        self.assertEqual(self.cards(self.say("每周给张三转100", calls), "clarify")[0]["slot"], "when")

    def test_card_lost_then_found_and_pin_reset(self):
        lost = self.cards(self.say("卡丢了", [call("propose_card_operation", op="REPORT_LOST")]), "confirm")[0]
        self.decide(lost, secret="demo-secret")
        self.assertEqual(self.s.gateway.read("service", "demo", "card.get", resource_id="card-demo")["status"], "LOST")
        found = self.cards(self.say("卡找到了，解除挂失", [call("propose_card_operation", op="CANCEL_LOST")]), "confirm")[0]
        self.assertEqual((found["action_type"], found["risk_level"]), ("CARD_CANCEL_LOST", "RED"))
        self.decide(found, secret="demo-secret")
        self.assertEqual(self.s.gateway.read("service", "demo", "card.get", resource_id="card-demo")["status"], "ACTIVE")
        pin = self.cards(self.say("我要改卡密码", [call("propose_card_operation", op="PIN_RESET")]), "confirm")[0]
        self.assertEqual(pin["risk_level"], "RED")
        done = self.decide(pin, secret="demo-secret")
        self.assertIn("不会经过我", done["reply"]["text"])
        self.assertEqual(self.s.db.one("SELECT channel FROM card_pin_resets")["channel"], "SMS_LINK")

    def test_risk_questionnaire(self):
        card = self.cards(self.say("帮我做个风险测评", [call("start_risk_assessment")]), "questionnaire")[0]
        self.assertEqual(len(card["questions"]), 5)
        with self.assertRaises(Rejected):
            self.chat.submit_assessment(self.sid, "demo", {"q1": "A"})
        result = self.chat.submit_assessment(self.sid, "demo", {"q1": "B", "q2": "C", "q3": "C", "q4": "C", "q5": "C"})
        self.rev = result["revision"]
        self.assertIn("R4 成长型", result["reply"]["text"])
        self.decide(result["reply"]["cards"][0])
        self.assertEqual(self.s.wealth.risk_profile("demo")["risk_level"], 4)
        conservative = self.chat.submit_assessment(self.sid, "demo", {"q1": "A", "q2": "D", "q3": "A", "q4": "D", "q5": "D"})
        self.assertIn("R1 保守型", conservative["reply"]["text"])

    def test_memo_shown_and_must_be_users_words(self):
        calls = [call("propose_transfer", payee={"value": "张三", "span": "张三"}, amount={"value": "500", "span": "500"},
                      memo={"text": "生活费", "span": "备注生活费"})]
        card = self.cards(self.say("给张三转500，备注生活费", calls), "confirm")[0]
        self.assertIn({"label": "备注", "value": "生活费"}, card["fields"])
        self.assertIn("备注：生活费", self.decide(card)["reply"]["text"])
        bad = [call("propose_transfer", payee={"value": "张三", "span": "张三"}, amount={"value": "500", "span": "500"},
                    memo={"text": "货款", "span": "500"})]
        self.assertEqual(self.cards(self.say("再给张三转500", bad), "clarify")[0]["question"], "备注写什么？")

    def test_demo_advance_is_off_by_default(self):
        self.say(self.BIRTHDAY, self.BIRTHDAY_CALLS)
        with patch.dict(os.environ, {"TRUSTA_DEMO_MODE": "false"}):
            with self.assertRaises(Rejected) as caught:
                self.chat.demo_advance(self.sid, "demo", self.pending_event("WISH_ORDER")["id"])
        self.assertEqual(caught.exception.code, "DEMO_MODE_DISABLED")


class WishFundBankRuleTests(unittest.TestCase):
    """Bank-side rules hold even if a caller bypasses the chat validator."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = TrustaService(Config(db_path=str(Path(self.tmp.name) / "bank.db"), signing_key=b"b8-bank", communication_version=3,
                                      banking_version=5, strong_auth_secret="x"))

    def create(self, operation, parameters, target="wish-unit0001"):
        return self.s.banking.create("t", "demo", "acct-demo", {"task_type": "WISH_FUND_MANAGEMENT", "operation": operation,
                                                                "target_id": target, "parameters": parameters, "needs_clarification": False})

    def approve(self, task):
        entry = task["action_records"][0]
        return self.s.banking.authorize(task["task_id"], "demo", digest(entry["action"]), True, "x", entry["action"]["action_id"])

    def test_price_and_balance_rules(self):
        self.approve(self.create("FUND_LOCK", {"amount": 30000, "currency": "CNY", "name": "x", "release_at": time.time() + 86400}))
        fake_price = self.create("WISH_ORDER", {"amount": 100, "currency": "CNY", "items": [{"merchant_id": "m-flower", "amount": 100}]})
        self.assertIn("MERCHANT_PRICE_MISMATCH", fake_price["action_records"][0]["policy"]["reason_codes"])
        too_much = self.create("WISH_ORDER", {"amount": 79900, "currency": "CNY", "items": [
            {"merchant_id": "m-flower", "amount": 29900}, {"merchant_id": "m-gift", "amount": 50000}]})
        self.assertIn("WISH_FUND_INSUFFICIENT", too_much["action_records"][0]["policy"]["reason_codes"])
        again = self.create("FUND_LOCK", {"amount": 100, "currency": "CNY", "name": "x", "release_at": time.time() + 86400})
        self.assertIn("WISH_FUND_EXISTS", again["action_records"][0]["policy"]["reason_codes"])

    def test_other_customer_cannot_touch_fund(self):
        self.approve(self.create("FUND_LOCK", {"amount": 100, "currency": "CNY", "name": "x", "release_at": time.time() + 86400}))
        with self.assertRaises(Rejected):
            self.s.gateway.read("service", "mallory", "wish_fund.get", resource_id="wish-unit0001")


if __name__ == "__main__":
    unittest.main()


class TakeoverUndoPlanTests(ChatHarness):
    """人工接管 / 回退 / 多步计划 / 主动提醒."""
    def setUp(self):
        super().setUp()
        from dataclasses import replace
        self.s.config = replace(self.s.config, security_operator_token="operator-secret")

    def test_human_takeover_and_operator_cannot_approve(self):
        self.say("给张三转100", [call("propose_transfer", payee={"value": "张三", "span": "张三"}, amount={"value": "100", "span": "100"})])
        turn = self.say("我要转人工", [call("request_human", reason="用户要求")])
        self.assertEqual(turn["state"], "HANDED_OFF")
        handoff = self.cards(turn, "handoff")[0]["handoff_id"]
        queued = self.say("我的卡被盗刷了怎么办")  # not sent to the model while a human owns the conversation
        self.assertIn(handoff, queued["reply"]["text"])
        with self.assertRaises(Rejected):
            self.chat.operator_handoffs("wrong")
        case = self.chat.operator_handoffs("operator-secret")["handoffs"][0]
        self.assertEqual(case["queue"][0]["text"], "我的卡被盗刷了怎么办")
        self.assertEqual(case["pending"], ["转账给 张三"])
        self.chat.operator_reply("operator-secret", handoff, "您好，建议先冻结卡片。", close=True)
        view = self.chat.get(self.sid, "demo")
        self.rev = view["revision"]
        self.assertEqual(view["messages"][-2]["role"], "human_agent")
        self.assertEqual(view["state"], "AWAITING_CONFIRMATION")  # the transfer still waits for the customer's own decision
        self.assertEqual(self.balance(), 1000000)

    def test_user_can_leave_takeover(self):
        self.say("转人工", [call("request_human")])
        back = self.say("取消转人工")
        self.assertEqual(back["state"], "IDLE")

    def test_undo_creates_inverse_proposal(self):
        freeze = self.cards(self.say("冻结卡", [call("propose_card_operation", op="FREEZE")]), "confirm")[0]
        self.decide(freeze)
        undo = self.say("刚才冻结错了，恢复一下", [call("undo_last")])
        inverse = self.cards(undo, "confirm")[0]
        self.assertEqual((inverse["action_type"], inverse["risk_level"]), ("CARD_UNFREEZE", "RED"))
        self.decide(inverse, secret="demo-secret")
        self.assertEqual(self.s.gateway.read("service", "demo", "card.get", resource_id="card-demo")["status"], "ACTIVE")
        limit = self.cards(self.say("限额调到2000", [call("propose_card_operation", op="LIMIT_CHANGE",
                                                       limit={"value": "2000", "span": "2000"})]), "confirm")[0]
        self.decide(limit)
        back = self.cards(self.say("改回去", [call("undo_last")]), "confirm")[0]
        self.assertIn({"label": "新限额", "value": "¥5,000.00", "amount_minor": 500000, "currency": "CNY"}, back["fields"])

    def test_transfer_undo_is_explained_not_faked(self):
        card = self.cards(self.say("给张三转100", [call("propose_transfer", payee={"value": "张三", "span": "张三"},
                                                       amount={"value": "100", "span": "100"})]), "confirm")[0]
        self.decide(card)
        turn = self.say("撤回刚才的转账", [call("undo_last")])
        self.assertIn("无法单方面撤回", turn["reply"]["text"])
        self.assertFalse(self.cards(turn, "confirm"))

    def test_multi_step_plan_tracks_each_node(self):
        turn = self.say("查余额，冻结卡，再给张三转100", [
            call("get_balance"), call("propose_card_operation", op="FREEZE"),
            call("propose_transfer", payee={"value": "张三", "span": "张三"}, amount={"value": "100", "span": "100"})])
        # Writes in a money-moving turn run one after another (each is proposed on the state the previous one left).
        self.assertEqual([n["status"] for n in turn["plan"]["nodes"]], ["SUCCEEDED", "AWAITING_CONFIRMATION", "QUEUED"])
        self.assertEqual(turn["plan"]["nodes"][2]["depends_on"], ["n2"])
        freeze = self.cards(turn, "confirm")[0]
        after = self.decide(freeze)
        self.assertEqual([n["status"] for n in after["plan"]["nodes"]], ["SUCCEEDED", "SUCCEEDED", "AWAITING_CONFIRMATION"])
        stopped = self.chat.interrupt(self.sid, "demo", self.rev)
        self.assertEqual(stopped["plan"]["nodes"][2]["status"], "CANCELLED")

    def test_renewal_nudge_but_no_unprompted_birthday_message(self):
        from datetime import datetime, timedelta
        from backend.business import CHINA
        soon = datetime.now(CHINA) + timedelta(days=10)
        with self.s.db.transaction() as conn:
            conn.execute("DELETE FROM life_events")
            conn.execute("INSERT INTO life_events VALUES('le-test','demo','小美','女儿','BIRTHDAY',?,?)", (soon.month, soon.day))
            conn.execute("UPDATE subscriptions SET next_charge_at=? WHERE id='sub-stream'", (time.time() + 2 * 86400,))
        session = self.chat.create_session("demo", "test")
        self.assertEqual(session["messages"], [])  # nothing before the customer speaks
        reply = self.chat.send(session["session_id"], "demo", "r1", session["revision"], "查一下余额", "acct-demo")
        self.assertIn("演示视频", reply["reply"]["text"])
        self.assertIn("扣费", reply["reply"]["text"])
        self.assertNotIn("生日", reply["reply"]["text"])  # life events are never announced unprompted
        again = self.chat.send(session["session_id"], "demo", "r2", reply["revision"], "查一下余额", "acct-demo")
        self.assertNotIn("演示视频", again["reply"]["text"])  # each reminder once
