"""Rates and yields are not market quotes (found by benchmarks/compare: "储蓄账户利率多少" was answered with gold and FX prices)."""
import unittest

from tests import test_chat as tc
from tests.test_chat import call


class MarketRoutingTests(unittest.TestCase):
    profile = "competition"
    setUp = tc.ChatServiceTests.setUp
    say = tc.ChatServiceTests.say

    def test_rate_question_routed_to_quotes_gets_an_honest_answer(self):
        turn = self.say("我的储蓄账户利率多少", [call("knowledge", topic="MARKET_QUOTES")])
        self.assertIn("查不到", turn["reply"]["text"])
        self.assertNotIn("元/克", turn["reply"]["text"])

    def test_gold_price_question_still_gets_quotes(self):
        turn = self.say("现在金价多少", [call("knowledge", topic="MARKET_QUOTES")])
        self.assertIn("黄金", turn["reply"]["text"])


if __name__ == "__main__":
    unittest.main()
