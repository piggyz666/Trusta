"""Synthetic local signer and fake RPC only. No configured wallet is loaded."""
import copy
import os
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from backend.domain import Rejected,digest
from backend.security.extensions import AnchorCommitment
from backend.audit_trust.external_anchor import ExternalBlockchainAnchor
from backend.audit_trust.independent_anchor import verify_two_rpcs
from backend.audit_trust.transaction_safety import prepare_transaction,plan_hash,sign_approved_plan
from tests.test_sepolia_readiness import PlanRPC,OWNER,DEPLOY,CODEHASH,KEY,TARGET
from tests.phase6_helpers import service
from pathlib import Path


class AnchorBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=service(self.tmp.name)
        self.rpc=PlanRPC();self.rpc.gas=356357
        env=patch.dict(os.environ,{'ANCHOR_WALLET_ADDRESS':OWNER,'ANCHOR_CONTRACT_CODE_HASH':CODEHASH,
            'ANCHOR_ALLOW_SIGNING':'true','ANCHOR_ALLOW_BROADCAST':'true','ANCHOR_APPROVED_PLAN_HASH':'',
            'ANCHOR_KEYSTORE_PATH':'','ZERO_SYNTHETIC_KEY':KEY})
        env.start();self.addCleanup(env.stop)

    def test_same_rpc_host_or_object_cannot_count_as_independent(self):
        first=PlanRPC();second=PlanRPC()
        for other in (first,second):
            first.url='https://provider.example/key1';other.url='https://provider.example/key2'
            with self.assertRaises(Rejected) as caught:verify_two_rpcs(first,other)
            self.assertEqual(caught.exception.code,'ANCHOR_RPC_INDEPENDENCE_REQUIRED')
        self.assertEqual(first.calls,[]);self.assertEqual(second.calls,[])

    def test_uncertain_broadcast_reserves_nonce_across_checkpoints_before_signing(self):
        a=ExternalBlockchainAnchor(self.s.db,self.rpc,TARGET,wallet_env='ZERO_SYNTHETIC_KEY')
        cp=self.s.checkpoints.generate_checkpoint();root=AnchorCommitment(cp['merkle_root']);h=digest(cp)
        plan=a.prepare_commit(root,h);os.environ['ANCHOR_APPROVED_PLAN_HASH']=plan_hash(plan)
        with patch('backend.audit_trust.external_anchor.broadcast_approved',side_effect=Rejected('ANCHOR_RPC_UNAVAILABLE')):
            with self.assertRaises(Rejected):a.submit(root,h,plan)
        row=self.s.db.one('SELECT * FROM external_anchor_submissions WHERE hash=?',(h,))
        self.assertEqual(a.submit(root,h),row['tx_hash'])
        self.s.audit.append(None,None,'NEXT_CHECKPOINT','test',{})
        cp2=self.s.checkpoints.generate_checkpoint();r2=AnchorCommitment(cp2['merkle_root']);h2=digest(cp2)
        plan2=a.prepare_commit(r2,h2);os.environ['ANCHOR_APPROVED_PLAN_HASH']=plan_hash(plan2)
        with patch('backend.audit_trust.external_anchor.sign_approved_plan',side_effect=AssertionError('must reject before signing')):
            with self.assertRaises(Rejected) as caught:a.submit(r2,h2,plan2)
        self.assertEqual(caught.exception.code,'ANCHOR_NONCE_RESERVED')
        self.assertEqual(len(self.s.db.rows('SELECT * FROM external_anchor_submissions')),1)
        self.assertEqual(self.rpc.sent,[])

    def test_deployment_nonce_reservation_blocks_commit_before_signing(self):
        from scripts.deploy_audit_anchor import execute_plan
        a=ExternalBlockchainAnchor(self.s.db,self.rpc,TARGET,wallet_env='ZERO_SYNTHETIC_KEY')
        rpc=PlanRPC();plan=prepare_transaction(rpc,OWNER,DEPLOY,'deploy')
        os.environ['ANCHOR_APPROVED_PLAN_HASH']=plan_hash(plan)
        path=Path(self.tmp.name)/'deployment.json'
        with patch('scripts.deploy_audit_anchor.broadcast_approved',side_effect=Rejected('ANCHOR_RPC_UNAVAILABLE')):
            with self.assertRaises(Rejected):execute_plan(rpc,plan,path,self.s.db,'ZERO_SYNTHETIC_KEY')
        self.assertTrue(path.exists());self.assertEqual(rpc.sent,[])
        cp=self.s.checkpoints.generate_checkpoint();root=AnchorCommitment(cp['merkle_root']);h=digest(cp)
        commit=a.prepare_commit(root,h);os.environ['ANCHOR_APPROVED_PLAN_HASH']=plan_hash(commit)
        with patch('backend.audit_trust.external_anchor.sign_approved_plan',side_effect=AssertionError('no re-sign')):
            with self.assertRaises(Rejected) as caught:a.submit(root,h,commit)
        self.assertEqual(caught.exception.code,'ANCHOR_NONCE_RESERVED')

    def test_deployment_respects_other_wallet_claim_before_signing(self):
        from scripts.deploy_audit_anchor import execute_plan
        other=service(self.tmp.name);a=ExternalBlockchainAnchor(other.db,self.rpc,TARGET)
        plan=prepare_transaction(PlanRPC(),OWNER,DEPLOY,'deploy')
        with a.claims.hold('anchor-wallet:11155111:'+OWNER.lower()):
            with patch('scripts.deploy_audit_anchor.sign_approved_plan',side_effect=AssertionError('no sign')):
                with self.assertRaises(Rejected) as caught:execute_plan(self.rpc,plan,Path(self.tmp.name)/'deployment.json',self.s.db)
        self.assertEqual(caught.exception.code,'GRAPH_WORKER_BUSY')

    def test_bool_float_and_extra_transaction_fields_never_reach_signer(self):
        rpc=PlanRPC();base=prepare_transaction(rpc,OWNER,DEPLOY,'deploy')
        for key,value in [('nonce',False),('value',0.0),('gas',float(base['transaction']['gas'])),('accessList',[])]:
            plan=copy.deepcopy(base);plan['transaction'][key]=value
            os.environ['ANCHOR_APPROVED_PLAN_HASH']=plan_hash(plan)
            with patch('eth_account.Account.from_key',side_effect=AssertionError('must not load a key')):
                with self.assertRaises(Rejected) as caught:sign_approved_plan(rpc,plan,'ZERO_SYNTHETIC_KEY')
            self.assertEqual(caught.exception.code,'ANCHOR_TRANSACTION_BINDING_INVALID')

    def test_second_anchor_worker_does_not_overwrite_inflight_job(self):
        cp=self.s.checkpoints.generate_checkpoint();other=service(self.tmp.name)
        started=threading.Event();release=threading.Event()
        original=self.s.anchors.adapter.submit
        def blocked(*a):
            started.set();self.assertTrue(release.wait(5));return original(*a)
        with patch.object(self.s.anchors.adapter,'submit',blocked),ThreadPoolExecutor(1) as pool:
            first=pool.submit(self.s.anchors.process_one,cp['checkpoint_id'])
            self.assertTrue(started.wait(5))
            try:
                with self.assertRaises(Rejected) as caught:other.anchors.process_one(cp['checkpoint_id'])
                self.assertEqual(caught.exception.code,'GRAPH_WORKER_BUSY')
                self.assertEqual(other.anchors.status(cp['checkpoint_id'])['status'],'PENDING')
            finally:release.set()
            self.assertEqual(first.result(5)['status'],'CONFIRMED')
