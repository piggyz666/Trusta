import copy
from dataclasses import replace
import hashlib
import json
import http.client
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from backend.config import Config
from backend.domain import Rejected,digest
from backend.service import TrustaService

ROOT=Path(__file__).resolve().parents[1]
ASSETS=ROOT/'data'/'zkp-dev-v1'


@unittest.skipUnless((ASSETS/'manifest.json').exists() and (ROOT/'crypto_trust/node_modules/snarkjs').exists(),
                     'Build optional real Groth16 development assets first.')
class PrivacyProofTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config=Config(db_path=str(Path(self.tmp.name)/'proof.db'),signing_key=b'privacy-proof-test-root',
                           communication_version=3,banking_version=5,safety_profile='competition',zkp_enabled=True,
                           zkp_artifact_dir=str(ASSETS),zkp_manifest_sha256=hashlib.sha256((ASSETS/'manifest.json').read_bytes()).hexdigest())
        self.s=TrustaService(self.config)
        self.task=self.s.create_task('给张三转 10 元')
        self.action=self.task['proposed_actions'][0]

    def bundle(self):return self.s.privacy.prove(self.task['task_id'],'demo',digest(self.action))

    def test_real_proof_independent_issuer_restart_replay_and_no_execution(self):
        before=self.s.gateway.read('verifier','demo','account.get_balance',account_id='acct-demo')
        bundle=self.bundle()
        # The verifier is a fresh service; public proof and locally pinned issuer suffice.
        resumed=TrustaService(self.config)
        result=resumed.privacy.verify('demo',bundle)
        self.assertTrue(result['verified']);self.assertFalse(result['financial_authority'])
        self.assertEqual(resumed._owned_task(self.task['task_id'],'demo')['status'],'WAITING_USER')
        with self.assertRaisesRegex(Rejected,'ZKP_REPLAY_DENIED'):resumed.privacy.verify('demo',bundle)
        self.assertEqual(before,resumed.gateway.read('verifier','demo','account.get_balance',account_id='acct-demo'))
        self.assertEqual(self.s.db.rows('SELECT * FROM transactions'),[])
        self.assertEqual(self.s.db.rows('SELECT * FROM execution_contracts'),[])
        stored=self.s.db.one('SELECT body FROM privacy_challenges')['body']
        self.assertNotIn('balance',stored);self.assertNotIn('risk_level',stored);self.assertNotIn('nonce',stored)
        self.assertTrue(self.s.audit.verify_chain())

    def test_invalid_public_inputs_owner_signature_and_groth16_proof(self):
        bundle=self.bundle()
        with self.assertRaisesRegex(Rejected,'ZKP_ACCESS_DENIED'):self.s.privacy.verify('other',bundle)
        bad=copy.deepcopy(bundle);bad['public_signals'][1]='11'
        with self.assertRaises(Rejected):self.s.privacy.verify('demo',bad)
        bad=copy.deepcopy(bundle);bad['signature']='A'*88
        with self.assertRaises(Rejected):self.s.privacy.verify('demo',bad)
        bad=copy.deepcopy(bundle);bad['proof']['pi_a'][0]='0'
        with self.assertRaises(Rejected):self.s.privacy.verify('demo',bad)
        # Failed verification never consumes the valid proof.
        self.assertTrue(self.s.privacy.verify('demo',bundle)['verified'])

    def test_expiry_and_fresh_authoritative_fact_check(self):
        bundle=self.bundle()
        with patch.object(self.s.privacy,'now',return_value=bundle['attestation']['expires_at']):
            with self.assertRaisesRegex(Rejected,'ZKP_EXPIRED'):self.s.privacy.verify('demo',bundle)
        # Simulates a separate channel changing source data, not an Agent write.
        with self.s.db.transaction() as conn:conn.execute("UPDATE accounts SET balance=balance-1,version=version+1 WHERE id='acct-demo'")
        with self.assertRaisesRegex(Rejected,'ZKP_FACTS_STALE'):self.s.privacy.verify('demo',bundle)

    def test_real_circuit_rejects_insufficient_balance_and_risk(self):
        base={'balance':'5','risk':'2','nonce':'123','amount':'10','requiredRisk':'1','context':'456'}
        with self.assertRaisesRegex(Rejected,'PRIVACY_PROOF_REJECTED'):self.s.privacy._run('prove',witness=base)
        base.update(balance='100',requiredRisk='3')
        with self.assertRaisesRegex(Rejected,'PRIVACY_PROOF_REJECTED'):self.s.privacy._run('prove',witness=base)
        base.update(risk='7',requiredRisk='1')
        with self.assertRaises(Rejected):self.s.privacy._run('prove',witness=base)

    def test_artifact_pinning_and_unsupported_action(self):
        with self.assertRaisesRegex(Rejected,'ZKP_ARTIFACT_TRUST_FAILED'):
            TrustaService(replace(self.config,zkp_manifest_sha256='0'*64))
        disabled=TrustaService(replace(self.config,zkp_enabled=False))
        with self.assertRaisesRegex(Rejected,'EXTENSION_DISABLED'):disabled.extensions.require('zkp')
        card=self.s.create_task('冻结card-demo')
        with self.assertRaisesRegex(Rejected,'ZKP_ACTION_UNSUPPORTED'):
            self.s.privacy.prove(card['task_id'],'demo',digest(card['proposed_actions'][0]))
        with self.assertRaises(Rejected):self.s.privacy.prove(self.task['task_id'],'demo','0'*64)

    def test_http_proof_identity_and_authorization_boundary(self):
        from backend.server import create_server
        server=create_server(self.s,'proof-http-token',port=0,identities={'proof-http-token':'demo','other-token':'other'})
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def call(path,body,token='proof-http-token'):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=30)
            conn.request('POST',path,json.dumps(body).encode(),{'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            response=conn.getresponse();result=(response.status,json.loads(response.read()));conn.close();return result
        params={'task_id':self.task['task_id'],'action_hash':digest(self.action)}
        self.assertNotEqual(call('/api/privacy/prove',params,'other-token')[0],201)
        status,bundle=call('/api/privacy/prove',params);self.assertEqual(status,201)
        self.assertNotEqual(call('/api/privacy/verify',{'bundle':bundle},'other-token')[0],200)
        status,result=call('/api/privacy/verify',{'bundle':bundle});self.assertEqual(status,200)
        self.assertTrue(result['verified']);self.assertFalse(result['bank_write_performed'])
        self.assertEqual(self.s.get_task(self.task['task_id'])['status'],'WAITING_USER')


if __name__=='__main__':unittest.main()
