import json
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch
from eth_account import Account
from eth_account.messages import encode_defunct
from backend.domain import Rejected,canonical
from scripts.anchor_operator import enroll,hidden_input,verify_control


class OperatorControlTests(unittest.TestCase):
    def test_hidden_input_refuses_redirected_terminal_before_prompt(self):
        with patch('scripts.anchor_operator.sys.stdin.isatty',return_value=False),patch('scripts.anchor_operator.getpass.getpass') as prompt:
            with self.assertRaises(Rejected) as caught:hidden_input('secret:')
            self.assertEqual(caught.exception.code,'ANCHOR_INTERACTIVE_TERMINAL_REQUIRED');prompt.assert_not_called()

    def test_getpass_fallback_warning_is_fatal(self):
        import getpass
        def fallback(_):warnings.warn('fallback',getpass.GetPassWarning)
        with patch('scripts.anchor_operator.sys.stdin.isatty',return_value=True),patch('scripts.anchor_operator.sys.stderr.isatty',return_value=True),patch('scripts.anchor_operator.getpass.getpass',side_effect=fallback):
            with self.assertRaises(Rejected) as caught:hidden_input('secret:')
            self.assertEqual(caught.exception.code,'ANCHOR_HIDDEN_INPUT_UNAVAILABLE')

    def test_control_proof_restores_exact_wallet_without_transaction_or_secret(self):
        wallet=Account.create();password='isolated-control-test-password'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'signing/wallet.json';enroll(wallet.key,password,wallet.address,path)
            first=verify_control(path,password,wallet.address);second=verify_control(path,password,wallet.address)
            recovered=Account.recover_message(encode_defunct(text=canonical(first['message'])),signature=first['signature'])
            self.assertEqual(recovered,wallet.address);self.assertNotEqual(first['message']['nonce'],second['message']['nonce'])
            self.assertEqual(first['transaction_signatures'],0);self.assertEqual(first['broadcasts'],0)
            self.assertFalse(first['external_blockchain_verified']);self.assertNotIn(password,json.dumps(first))
            self.assertNotIn(wallet.key.hex(),path.read_text(encoding='utf-8'))
            with self.assertRaises(Rejected) as caught:verify_control(path,password,Account.create().address)
            self.assertEqual(caught.exception.code,'ANCHOR_SIGNER_MISMATCH')
            with self.assertRaises(Rejected) as caught:verify_control(path,'wrong-password',wallet.address)
            self.assertEqual(caught.exception.code,'ANCHOR_LOCAL_SIGNER_UNAVAILABLE')


if __name__=='__main__':unittest.main()
