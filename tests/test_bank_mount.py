import unittest
from backend.server import bank_path
class BankMountTests(unittest.TestCase):
    def test_mount_preserves_queries_and_route_boundaries(self):
        self.assertEqual(bank_path('/bank/api/chat/sessions?x=1'), '/api/chat/sessions?x=1')
        self.assertEqual(bank_path('/bank?x=1'), '/?x=1')
        self.assertEqual(bank_path('/bank'), '/')
        self.assertEqual(bank_path('/bankruptcy'), '/bankruptcy')
        self.assertEqual(bank_path('/api/bank'), '/api/bank')
