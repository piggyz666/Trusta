import unittest
from unittest.mock import patch
from scripts.finalize_sepolia_evidence import observe_explorer


class ExplorerEvidenceTests(unittest.TestCase):
    def observe(self,body):
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
        class Opener:
            def open(self,*args,**kwargs):return Response()
        with patch('urllib.request.build_opener',return_value=Opener()),patch('backend.network.read_bounded',return_value=body.encode()):
            return observe_explorer('0x'+'ab'*32,11892926)['page_observed']

    def page(self,status='Success',block='11892926',hash_value=None):
        return (f'<span id="spanTxHash">{hash_value or "0x"+"ab"*32}</span>'
            f'<span id="data-status"><i></i>{status}</span><span id="data-blk">{block}</span>')

    def test_unrelated_wallet_script_does_not_override_actual_success(self):
        self.assertTrue(self.observe(self.page()+"<script>alert('Unable to locate a compatible web3 browser!')</script>"))

    def test_failed_wrong_block_wrong_hash_and_metadata_only_rejected(self):
        for body in (self.page(status='Fail'),self.page(block='1'),self.page(hash_value='0x'+'cd'*32),
                '<script>'+self.page()+'</script>', '<title>Transaction Hash '+ '0x'+'ab'*32+' Success 11892926</title>'):
            with self.subTest(body=body):self.assertFalse(self.observe(body))

if __name__=='__main__':unittest.main()
