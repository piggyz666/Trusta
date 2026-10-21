"""Feishu channel: platform verification, binding, dedupe, YELLOW buttons, RED never completes in IM."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from backend.channels.feishu import FeishuChannel, FeishuConfig, encrypt
from backend.chat.llm import ScriptedToolClient
from backend.chat.service import ChatService
from backend.config import Config
from backend.domain import Rejected
from backend.server import create_server
from backend.service import TrustaService


class FakeSender:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, msg_type, content):
        self.sent.append((chat_id, msg_type, content))


def call(tool, **arguments):
    return {"name": tool, "arguments": arguments}


class FeishuTests(unittest.TestCase):
    TOKEN = "verify-token"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = TrustaService(Config(db_path=str(Path(self.tmp.name) / "fs.db"), signing_key=b"feishu", communication_version=3,
                                      banking_version=5, strong_auth_secret="x"))
        self.script = {
            "给张三转100": [call("propose_transfer", payee={"value": "张三", "span": "张三"}, amount={"value": "100", "span": "100"})],
            "我的卡丢了": [call("propose_card_operation", op="REPORT_LOST")],
            "查余额": [call("get_balance")],
        }
        self.chat = ChatService(self.s, ScriptedToolClient(self.script))
        self.sender = FakeSender()
        self.config = FeishuConfig(verification_token=self.TOKEN, bindings={"ou_alice": "demo"})
        self.channel = FeishuChannel(self.chat, self.config, self.sender, synchronous=True)
        self.n = 0

    def event(self, text, open_id="ou_alice", chat_id="oc_1", event_id=None):
        self.n += 1
        return json.dumps({"schema": "2.0", "header": {"event_id": event_id or f"ev{self.n}", "event_type": "im.message.receive_v1",
                                                       "token": self.TOKEN},
                           "event": {"sender": {"sender_id": {"open_id": open_id}},
                                     "message": {"message_id": f"om_{self.n}", "chat_id": chat_id, "message_type": "text",
                                                 "content": json.dumps({"text": text})}}}).encode()

    def click(self, value, open_id="ou_alice"):
        self.n += 1
        return json.dumps({"schema": "2.0", "header": {"event_id": f"cb{self.n}", "event_type": "card.action.trigger", "token": self.TOKEN},
                           "event": {"operator": {"open_id": open_id}, "action": {"value": value}}}).encode()

    def test_url_verification_and_token(self):
        raw = json.dumps({"type": "url_verification", "challenge": "abc", "token": self.TOKEN}).encode()
        self.assertEqual(self.channel.handle({}, raw), {"challenge": "abc"})
        with self.assertRaises(Rejected):
            self.channel.handle({}, json.dumps({"type": "url_verification", "challenge": "abc", "token": "wrong"}).encode())

    def test_message_reply_and_dedupe(self):
        raw = self.event("查余额", event_id="same")
        self.channel.handle({}, raw)
        self.assertIn("可用余额", self.sender.sent[-1][2]["text"])
        self.assertEqual(self.channel.handle({}, raw), {"code": 0, "duplicate": True})
        self.assertEqual(len(self.sender.sent), 1)

    def test_unbound_user(self):
        self.channel.handle({}, self.event("查余额", open_id="ou_stranger"))
        self.assertIn("绑定", self.sender.sent[-1][2]["text"])

    def test_yellow_card_button_confirms(self):
        self.channel.handle({}, self.event("给张三转100"))
        card = self.sender.sent[-1][2]
        buttons = card["elements"][-1]["actions"]
        self.assertEqual([b["text"]["content"] for b in buttons], ["确认", "拒绝"])
        stranger = self.channel.handle({}, self.click(buttons[0]["value"], open_id="ou_mallory"))
        self.assertEqual(stranger["toast"]["content"], "无权操作")
        result = self.channel.handle({}, self.click(buttons[0]["value"]))
        self.assertIn("银行核验通过", result["card"]["data"]["elements"][0]["content"])
        self.assertEqual(self.s.transaction.balance("demo", "acct-demo")["balance"], 990000)
        again = self.channel.handle({}, self.click(buttons[0]["value"]))
        self.assertEqual(again["toast"]["content"], "这项操作已经处理过了")

    def test_red_action_cannot_complete_in_im(self):
        self.channel.handle({}, self.event("我的卡丢了"))
        card = self.sender.sent[-1][2]
        self.assertEqual(card["header"]["template"], "red")
        self.assertIn("url", card["elements"][-1]["actions"][0])
        self.assertNotIn("value", card["elements"][-1]["actions"][0])
        session = self.s.db.one("SELECT session_id FROM channel_sessions")["session_id"]
        pending = self.chat.get(session, "demo")["pending"][0]
        forged = {"session_id": session, "action_id": pending["action_id"], "action_hash": pending["action_hash"], "approve": True}
        self.assertIn("APP", self.channel.handle({}, self.click(forged))["toast"]["content"])
        self.assertEqual(self.s.gateway.read("service", "demo", "card.get", resource_id="card-demo")["status"], "ACTIVE")

    def test_encrypted_and_signed_callbacks(self):
        self.channel.config.encrypt_key = "enc-key"
        payload = json.loads(self.event("查余额"))
        raw = json.dumps({"encrypt": encrypt("enc-key", payload)}).encode()
        headers = {"X-Lark-Request-Timestamp": "1", "X-Lark-Request-Nonce": "n",
                   "X-Lark-Signature": hashlib.sha256(b"1nenc-key" + raw).hexdigest()}
        self.channel.handle(headers, raw)
        self.assertIn("可用余额", self.sender.sent[-1][2]["text"])
        with self.assertRaises(Rejected):
            self.channel.handle(headers | {"X-Lark-Signature": "0" * 64}, raw)
        with self.assertRaises(Rejected):  # unsigned non-verification events are refused once an encrypt key is set
            self.channel.handle({}, raw)


class FeishuHTTPTests(FeishuTests):
    """The webhook bypasses the local bearer token / Host check but nothing else."""
    def setUp(self):
        super().setUp()
        self.server = create_server(self.s, "local-token", 0, chat_service=self.chat, feishu_channel=self.channel)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), thread.join(5), self.server.server_close()))

    def post(self, raw, host=None):
        port = self.server.server_port
        req = Request(f"http://127.0.0.1:{port}/api/channels/feishu/webhook", data=raw,
                      headers={"Content-Type": "application/json", **({"Host": host} if host else {})})
        try:
            with urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_webhook_over_http(self):
        status, body = self.post(json.dumps({"type": "url_verification", "challenge": "x", "token": self.TOKEN}).encode(), host="abc.tunnel.example")
        self.assertEqual((status, body), (200, {"challenge": "x"}))
        status, body = self.post(json.dumps({"type": "url_verification", "challenge": "x", "token": "bad"}).encode())
        self.assertEqual((status, body["error"]), (401, "FEISHU_UNVERIFIED"))
        status, _ = self.post(self.event("查余额"))
        self.assertEqual(status, 200)
        self.assertIn("可用余额", self.sender.sent[-1][2]["text"])


if __name__ == "__main__":
    unittest.main()
