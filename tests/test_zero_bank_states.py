"""Terminal bank status observations may reverse, but cannot regress silently."""
import unittest
from tests import test_revolut_banking as banking
from backend.bank_sdk.contracts import BankError

class BankStateTests(unittest.TestCase):
    def test_stale_pending_does_not_overwrite_settled_and_reversal_is_visible(self):
        f=banking.TransferTests();f.setUp();self.addCleanup(f.temp.cleanup)
        task=f.approve(f.propose());self.assertEqual(task['status'],'COMPLETED')
        row=f.s.db.one('SELECT * FROM external_bank_operations');self.assertEqual(row['status'],'SETTLED')
        f.net.raw['state']='pending'
        with self.assertRaises(BankError) as caught:f.provider._status(banking.TX)
        self.assertEqual(caught.exception.code,'BANK_STATUS_REGRESSION')
        self.assertEqual(f.s.db.one('SELECT status FROM external_bank_operations')['status'],'SETTLED')
        f.net.raw['state']='reverted';self.assertEqual(f.provider._status(banking.TX)['status'],'REVERSED')
        f.net.raw['state']='completed'
        with self.assertRaises(BankError):f.provider._status(banking.TX)
        self.assertEqual(f.s.db.one('SELECT status FROM external_bank_operations')['status'],'REVERSED')
        self.assertTrue(f.s.audit.verify_chain());self.assertEqual(len(f.net.posts),1)
