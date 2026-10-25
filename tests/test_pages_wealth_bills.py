"""账单与订阅 page, 理财 page, market quotes, product-name matching and the no-stock-picks rule."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.chat.pages import BillsPage, WealthPage
from backend.chat.rules import rule_calls
from backend.chat.service import ChatService
from backend.config import Config
from backend.domain import Rejected
from backend.market import MarketQuotes, OZ
from backend.service import TrustaService
from tests.test_chat_b8 import ScriptedToolClient


def gold_ok():
    return {"value": 4000.0, "as_of": "2026-10-25T08:00:00Z", "source": "test-gold"}


def fx_ok():
    rates = {"USD": 7.0, "EUR": 8.0, "JPY": 0.05, "HKD": 0.9}
    return {k: {"value": v, "as_of": "2026-10-25T00:00:00Z", "source": "test-fx"} for k, v in rates.items()}


def down():
    raise OSError("offline")


class PagesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.dict(os.environ, {"TRUSTA_DEMO_MODE": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.s = TrustaService(Config(db_path=str(Path(self.tmp.name) / "p.db"), signing_key=b"pages", communication_version=3,
                                      banking_version=5, strong_auth_secret="x"))
        self.chat = ChatService(self.s, ScriptedToolClient({}))

    # ---- market -----------------------------------------------------------------------------------
    def test_quotes_convert_gold_to_yuan_per_gram_and_label_sources(self):
        m = MarketQuotes(self.s.db, (gold_ok,), (fx_ok,))
        view = m.view()
        gold = view["quotes"][0]
        self.assertTrue(view["live"])
        self.assertAlmostEqual(gold["price"], round(4000 * 7.0 / OZ, 2))
        self.assertEqual(gold["unit"], "元/克")
        jpy = next(q for q in view["quotes"] if q["symbol"] == "JPYCNY")
        self.assertAlmostEqual(jpy["price"], 5.0)  # shown per 100 yen
        self.assertTrue(all(q["source"] and q["as_of"] for q in view["quotes"]))

    def test_offline_never_blank_and_says_so(self):
        view = MarketQuotes(self.s.db, (down,), (down,)).view()
        self.assertFalse(view["live"])
        self.assertTrue(all(q["snapshot"] and "未联网" in q["source"] for q in view["quotes"]))
        self.assertTrue(view["errors"])

    def test_last_good_value_survives_a_restart_and_a_dead_source(self):
        MarketQuotes(self.s.db, (gold_ok,), (fx_ok,)).view()
        later = MarketQuotes(self.s.db, (down,), (down,))  # new process, sources unreachable
        view = later.view(force=True)
        self.assertFalse(any(q["snapshot"] for q in view["quotes"]))
        self.assertEqual(view["quotes"][1]["source"], "test-fx")

    def test_implausible_source_falls_through_to_next(self):
        def bad():
            raise ValueError("implausible")
        view = MarketQuotes(self.s.db, (bad, gold_ok), (fx_ok,)).view()
        self.assertEqual(view["quotes"][0]["source"], "test-gold")

    # ---- bills ------------------------------------------------------------------------------------
    def test_unregistered_monthly_charge_is_detected_after_three_times(self):
        page = BillsPage(self.chat).view("demo")
        self.assertEqual([d["merchant"] for d in page["detected"]], ["未登记商户"])
        d = page["detected"][0]
        self.assertEqual((d["count"], d["state"]), (3, "NEW"))
        self.assertGreater(d["next_estimate"], time.time() - 86400)  # rolled forward, never a date in the past
        self.assertNotIn("演示视频", [x["merchant"] for x in page["detected"]])  # registered ones are not "detected"

    def test_two_charges_are_not_enough(self):
        with self.s.db.transaction() as conn:
            conn.execute("DELETE FROM recurring_charges WHERE id='rec-未登记商户-9'")
        self.assertEqual(BillsPage(self.chat).view("demo")["detected"], [])

    def test_confirm_adds_to_monthly_total_and_dismiss_hides(self):
        page = BillsPage(self.chat)
        key = page.view("demo")["detected"][0]["key"]
        before = page.view("demo")["monthly_minor"]
        self.assertEqual(page.mark("demo", key, "CONFIRMED")["monthly_minor"], before + 1500)
        self.assertEqual(page.mark("demo", key, "DISMISSED")["monthly_minor"], before)
        with self.assertRaises(Rejected):
            page.mark("demo", "made-up|1|CNY", "CONFIRMED")
        with self.assertRaises(Rejected):
            page.mark("demo", key, "DELETE")

    def test_new_subscription_is_announced_once_across_chats(self):
        first = [m["text"] for m in self.chat.create_session("demo", "test")["messages"]]
        self.assertTrue(any("未登记商户" in t and "可能的订阅" in t for t in first))
        again = ChatService(self.s, ScriptedToolClient({})).create_session("demo", "test")  # even after a restart
        self.assertFalse(any("可能的订阅" in m["text"] for m in again["messages"]))

    def test_confirmed_subscription_gets_reminder_before_estimated_charge(self):
        page = BillsPage(self.chat)
        d = page.view("demo")["detected"][0]
        page.mark("demo", d["key"], "CONFIRMED")
        self.chat.store.claim_nudge("demo", "recurring:" + d["key"])
        soon = time.time() + 2 * 86400
        with patch.object(BillsPage, "detected", lambda _self, owner: [d | {"state": "CONFIRMED", "next_estimate": soon}]):
            texts = [m["text"] for m in self.chat.create_session("demo", "test")["messages"]]
        self.assertTrue(any("预计" in t and "未登记商户" in t for t in texts))

    # ---- wealth -----------------------------------------------------------------------------------
    def test_shelf_marks_products_above_risk_level_and_hides_delisted(self):
        view = WealthPage(self.chat, MarketQuotes(None, (down,), (down,))).view("demo")
        self.assertEqual(view["risk"]["level"], 2)
        names = {p["id"]: p for p in view["products"]}
        self.assertNotIn("sandbox-inactive", names)
        self.assertTrue(names["sandbox-r2"]["suitable"])
        self.assertFalse(names["wp-r3-gold"]["suitable"])
        self.assertIn("R2", names["wp-r3-gold"]["blocked_reason"])
        self.assertTrue(all(p["benchmark"] and p["liquidity"] for p in view["products"]))

    def test_recommendation_stays_within_risk_level(self):
        profile = self.s.wealth.risk_profile("demo")
        picked = self.s.wealth.match(profile, self.s.wealth.products("demo"))
        self.assertTrue(picked and all(p["risk_level"] <= 2 for p in picked))

    # ---- language ---------------------------------------------------------------------------------
    def test_product_names_with_shared_series_prefix_match_the_right_product(self):
        catalog = self.chat.catalog("demo")
        cases = {"申购2000元安睿·稳健30天持有": "sandbox-r2", "申购1000元稳盈90天持有": "wp-r2-90d",
                 "申购100元安睿·活钱管理": "sandbox-r1", "申购1000元安睿·全球科技(QDII)": "wp-r5-global"}
        for text, product in cases.items():
            calls = rule_calls(text, catalog)
            self.assertEqual(calls[0]["arguments"]["product"]["id"], product, text)
        compare = rule_calls("比较一下活钱管理和七天周周开", catalog)[0]
        self.assertEqual([p["id"] for p in compare["arguments"]["products"]], ["sandbox-r1", "wp-r1-7d"])

    def test_stock_picks_are_declined_with_shelf_alternative(self):
        for text in ["推荐几支股票", "我该买哪只股票", "现在炒股买什么好"]:
            self.assertEqual(rule_calls(text, {})[0]["arguments"]["topic"], "NO_STOCK_PICKS", text)
        session = self.chat.create_session("demo", "test")
        reply = self.chat.send(session["session_id"], "demo", "m-stock", session["revision"], "推荐几支股票", "acct-demo")
        self.assertIn("不推荐具体股票", reply["reply"]["text"])

    def test_third_party_membership_is_explained_not_out_of_scope(self):
        session = self.chat.create_session("demo", "test")
        reply = self.chat.send(session["session_id"], "demo", "m-qq", session["revision"], "我要开通一个qq音乐的会员", "acct-demo")
        self.assertIn("对应的 App 里开通", reply["reply"]["text"])

    def test_gold_price_question_answers_with_source(self):
        from backend.chat import knowledge
        text, sources = knowledge.answer("MARKET_QUOTES", self.s.config, (), MarketQuotes(None, (gold_ok,), (fx_ok,)))
        self.assertIn("黄金", text)
        self.assertIn("test-gold", text)
        self.assertIn("不构成投资建议", text)


if __name__ == "__main__":
    unittest.main()
