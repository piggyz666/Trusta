"""Chat requests run through the signed multi-agent pipeline end to end: signed REQUEST carries provenance,
read answers are signed agent reports, evidence is exportable and verifies offline; tampering is detected."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from backend.chat.llm import ScriptedToolClient
from backend.chat.service import ChatService
from backend.config import Config
from backend.domain import Rejected, digest
from backend.runtime.handoff_evidence import verify_bundle
from backend.service import TrustaService


def call(tool, **arguments):
    return {"name": tool, "arguments": arguments}


class TrustedChatCommunicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = TrustaService(Config(db_path=str(Path(self.tmp.name) / "tc.db"), signing_key=b"trusted-comm", communication_version=3,
                                      banking_version=5))
        self.chat = ChatService(self.s, ScriptedToolClient({
            "给张三转100": [call("propose_transfer", payee={"value": "张三", "span": "张三"}, amount={"value": "100", "span": "100"})],
            "查余额然后看看9月吃饭花了多少": [call("get_balance"), call("query_spending", months=["2026-09"], category="FOOD")],
        }))
        session = self.chat.create_session("demo", "test")
        self.sid, self.rev = session["session_id"], session["revision"]

    def say(self, message_id, text):
        turn = self.chat.send(self.sid, "demo", message_id, self.rev, text)
        self.rev = turn["revision"]
        return turn

    def registry(self):
        snapshot = self.s.crypto_registry.snapshot()
        return {k["key_id"]: k for k in snapshot["keys"]}, {a["agent_id"]: a for a in snapshot["agents"]}

    def test_transfer_messages_are_signed_and_chained(self):
        card = self.say("m1", "给张三转100")["reply"]["cards"][0]
        self.chat.decide(self.sid, "demo", card["action_id"], card["action_hash"], True)
        task = self.chat.trace(self.sid, "demo", "m1")["tasks"][0]
        messages = task["agent_messages"]
        self.assertEqual([(m["sender"], m["receiver"], m["type"]) for m in messages],
                         [("supervisor", "planner", "REQUEST"), ("planner", "transaction", "PLAN"), ("transaction", "supervisor", "ACTION_PROPOSED")])
        self.assertTrue(all(m["message_signature_valid"] and m["evidence_signature_valid"] and m["causal_parents_known"] for m in messages))
        self.assertTrue(all(m["sender_identity"].startswith("urn:trusta:") for m in messages))

    def test_signed_request_binds_interpretation_provenance(self):
        self.say("m1", "给张三转100")
        task_id = self.chat.trace(self.sid, "demo", "m1")["tasks"][0]["task_id"]
        normalized = self.s.blackboard.get(task_id)["context"]["normalized"]
        origin = normalized["origin"]
        self.assertEqual((origin["session_id"], origin["message_id"], origin["utterance_hash"]), (self.sid, "m1", digest("给张三转100")))
        record = self.chat.trace(self.sid, "demo", "m1")["trace"]
        self.assertEqual(origin["tool_calls_hash"], digest(record["calls"]))
        request = self.chat.trace(self.sid, "demo", "m1")["tasks"][0]["agent_messages"][0]
        self.assertEqual(request["payload_hash"], digest(normalized))  # the supervisor signed exactly this provenance

    def test_read_answers_are_signed_agent_reports(self):
        turn = self.say("m1", "查余额然后看看9月吃饭花了多少")
        self.assertIn("¥10,000.00", turn["reply"]["text"])
        tasks = self.chat.trace(self.sid, "demo", "m1")["tasks"]
        self.assertEqual([t["task_type"] for t in tasks], ["BALANCE_QUERY", "SPENDING_QUERY"])
        senders = {t["task_type"]: {m["sender"] for m in t["agent_messages"] if m["type"] == "READ_ONLY_REPORT"} for t in tasks}
        self.assertEqual(senders, {"BALANCE_QUERY": {"transaction"}, "SPENDING_QUERY": {"finance"}})
        self.assertTrue(all(m["message_signature_valid"] for t in tasks for m in t["agent_messages"]))

    def test_evidence_bundle_verifies_offline_and_detects_tampering(self):
        self.say("m1", "给张三转100")
        task_id = self.chat.trace(self.sid, "demo", "m1")["tasks"][0]["task_id"]
        exported = json.loads(json.dumps(self.chat.evidence_bundle(self.sid, "demo", task_id)))
        keys, agents = {k["key_id"]: k for k in exported["public_registry"]["keys"]}, {a["agent_id"]: a for a in exported["public_registry"]["agents"]}
        self.assertTrue(verify_bundle(exported["bundle"], keys, agents))
        forged = copy.deepcopy(exported["bundle"])
        forged["entries"][2]["evidence"]["message"]["payload_hash"] = "0" * 64  # e.g. swap the proposed amount
        self.assertFalse(verify_bundle(forged, keys, agents))
        impostor = copy.deepcopy(exported["bundle"])
        impostor["entries"][2]["evidence"]["message"]["sender_agent"] = "wealth"  # pretend another agent proposed it
        self.assertFalse(verify_bundle(impostor, keys, agents))
        self.s.crypto_registry.rotate("transaction", 0)
        rotated = {k["key_id"]: k for k in self.s.crypto_registry.snapshot()["keys"] if k["agent_id"] != "transaction" or k["status"] == "ACTIVE"}
        self.assertFalse(verify_bundle(exported["bundle"], rotated, agents))  # a verifier pinned to the new key rejects old signatures

    def test_evidence_only_for_this_sessions_tasks(self):
        self.say("m1", "给张三转100")
        other = self.s.create_task("给张三转200元")
        with self.assertRaises(Rejected):
            self.chat.evidence_bundle(self.sid, "demo", other["task_id"])
        with self.assertRaises(Rejected):
            self.chat.trace(self.sid, "mallory", "m1")


if __name__ == "__main__":
    unittest.main()
