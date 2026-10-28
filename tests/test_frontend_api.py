"""APIs behind the web / H5 app: conversation history, records & overview, feature switches, settings (local-only writes),
owner-level protection, and static serving of the built app with an SPA fallback."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from backend import server as server_module
from backend.access import AccessControl
from backend.chat.llm import ScriptedToolClient
from backend.chat.service import ChatService
from backend.config import Config
from backend.server import create_server
from backend.service import TrustaService


def call(tool, **arguments):
    return {"name": tool, "arguments": arguments}


S = lambda v: {"value": v, "span": v}


class FrontendApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = TrustaService(Config(db_path=str(Path(self.tmp.name) / "f.db"), signing_key=b"front", communication_version=3,
                                      banking_version=5))
        self.access = AccessControl(self.s, mode="lan")
        self.s.access = self.access
        script = {"给张三转500": [call("propose_transfer", payee=S("张三"), amount=S("500"))],
                  "冻结卡": [call("propose_card_operation", op="FREEZE")]}
        self.server = create_server(self.s, "master", 0, chat_service=ChatService(self.s, ScriptedToolClient(script)), access=self.access)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), thread.join(5), self.server.server_close()))
        self.port = self.server.server_port

    def request(self, path, body=None, token="master", raw=False):
        headers = {"Content-Type": "application/json", "Host": f"127.0.0.1:{self.port}"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = Request(f"http://127.0.0.1:{self.port}{path}", data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urlopen(req) as response:
                data = response.read()
                return response.status, (data, response.headers) if raw else json.loads(data)
        except HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def turn(self, sid, text):
        _, view = self.request(f"/api/chat/sessions/{sid}")
        return self.request(f"/api/chat/sessions/{sid}/messages", {"message_id": "m" + str(view["revision"]), "revision": view["revision"],
                                                                   "text": text})[1]

    def approve(self, sid, card):
        return self.request(f"/api/chat/sessions/{sid}/actions/{card['action_id']}/decision",
                            {"action_hash": card["action_hash"], "approve": True})[1]

    def test_history_records_overview_and_rename_delete(self):
        sid = self.request("/api/chat/sessions", {"channel": "app"})[1]["session_id"]
        self.approve(sid, self.turn(sid, "给张三转500")["reply"]["cards"][0])
        self.approve(sid, self.turn(sid, "冻结卡")["reply"]["cards"][0])
        sessions = self.request("/api/chat/sessions")[1]["sessions"]
        self.assertEqual([s["title"] for s in sessions], ["转账给 张三 · ¥500.00"])  # named by what it did
        items = self.request("/api/chat/activity")[1]["items"]
        kinds = {i["kind"]: i for i in items}
        self.assertEqual(kinds["transfer"]["amount_minor"], 50000)
        self.assertEqual(kinds["transfer"]["status"], "SUCCEEDED")
        self.assertEqual(kinds["transfer"]["source"]["session_id"], sid)
        self.assertIn("card", kinds)
        self.assertEqual([i["kind"] for i in self.request("/api/chat/activity?kind=card")[1]["items"]], ["card"])
        self.assertEqual(self.request("/api/chat/activity?kind=nope")[0], 400)
        overview = self.request("/api/chat/overview")[1]
        self.assertEqual(overview["cards"][0]["status"], "FROZEN")
        self.assertTrue(overview["recent"])
        self.assertEqual(self.request(f"/api/chat/sessions/{sid}/rename", {"title": "  转账  测试 "})[1]["title"], "转账 测试")
        self.assertEqual(self.request(f"/api/chat/sessions/{sid}/rename", {"title": ""})[0], 400)
        self.request(f"/api/chat/sessions/{sid}/delete", {})
        self.assertEqual(self.request("/api/chat/sessions")[1]["sessions"], [])
        # deleting the conversation never deletes the bank record
        self.assertTrue(self.request("/api/chat/activity?kind=transfer")[1]["items"])

    def test_feature_switches(self):
        self.assertEqual(self.request("/api/chat/capabilities/toggle", {"feature": "wealth", "enabled": False})[1]["status"], "OFF")
        features = {f["id"]: f for f in self.request("/api/chat/capabilities")[1]["features"]}
        self.assertEqual(features["wealth"]["status"], "OFF")
        self.assertEqual(self.request("/api/chat/capabilities/toggle", {"feature": "strong_auth", "enabled": False})[0], 409)

    def test_settings_writes_are_local_only_and_secrets_masked(self):
        status, settings = self.request("/api/chat/settings")
        self.assertEqual(status, 200)
        self.assertTrue(settings["editable"])
        self.assertNotIn("api_key", json.dumps(settings))
        device = self.access.pair_device(self.access.start_pairing("demo", "device")["code"], "phone")["device_token"]
        self.assertFalse(self.request("/api/chat/settings", token=device)[1]["editable"])
        # A paired phone (even one on this computer) must not repoint the model or the access mode.
        self.assertEqual(self.request("/api/chat/settings/access", {"mode": "local"}, token=device)[0], 403)
        self.assertEqual(self.request("/api/chat/settings/llm", {"model": "x-model"}, token=device)[0], 403)

    def test_feishu_credentials_from_settings(self):
        """新部署的人可以在 设置 → 飞书 填凭据：只在本机可改、密钥不回显、凭据立即向飞书验证、权限清单随页面给出。"""
        import os
        from backend import envfile
        from backend.channels.feishu import SCOPES, FeishuSender
        env = Path(self.tmp.name) / ".env"
        real_write = envfile.write_env
        tokens = []

        def token(sender):
            tokens.append(sender.config.app_id)
            if sender.config.app_secret != "s" * 32:
                raise ValueError("飞书返回 10014：app secret invalid")
            return "t-ok"

        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(envfile, "write_env", lambda updates: real_write(updates, path=env)), \
                mock.patch.object(FeishuSender, "token", token):
            for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_MODE", "FEISHU_VERIFICATION_TOKEN", "FEISHU_ENCRYPT_KEY"):
                os.environ.pop(key, None)
            feishu = self.request("/api/chat/settings")[1]["feishu"]
            self.assertFalse(feishu["configured"])
            self.assertEqual([s["scope"] for s in feishu["scopes"]], [s for s, _, _ in SCOPES])
            self.assertTrue(any("im.message.receive_v1" in step for step in feishu["steps"]))
            device = self.access.pair_device(self.access.start_pairing("demo", "device")["code"], "phone")["device_token"]
            body = {"app_id": "cli_a1b2c3d4e5f6", "app_secret": "s" * 32}
            self.assertEqual(self.request("/api/chat/settings/feishu", body, token=device)[0], 403)
            for bad in ({"app_id": "a1b2c3", "app_secret": "s" * 32}, {"app_id": "cli_a1b2c3d4e5f6", "app_secret": "short"},
                        {"app_id": "cli_a1b2c3d4e5f6", "app_secret": "s" * 32, "mode": "webhook"}, body | {"extra": 1}):
                self.assertEqual(self.request("/api/chat/settings/feishu", bad)[0], 400, bad)
            self.assertFalse(env.exists())
            status, result = self.request("/api/chat/settings/feishu", body)
            self.assertEqual((status, result["status"], result["restart_required"]), (200, "VERIFIED", True))
            saved = env.read_text(encoding="utf-8")
            self.assertIn("FEISHU_APP_ID=cli_a1b2c3d4e5f6", saved)
            self.assertIn("FEISHU_MODE=longconn", saved)
            settings = self.request("/api/chat/settings")[1]
            self.assertTrue(settings["feishu"]["configured"])
            self.assertNotIn("s" * 32, json.dumps(settings))  # the secret is never sent back, only masked
            # A wrong secret is saved but reported, with Feishu's own message (never the secret).
            result = self.request("/api/chat/settings/feishu", {"app_id": "cli_a1b2c3d4e5f6", "app_secret": "x" * 32})[1]
            self.assertEqual(result["status"], "FAILED")
            self.assertIn("10014", result["detail"])
            # Changing only the App ID keeps the saved secret.
            self.request("/api/chat/settings/feishu", {"app_id": "cli_a1b2c3d4e5f6", "app_secret": "s" * 32})
            self.assertEqual(self.request("/api/chat/settings/feishu", {"app_id": "cli_ffffeeee0000"})[1]["status"], "VERIFIED")
            # Webhook mode needs a verification token.
            webhook = {"app_id": "cli_ffffeeee0000", "mode": "webhook", "verification_token": "vtoken123"}
            self.assertEqual(self.request("/api/chat/settings/feishu", webhook)[0], 200)
            self.assertEqual(self.request("/api/chat/settings")[1]["feishu"]["mode"], "webhook")
            self.assertEqual(self.request("/api/chat/settings/feishu", {"disable": True})[1]["status"], "NOT_CONFIGURED")
            self.assertNotIn("FEISHU_APP_ID", env.read_text(encoding="utf-8"))
            self.assertFalse(self.request("/api/chat/settings")[1]["feishu"]["configured"])
            self.assertTrue(any('"SETTINGS_FEISHU_CHANGED"' in r["body"] for r in self.s.db.rows("SELECT body FROM audit_events")))
            self.assertTrue(self.s.audit.verify_chain())

    def test_owner_protection_from_settings(self):
        status, state = self.request("/api/chat/protection", {"hours": 2})
        self.assertEqual(status, 200)
        self.assertEqual(state["reason"], "USER_REQUEST")
        self.assertEqual(self.request("/api/chat/protection", {"hours": 999})[0], 400)
        self.assertTrue(self.request("/api/chat/overview")[1]["protection"])


class StaticServingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "dist" / "assets").mkdir(parents=True)
        (root / "dist" / "index.html").write_text("<div id=root></div>", encoding="utf-8")
        (root / "dist" / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        (root / "index.html").write_text("legacy", encoding="utf-8")
        (root / "secret.txt").write_text("nope", encoding="utf-8")
        patches = [mock.patch.object(server_module, "FRONTEND", root), mock.patch.object(server_module, "DIST", root / "dist")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_spa_routes_assets_and_traversal(self):
        serve = server_module.serve_static
        for path in ("/", "/overview", "/settings/", "/features"):
            self.assertEqual(serve(path)[0], b"<div id=root></div>")
        self.assertEqual(serve("/assets/app.js")[1], "text/javascript; charset=utf-8")
        self.assertEqual(serve("/legacy")[0], b"legacy")
        self.assertIsNone(serve("/../secret.txt"))
        self.assertIsNone(serve("/assets/../../secret.txt"))
        self.assertIsNone(serve("/api/chat/sessions"))
        self.assertIsNone(serve("/missing.js"))

    def test_without_build_falls_back_to_legacy(self):
        (Path(self.tmp.name) / "dist" / "index.html").unlink()
        self.assertEqual(server_module.serve_static("/")[0], b"legacy")
        self.assertIsNone(server_module.serve_static("/overview"))


if __name__ == "__main__":
    unittest.main()


class MediaRangeTests(unittest.TestCase):
    def test_video_supports_range_requests(self):
        root = Path(tempfile.mkdtemp())
        (root / "dist" / "bg").mkdir(parents=True)
        (root / "dist" / "index.html").write_text("x", encoding="utf-8")
        (root / "dist" / "bg" / "v.mp4").write_bytes(bytes(range(256)) * 4)
        with mock.patch.object(server_module, "FRONTEND", root), mock.patch.object(server_module, "DIST", root / "dist"):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            s = TrustaService(Config(db_path=str(Path(tmp.name) / "m.db"), signing_key=b"m", communication_version=3, banking_version=5))
            srv = create_server(s, "master", 0)
            thread = threading.Thread(target=srv.serve_forever, daemon=True)
            thread.start()
            try:
                port = srv.server_port
                req = Request(f"http://127.0.0.1:{port}/bg/v.mp4", headers={"Range": "bytes=10-19", "Host": f"127.0.0.1:{port}"})
                with urlopen(req) as r:
                    self.assertEqual((r.status, r.headers["Content-Range"], r.read()), (206, "bytes 10-19/1024", bytes(range(10, 20))))
                req = Request(f"http://127.0.0.1:{port}/bg/v.mp4", headers={"Range": "bytes=5000-", "Host": f"127.0.0.1:{port}"})
                with self.assertRaises(HTTPError) as caught:
                    urlopen(req)
                self.assertEqual(caught.exception.code, 416)
            finally:
                srv.shutdown(); thread.join(5); srv.server_close()
