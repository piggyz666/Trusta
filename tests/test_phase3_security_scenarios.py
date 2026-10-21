from pathlib import Path
import tempfile
import unittest
from scripts.phase3_security_demo import demo


class Phase3SecurityScenarios(unittest.TestCase):
    def test_memory_rotation_deletion_restart_and_three_http_business_scenarios(self):
        with tempfile.TemporaryDirectory() as directory:
            report = demo(Path(directory))
        self.assertEqual(report["TRANSFER"]["verification"], "VERIFIED")
        self.assertTrue(report["audit_chain_valid"])
        self.assertEqual(report["MEMORY"]["remaining_after_clear"], 0)
        self.assertEqual(report["COMMUNICATION"]["active_key_version"], 2)
