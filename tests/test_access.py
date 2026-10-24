"""Access modes (local / LAN / tunnel), phone pairing, device tokens, Feishu binding and the long-connection transport."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from backend.access import AccessControl, lan_ip
from backend.channels.feishu import FeishuChannel, FeishuConfig
from backend.chat.llm import ScriptedToolClient
from backend.chat.service import ChatService
from backend.config import Config
from backend.domain import Rejected
from backend.server import create_server
from backend.service import TrustaService
from scripts.feishu_worker import RawEventHandler


def call(tool, **arguments):
    return {"name": tool, "arguments": arguments}


class Base(unittest.TestCase):
    mode, public_host = "lan", ""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = TrustaService(Config(db_path=str(Path(self.tmp.name) / "acc.db"), signing_key=b"access", communication_version=3,
                                      banking_version=5))
        self.access = AccessControl(self.s, mode=self.mode, public_host=self.public_host)


class PairingTests(Base):
    def test_code_is_single_use_and_wrong_guesses_burn_it(self):
        code = self.access.start_pairing("demo", "device")["code"]
        device = self.access.pair_device(code, "我的手机")
        self.assertEqual(self.access.device_owner(device["device_token"]), "demo")
        with self.assertRaises(Rejected):
            self.access.pair_device(code, "再来一次")
        code = self.access.start_pairing("demo", "device")["code"]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            with self.assertRaises(Rejected):
                self.access.pair_device(wrong, "x")
        with self.assertRaises(Rejected):
            self.access.pair_device(code, "x")  # five misses exhausted the live code

    def test_expired_code_and_revocation(self):
        code = self.access.start_pairing("demo", "device")["code"]
        with self.s.db.transaction() as conn:
            conn.execute("UPDATE pairing_codes SET expires_at=?", (time.time() - 1,))
        with self.assertRaises(Rejected):
            self.access.pair_device(code, "x")
        device = self.access.pair_device(self.access.start_pairing("demo", "device")["code"], "x")
        self.access.revoke("demo", device["device_id"])
        self.assertIsNone(self.access.device_owner(device["device_token"]))

    def test_global_throttle(self):
        for _ in range(20):
            with self.assertRaises(Rejected):
                self.access.claim("123456", "device")
        with self.assertRaises(Rejected) as caught:
            self.access.claim("123456", "device")
        self.assertEqual(caught.exception.code, "PAIRING_RATE_LIMITED")

    def test_local_mode_does_not_pair_phones(self):
        with self.assertRaises(Rejected):
            AccessControl(self.s, mode="local").start_pairing("demo", "device")


class LanHTTPTests(Base):
    def setUp(self):
        super().setUp()
        self.server = create_server(self.s, "master", 0, chat_service=ChatService(self.s, ScriptedToolClient({})), access=self.access)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_port

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def request(self, path, body=None, token=None, host=None):
        headers = {"Content-Type": "application/json", "Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = Request(f"http://127.0.0.1:{self.port}{path}", data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read())
        except HTTPError as response:
            with response:
                return response.code, json.loads(response.read())

    def test_phone_pairs_and_uses_its_own_token(self):
        phone = f"{lan_ip()}:{self.port}"
        # The master token is refused from the phone's address; only this computer may mint codes.
        self.assertEqual(self.request("/api/chat/capabilities", token="master", host=phone)[0], 403)
        self.assertEqual(self.request("/api/pairing/start", {"purpose": "device"}, token="master", host=phone)[0], 403)
        status, started = self.request("/api/pairing/start", {"purpose": "device"}, token="master")
        self.assertEqual(status, 201)
        self.assertIn(f"#pair={started['code']}", started["pair_url"])
        status, paired = self.request("/api/pairing/claim", {"code": started["code"], "name": "iPhone"}, host=phone)
        self.assertEqual(status, 201)
        token = paired["device_token"]
        status, session = self.request("/api/chat/sessions", {"channel": "app"}, token=token, host=phone)
        self.assertEqual(status, 201)
        # A paired phone cannot mint further pairing codes.
        self.assertEqual(self.request("/api/pairing/start", {"purpose": "device"}, token=token, host=phone)[0], 403)
        status, info = self.request("/api/access", token="master")
        self.assertEqual((info["mode"], len(info["devices"])), ("lan", 1))
        self.assertEqual(self.request(f"/api/pairing/devices/{paired['device_id']}/revoke", {}, token="master")[0], 200)
        self.assertEqual(self.request("/api/chat/capabilities", token=token, host=phone)[0], 403)

    def test_unknown_host_is_refused(self):
        self.assertEqual(self.request("/api/chat/capabilities", token="master", host="evil.example:80")[0], 403)
        self.assertEqual(self.request("/api/pairing/claim", {"code": "123456"}, host="evil.example:80")[0], 403)


class TunnelTests(Base):
    mode, public_host = "tunnel", "demo.trycloudflare.com"

    def test_tunnel_requests_are_remote_even_from_loopback(self):
        self.assertFalse(self.access.is_local("demo.trycloudflare.com", "127.0.0.1", 8000))
        self.assertTrue(self.access.is_local("127.0.0.1:8000", "127.0.0.1", 8000))
        self.assertIn("https://demo.trycloudflare.com", self.access.allowed_origins(8000))
        self.assertTrue(self.access.start_pairing("demo", "device")["pair_url"].startswith("https://demo.trycloudflare.com/#pair="))


class FakeSender:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, msg_type, content):
        self.sent.append((chat_id, msg_type, content))


class FeishuLongConnectionTests(Base):
    mode = "local"

    def setUp(self):
        super().setUp()
        self.s.access = self.access
        script = {"查余额": [call("get_balance")],
                  "给张三转100": [call("propose_transfer", payee={"value": "张三", "span": "张三"}, amount={"value": "100", "span": "100"})]}
        self.chat = ChatService(self.s, ScriptedToolClient(script))
        self.sender = FakeSender()
        self.config = FeishuConfig(app_id="cli_app", app_secret="secret")
        self.assertEqual((self.config.mode, self.config.enabled), ("longconn", True))
        self.channel = FeishuChannel(self.chat, self.config, self.sender, synchronous=True)
        self.handler = RawEventHandler(self.channel)
        self.n = 0

    def message(self, text, open_id="ou_bob", chat_type="p2p", app_id="cli_app"):
        self.n += 1
        return json.dumps({"schema": "2.0", "header": {"event_id": f"ev{self.n}", "event_type": "im.message.receive_v1", "app_id": app_id},
                           "event": {"sender": {"sender_id": {"open_id": open_id}},
                                     "message": {"message_id": f"om{self.n}", "chat_id": "oc_bob", "chat_type": chat_type,
                                                 "message_type": "text", "content": json.dumps({"text": text})}}}).encode()

    def test_bind_with_code_then_chat_and_confirm(self):
        self.handler._do_without_validation(self.message("查余额"))
        self.assertIn("还没有绑定", self.sender.sent[-1][2]["text"])
        code = self.access.start_pairing("demo", "feishu")["code"]
        self.handler._do_without_validation(self.message(f"绑定 {code}", chat_type="group"))
        self.assertIsNone(self.access.channel_owner("feishu", "ou_bob"))  # never in a group chat
        self.handler._do_without_validation(self.message(f"绑定 {code}"))
        self.assertIn("绑定成功", self.sender.sent[-1][2]["text"])
        self.handler._do_without_validation(self.message("给张三转100"))
        card = self.sender.sent[-1][2]
        value = card["elements"][-1]["actions"][0]["value"]
        self.n += 1
        result = self.handler._do_without_validation(json.dumps({"schema": "2.0", "header": {
            "event_id": f"cb{self.n}", "event_type": "card.action.trigger", "app_id": "cli_app"},
            "event": {"operator": {"open_id": "ou_bob"}, "action": {"value": value}}}).encode())
        self.assertEqual(result["toast"]["type"], "success")

    def test_events_for_another_app_are_ignored(self):
        self.assertIsNone(self.handler._do_without_validation(self.message("查余额", app_id="cli_other")))
        self.assertEqual(self.sender.sent, [])


if __name__ == "__main__":
    unittest.main()


class FeishuRuleCardTests(FeishuLongConnectionTests):
    def test_rule_card_buttons_activate_rule(self):
        self.chat.llm.script["余额低于1000提醒我"] = [call("propose_automation", trigger={"type": "BALANCE_BELOW", "span": "余额低于1000",
                                                                                     "amount": {"value": "1000", "span": "1000"}}, actions=[{"type": "NOTIFY"}])]
        self.handler._do_without_validation(self.message(f"绑定 {self.access.start_pairing('demo', 'feishu')['code']}"))
        self.handler._do_without_validation(self.message("余额低于1000提醒我"))
        card = self.sender.sent[-1][2]
        self.assertEqual(card["header"]["title"]["content"], "确认联动规则")
        value = card["elements"][-1]["actions"][0]["value"]
        self.n += 1
        result = self.handler._do_without_validation(json.dumps({"schema": "2.0", "header": {
            "event_id": f"cb{self.n}", "event_type": "card.action.trigger", "app_id": "cli_app"},
            "event": {"operator": {"open_id": "ou_bob"}, "action": {"value": value}}}).encode())
        self.assertEqual(result["toast"]["type"], "success")
        self.assertEqual(len(self.s.automation.rules("demo")), 1)


class ProseOfferTests(Base):
    """Live finding: the model answered “需要我帮你挂失吗？” in prose instead of calling the tool."""
    mode = "local"

    def test_actionable_prose_becomes_card_and_offers_are_never_shown(self):
        class Prose(ScriptedToolClient):
            def complete(self, prompt, history, tools=None):
                return {"calls": [], "usage": None, "text": {
                    "我的卡丢了怎么办": "卡片丢失后建议先挂失。需要我现在帮你挂失这张借记卡吗？",
                    "信息呢": "抱歉，刚才没有展示待确认的信息。现在为你准备挂失操作，确认后即执行挂失。"}[history[-1]["content"]]}
        chat = ChatService(self.s, Prose({}))
        session = chat.create_session("demo", "test")
        turn = chat.send(session["session_id"], "demo", "p1", session["revision"], "我的卡丢了怎么办")
        self.assertEqual([c["action_type"] for c in turn["reply"]["cards"] if c["type"] == "confirm"], ["CARD_REPORT_LOST"])
        turn = chat.send(session["session_id"], "demo", "p2", turn["revision"], "信息呢")
        self.assertNotIn("准备挂失", turn["reply"]["text"])


class FeishuAppLinkTests(FeishuLongConnectionTests):
    mode = "lan"
    def test_red_action_link_points_at_a_reachable_address_and_the_conversation(self):
        link = self.channel.app_link("cs_abc12345")
        # Base runs in LAN mode: the phone gets this computer's LAN address, not the loopback default
        self.assertEqual(link, self.access.urls(8000)["lan"] + "?s=cs_abc12345")
        card = self.channel.confirm_card("cs_abc12345", {"risk_level": "RED", "title": "挂失卡片", "fields": [{"label": "卡片", "value": "借记卡"}],
                                                         "action_id": "a", "action_hash": "h"})
        self.assertIn("?s=cs_abc12345", json.dumps(card, ensure_ascii=False))
        self.channel.config.app_url = "https://bank.example.com"
        self.assertEqual(self.channel.app_link("cs_abc12345"), "https://bank.example.com/?s=cs_abc12345")
