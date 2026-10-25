"""生活服务: link-out only. The customer's words become a pre-filled search on an allow-listed partner site."""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from backend.chat import life
from backend.chat.rules import rule_calls
from backend.chat.service import ChatService
from backend.config import Config
from backend.service import TrustaService
from tests.test_chat_b8 import ScriptedToolClient

NOW = datetime(2026, 10, 25, 17, 0)  # a Sunday


def first(text):
    card, said = life.understand(text, NOW)
    return card, said, card["links"][0]["url"]


class LifeParsingTest(unittest.TestCase):
    def test_one_way_flight_with_relative_date(self):
        card, said, url = first("帮我查下周五北京到上海的机票")
        self.assertEqual(url, "https://flights.ctrip.com/online/list/oneway-bjs-sha?depdate=2026-10-30&cabin=y_s&adult=1")
        self.assertTrue(card["complete"])
        self.assertIn("不参与交易", said)

    def test_round_trip_from_two_dates(self):
        _, _, url = first("我想11月6号从北京飞成都，9号回来")
        self.assertIn("round-bjs-ctu?depdate=2026-11-06_2026-11-09", url)

    def test_international_flight(self):
        _, _, url = first("11月20日上海到东京的机票")
        self.assertIn("oneway-sha-tyo?depdate=2026-11-20", url)

    def test_train_uses_12306_station_codes(self):
        _, _, url = first("明天上海到杭州的高铁")
        query = urlparse(url).query
        self.assertIn("fs=%E4%B8%8A%E6%B5%B7%2CSHH", query)
        self.assertIn("ts=%E6%9D%AD%E5%B7%9E%2CHZH", query)
        self.assertIn("date=2026-10-26", query)

    def test_hotel_nights(self):
        _, _, url = first("帮我订三亚的酒店，11月10号住3晚")
        self.assertEqual(url, "https://hotels.ctrip.com/hotels/list?city=43&checkin=2026/11/10&checkout=2026/11/13")

    def test_show_keyword_is_the_artist_not_filler(self):
        card, _, url = first("我想看周深的演唱会")
        self.assertTrue(url.endswith("keyword=%E5%91%A8%E6%B7%B1"))
        card, _, url = first("11月杭州的音乐节")
        self.assertIn("%E6%9D%AD%E5%B7%9E", url)  # 杭州 kept, "11月" dropped
        self.assertNotIn("11%E6%9C%88", url)

    def test_missing_city_falls_back_to_home_page(self):
        card, said, url = first("我想订机票")
        self.assertFalse(card["complete"])
        self.assertEqual(url, "https://flights.ctrip.com/")
        self.assertIn("出发和目的城市", said)

    def test_every_link_is_https_on_the_allow_list(self):
        for text in ["北京到上海的机票", "明天上海到杭州的高铁", "成都的酒店", "上海的演唱会", "交电费", "社保怎么查", "看电影"]:
            card, _ = life.understand(text, NOW)
            for link in card["links"]:
                parsed = urlparse(link["url"])
                self.assertEqual(parsed.scheme, "https")
                self.assertIn(parsed.netloc, life.ALLOWED_HOSTS)

    def test_banking_requests_are_not_life_services(self):
        catalog = {"direct_debits": [{"id": "dd-electric", "display": "演示电力"}]}
        self.assertEqual(rule_calls("关闭演示电力代扣", catalog)[0]["name"], "propose_direct_debit_disable")
        self.assertFalse(life.wants_life_service("每月电费超过200就提醒我"))
        self.assertFalse(life.wants_life_service("给张三转500元"))


class LifeChatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.dict(os.environ, {"TRUSTA_DEMO_MODE": "true"})
        patcher.start()
        self.addCleanup(patcher.stop)
        s = TrustaService(Config(db_path=str(Path(self.tmp.name) / "l.db"), signing_key=b"life", communication_version=3,
                                 banking_version=5, strong_auth_secret="x"))
        self.chat = ChatService(s, ScriptedToolClient({}))
        session = self.chat.create_session("demo", "test")
        self.sid, self.rev, self.n = session["session_id"], session["revision"], 0

    def say(self, text):
        self.n += 1
        reply = self.chat.send(self.sid, "demo", f"m{self.n}", self.rev, text, "acct-demo")
        self.rev = reply["revision"]
        return reply["reply"]

    def test_follow_up_completes_the_search(self):
        first_reply = self.say("下周六飞往三亚")
        self.assertIn("出发城市", first_reply["text"])
        second = self.say("从上海出发")
        card = next(c for c in second["cards"] if c["type"] == "life")
        self.assertTrue(card["complete"])
        self.assertIn("oneway-sha-syx", card["links"][0]["url"])

    def test_life_card_never_creates_a_bank_action(self):
        reply = self.say("帮我查下周五北京到上海的机票")
        self.assertEqual([c["type"] for c in reply["cards"]], ["life"])
        self.assertEqual(self.chat.get(self.sid, "demo")["pending"], [])


if __name__ == "__main__":
    unittest.main()
