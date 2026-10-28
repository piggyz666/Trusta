"""The declared permission tiers (backend/security/permission_matrix.py, 赛题“权限分级机制”) are what the runtime does.

Each case says something in a conversation and checks the tier of the card the system produces: GREEN shows the result
directly, YELLOW needs a confirmation (card or query card), RED needs a confirmation plus strong verification.
"""
import unittest

from backend.security.permission_matrix import MATRIX, TIERS, public_matrix
from tests import test_chat as tc
from tests.test_chat import call, transfer


def declared(operation):
    return {tier for op, tier, _ in MATRIX if op == operation}


class PermissionMatrixTests(unittest.TestCase):
    profile = "competition"
    setUp = tc.ChatServiceTests.setUp
    say = tc.ChatServiceTests.say
    cards = tc.ChatServiceTests.cards
    decide = tc.ChatServiceTests.decide

    def tier(self, turn):
        if self.cards(turn, "query_confirm"):
            return "YELLOW"
        confirm = self.cards(turn, "confirm")
        if confirm:
            return confirm[0]["risk_level"]
        return "GREEN" if self.cards(turn, "report") else None

    def check(self, operation, text, calls, expected):
        self.assertIn(expected, declared(operation), f"{operation} is not declared {expected}")
        got = self.tier(self.say(text, calls))
        self.assertEqual(got, expected, f"{operation}: {text}")

    def test_green_reads_run_directly(self):
        self.check("BALANCE", "查一下余额", [call("get_balance")], "GREEN")
        self.check("SPENDING_QUERY", "上个月花了多少", [call("analyze_spending", month="2026-09")], "GREEN")

    def test_yellow_needs_confirmation(self):
        self.check("TRANSFER", "给张三转100", [transfer("张三", "100", "100")], "YELLOW")
        self.check("SUBSCRIPTION_LIST", "我有哪些订阅", [call("list_subscriptions", detect=True)], "YELLOW")
        self.check("SUBSCRIPTION_CANCEL", "取消视频会员",
                   [call("propose_subscription_cancel", subscriptions=[{"id": "sub-stream", "span": "视频会员"}])], "YELLOW")
        self.check("CARD_APPLY", "申请一张虚拟卡", [call("propose_card_operation", op="APPLY_VIRTUAL")], "YELLOW")
        self.check("CARD_FREEZE", "冻结我的卡", [call("propose_card_operation", op="FREEZE")], "YELLOW")

    def test_red_needs_strong_verification(self):
        self.check("TRANSFER", "给张三转1500", [transfer("张三", "1500", "1500")], "RED")  # 日累计 > ¥1,000
        self.check("CARD_REPORT_LOST", "我的卡丢了", [call("propose_card_operation", op="REPORT_LOST")], "RED")
        self.check("CARD_PIN_RESET", "我要改密码", [call("propose_card_operation", op="PIN_RESET")], "RED")
        product = next(p["id"] for p in self.chat.catalog("demo")["products"])
        self.check("INVESTMENT_PURCHASE", "申购1000元", [call("propose_investment", op="PURCHASE", product={"id": product, "span": "申购"},
                                                              amount={"value": "1000", "span": "1000"})], "RED")
        self.check("ADD_PAYEE", "添加收款人王五13800001111",
                   [{"name": "propose_add_payee", "arguments": {"name": {"value": "王五", "span": "王五"},
                                                                "phone": {"value": "13800001111", "span": "13800001111"}}}], "RED")

    def test_daily_total_crosses_into_red(self):
        """小额转账（日累计 ≤ ¥1,000）为黄色；同一天累计超过 ¥1,000 起为红色。"""
        first = self.cards(self.say("给张三转600", [transfer("张三", "600", "600")]), "confirm")[0]
        self.assertEqual(first["risk_level"], "YELLOW")
        self.decide(first)
        second = self.cards(self.say("再给张三转500", [transfer("张三", "500", "500")]), "confirm")[0]
        self.assertEqual(second["risk_level"], "RED")

    def test_matrix_is_complete_and_public(self):
        table = public_matrix()
        self.assertEqual(set(table["tiers"]), set(TIERS))
        for op in ("TRANSFER", "SUBSCRIPTION_LIST", "SUBSCRIPTION_CANCEL", "CARD_APPLY", "CARD_REPORT_LOST", "CARD_CANCEL_LOST",
                   "CARD_PIN_RESET", "INVESTMENT_PURCHASE", "BALANCE"):
            self.assertTrue(declared(op), op)
        self.assertEqual(declared("SUBSCRIPTION_LIST"), {"YELLOW"})  # 赛题：订阅查询为黄色
        self.assertEqual(declared("CARD_PIN_RESET"), {"RED"})       # 赛题：密码修改为红色


if __name__ == "__main__":
    unittest.main()
