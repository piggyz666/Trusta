from pathlib import Path
import tempfile
import unittest
from scripts.phase4_demo import demo


class Phase4Scenarios(unittest.TestCase):
    def test_original_three_http_scenarios_and_identity_nonce_receipt_proof_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            result = demo(directory)
        self.assertEqual(result["TRANSFER"]["verification"], "VERIFIED")
        self.assertEqual(result["TRANSFER"]["transaction_count"], 1)
        self.assertTrue(all(result["restart"].values()))
        self.assertTrue(result["independent_proof_verification"])
        self.assertTrue(result["receipt_tampering_detected"])
        self.assertEqual(result["BILL_ANALYSIS"]["net_spend_cents"], 542500)
        self.assertEqual(result["WEALTH_RECOMMENDATION"]["product_ids"], ["sandbox-r1", "sandbox-r2"])
        self.assertFalse(result["blockchain_externally_verified"])
