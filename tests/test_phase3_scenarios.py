import tempfile
import unittest
from scripts.phase3_demo import demo


class Phase3ScenarioTests(unittest.TestCase):
    def test_shadow_http_scenarios_and_provider_switch_preserve_bank_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            report = demo(directory)
        for kind in ("TRANSFER", "BILL_ANALYSIS", "WEALTH_RECOMMENDATION"):
            self.assertEqual(report[kind]["status"], "COMPLETED")
        self.assertEqual({call["provider"] for call in report["shadow_calls"]}, {"offline-a", "offline-b"})
        self.assertTrue(report["TRANSFER"]["approved_action_unchanged_after_provider_switch"])
        self.assertTrue(report["ledger_balanced"])
        self.assertTrue(report["audit_chain_valid"])
