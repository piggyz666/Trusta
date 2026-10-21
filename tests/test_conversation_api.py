import json
import unittest
from tests import test_api
from backend.domain import uid
from backend.llm.configuration import LLMSettings, ProviderConfig
from backend.llm.gateway import LLMGateway


class ConversationApiTests(unittest.TestCase):
    request = test_api.ApiTests.request
    close_server = test_api.ApiTests.close_server

    def setUp(self):
        test_api.ApiTests.setUp(self)
        self.service.llm = LLMGateway(LLMSettings(mode="assisted", providers=(ProviderConfig("offline", max_sensitivity="restricted"),)),
                                      self.service.db, self.service.audit)
        _, self.conv = self.request("/api/conversations", {})

    def turn(self, text, turn_id=None, revision=None):
        _, current = self.request(f"/api/conversations/{self.conv['conversation_id']}")
        return self.request(f"/api/conversations/{self.conv['conversation_id']}/turns",
                            {"text": text, "turn_id": turn_id or uid(), "revision": current["revision"] if revision is None else revision})

    def test_http_dialogue_and_card_authorization_stay_separate(self):
        self.assertEqual(self.turn("帮我给张三转账")[1]["status"], "CLARIFICATION")
        code, response = self.turn("两千元")
        self.assertEqual(code, 200)
        task = response["task"]
        self.assertEqual(task["proposed_actions"][0]["amount"], 200000)
        self.assertEqual(self.turn("我确认")[1]["task"]["status"], "WAITING_USER")
        self.assertEqual(self.request("/api/balance")[1]["balance"], 1000000)
        code, done = self.request(f"/api/tasks/{task['task_id']}/authorize", {"action_hash": task["authorization"]["action_hash"], "approve": True})
        self.assertEqual(done["status"], "COMPLETED")
        self.assertEqual(self.request("/api/balance")[1]["balance"], 800000)

    def test_authentication_origin_and_owner_checks(self):
        path = f"/api/conversations/{self.conv['conversation_id']}/turns"
        body = {"text": "给张三转2000元", "turn_id": uid(), "revision": 0}
        self.assertEqual(self.request(path, body, token="bad")[0], 403)
        self.assertEqual(self.request(path, body, Origin="https://attacker.invalid")[0], 403)
        other = self.service.conversations.create("other")
        self.assertEqual(self.request(f"/api/conversations/{other['conversation_id']}")[0], 403)
        self.assertEqual(self.request(f"/api/conversations/{other['conversation_id']}/turns", body)[0], 403)
        self.assertEqual(self.service.blackboard.list("demo"), [])

    def test_client_cannot_supply_identity_intent_approval_or_unsafe_account(self):
        path = f"/api/conversations/{self.conv['conversation_id']}/turns"
        base = {"text": "给张三转2000元", "turn_id": uid(), "revision": 0}
        for key, value in (("user_id", "other"), ("task_type", "TRANSFER"), ("approve", True),
                           ("normalized", {"amount": 1}), ("model", "bypass")):
            self.assertEqual(self.request(path, base | {key: value})[0], 400)
        for body in (base | {"revision": True}, base | {"text": ""}, base | {"source_account_id": {}}):
            self.assertEqual(self.request(path, body)[0], 400)
        code, result = self.request(path, base | {"source_account_id": "settlement"})
        self.assertEqual(code, 200)
        self.assertEqual(result["task"]["status"], "BLOCKED")
        self.assertEqual(self.request("/api/balance")[1]["balance"], 1000000)

    def test_http_retries_do_not_create_duplicate_bank_proposals(self):
        turn_id = uid()
        first = self.turn("给张三转2000元", turn_id, 0)[1]
        second = self.turn("给张三转2000元", turn_id, 0)[1]
        self.assertEqual(first["task"]["task_id"], second["task"]["task_id"])
        self.assertEqual(len(self.service.blackboard.list("demo")), 1)
        self.assertEqual(self.turn("给张三转3000元", turn_id, 0)[0], 400)

    def test_call_trace_is_owned_and_does_not_contain_input_text(self):
        _, response = self.turn("帮我给张三转2000元")
        code, trace = self.request("/api/llm-calls/" + response["llm_request_id"])
        self.assertEqual(code, 200)
        self.assertNotIn("张三", json.dumps(trace, ensure_ascii=False))
        self.assertEqual(self.request("/api/llm-calls/missing")[0], 403)
        self.assertEqual(self.request("/api/llm-calls/" + response["llm_request_id"], token="bad")[0], 403)

    def test_close_cancels_pending_task_and_rejects_new_turn(self):
        task = self.turn("给张三转2000元")[1]["task"]
        path = f"/api/conversations/{self.conv['conversation_id']}/close"
        self.assertEqual(self.request(path, {})[1]["status"], "CLOSED")
        self.assertEqual(self.request(f"/api/tasks/{task['task_id']}")[1]["status"], "CANCELLED")
        self.assertEqual(self.turn("给张三转2000元")[0], 400)
