import copy
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor,ProcessPoolExecutor
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from eth_account import Account
from backend.persistence import Database
from backend.domain import Rejected
from backend.tx.state_machine import TransactionStateMachine
from backend.tx.nonce_manager import NonceManager
from backend.tx.recovery_manager import RecoveryManager
from backend.monitor.receipt_watcher import ReceiptWatcher
from backend.anchor.worker import AnchorWorker
from backend.audit_trust.anchor import AnchorWorker as CoreWorker
from backend.audit_trust.checkpoints import verify_checkpoint
from backend.audit_trust.external_anchor import ExternalBlockchainAnchor
from backend.audit_trust.transaction_safety import prepare_transaction,plan_hash
from backend.audit_trust.metamask_ingress import SignedIngress
from backend.audit_trust.public_ingress import PublicDelivery,expected_runtime
from eth_utils import keccak
from scripts.prepare_anchor_business_evidence import prepare
from scripts.sepolia_metamask_gateway import commit_data
from scripts.transaction_supervisor import ReadOnlyRPC
from tests.test_sepolia_public_ingress import LocalPublicShapeRPC,DEPLOY

def competing_transition(path):
    machine=TransactionStateMachine(Database(path))
    try:
        machine.transition('competing','PLAN_APPROVED',{'approved':True},0)
        return 'won'
    except Rejected:return 'conflict'

class SupervisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);prepare(self.root/'business')
        self.db=Database(self.root/'business/business.sqlite3');self.machine=TransactionStateMachine(self.db)
        self.wallet=Account.create();self.rpc=LocalPublicShapeRPC(self.wallet,'https://local-fixture.example')
        env=patch.dict(os.environ,{'ANCHOR_CONTRACT_CODE_HASH':'0x'+keccak(hexstr=expected_runtime(self.wallet.address)).hex()})
        env.start();self.addCleanup(env.stop)
        self.bundle=json.loads((self.root/'business/business-record.json').read_text())
        self.pem=(self.root/'business/audit-public-key.pem').read_text()
        self.plan=prepare_transaction(self.rpc,self.wallet.address,DEPLOY,'deploy')
        self.watcher=ReceiptWatcher(self.machine,self.rpc)
        self.recovery=RecoveryManager(self.machine,self.watcher)
    def send(self,plan):
        raw='0x'+self.wallet.sign_transaction(plan['transaction']).raw_transaction.hex()
        with patch.dict(os.environ,{'ANCHOR_ALLOW_SIGNING':'true','ANCHOR_ALLOW_BROADCAST':'true','ANCHOR_APPROVED_PLAN_HASH':plan_hash(plan)}):
            return PublicDelivery(SignedIngress(self.db,self.rpc,plan,plan_hash(plan))).send(raw)
    def worker(self,contract):
        ro=ReadOnlyRPC(self.rpc)
        core=CoreWorker(self.db,SimpleNamespace(verify_checkpoint=lambda cp:verify_checkpoint(cp,self.pem)),ExternalBlockchainAnchor(self.db,ro,contract))
        return AnchorWorker(self.machine,RecoveryManager(self.machine,ReceiptWatcher(self.machine,ro)),core,'operator')
    def test_nonce_conflict_restart_and_concurrent_reservations(self):
        manager=NonceManager(self.db,self.rpc)
        self.assertEqual(manager.reserve(self.wallet.address,'a'*64,0),0)
        with self.assertRaises(Rejected):manager.reserve(self.wallet.address,'b'*64,0)
        restarted=NonceManager(Database(self.db.path),self.rpc)
        self.assertEqual(restarted.reserve(self.wallet.address,'a'*64,0),0)
        with ThreadPoolExecutor(4) as pool:
            values=list(pool.map(lambda n:restarted.reserve(self.wallet.address,format(n,'064x')),range(1,9)))
        self.assertEqual(sorted(values),list(range(1,9)))
    def test_frozen_ingress_nonce_reservation_blocks_new_manager(self):
        self.send(self.plan)
        manager=NonceManager(self.db,self.rpc)
        with self.assertRaises(Rejected):manager.reserve(self.wallet.address,'b'*64,0)
        self.assertEqual(manager.reserve(self.wallet.address,'c'*64),1)
    def test_full_lifecycle_real_contract_and_anchor_restart(self):
        deploy=self.send(self.plan);self.rpc.local.evm.mine_blocks(2)
        contract=self.rpc.call('eth_getTransactionReceipt',[deploy])['contractAddress']
        worker=self.worker(contract);r=worker.run_once()[0]
        self.assertEqual(r['state'],'CONFIRMED')
        commit=prepare_transaction(self.rpc,self.wallet.address,commit_data(self.bundle,self.pem),'commit',contract)
        h=self.send(commit);self.rpc.local.evm.mine_blocks(2)
        result=worker.run_once();r=next(r for r in result if r['tx_hash']==h)
        self.assertIn(r['state'],('ANCHORED','FINALIZED'))
        self.assertTrue(worker.core_worker.verify_anchor(self.bundle['checkpoint']['checkpoint_id']))
        self.machine=TransactionStateMachine(Database(self.db.path));again=self.worker(contract).run_once()
        self.assertIn(next(x for x in again if x['tx_hash']==h)['state'],('ANCHORED','FINALIZED'))
        self.assertEqual(len(self.rpc.local.sent),2)
        self.assertEqual(worker.by_root(self.bundle['merkle_root'],'operator')['transactions'][0]['tx_hash'],h)
    def test_real_evm_revert_detected(self):
        deploy=self.send(self.plan);self.rpc.local.evm.mine_blocks(2)
        contract=self.rpc.call('eth_getTransactionReceipt',[deploy])['contractAddress']
        # Invalid selector genuinely executes and reverts in PyEVM, no success mock.
        p=copy.deepcopy(self.plan);p.update(kind='commit');p['transaction'].update(to=contract,nonce=1,data='0xdeadbeef',gas=100000)
        # Frozen ingress correctly refuses malformed Anchor calls. A genuine
        # local reverted transaction exercises the monitor separately.
        with self.assertRaises(Rejected):self.send(p)
        raw='0x'+self.wallet.sign_transaction(p['transaction']).raw_transaction.hex()
        h=self.rpc.local.call('eth_sendRawTransaction',[raw]);self.rpc.local.evm.mine_blocks(2)
        identifier=plan_hash(p);r=self.machine.create(identifier,'operator',p)
        for target in ('PLAN_APPROVED','SIGNED','CAPTURED','BROADCASTED','PENDING'):
            r=self.machine.transition(identifier,target,{'tx_hash':h},r['version'])
        self.assertEqual(self.watcher.poll(identifier)['state'],'FAILED')
    def test_reorg_by_real_evm_snapshot_and_recovery_never_resends(self):
        snapshot=self.rpc.local.evm.take_snapshot();h=self.send(self.plan);self.rpc.local.evm.mine_blocks(2)
        contract=self.rpc.call('eth_getTransactionReceipt',[h])['contractAddress'];worker=self.worker(contract)
        self.assertEqual(worker.run_once()[0]['state'],'CONFIRMED')
        self.rpc.local.evm.revert_to_snapshot(snapshot)
        r=worker.run_once()[0];self.assertEqual(r['state'],'REORGED')
        again=worker.run_once()[0];self.assertEqual(again['state'],'REORGED');self.assertEqual(len(self.rpc.local.sent),1)
    def test_invalid_transitions_cas_and_cross_owner(self):
        r=self.machine.create('test','alice',self.plan)
        with self.assertRaises(Rejected):self.machine.transition('test','FINALIZED',{'finality_verified':True})
        self.machine.transition('test','PLAN_APPROVED',{'approved':True},0)
        with self.assertRaises(Rejected):self.machine.transition('test','SIGNED',{'signed':True},0)
        with self.assertRaises(Rejected):self.machine.create('test','bob',self.plan)
    def test_timeout_and_quote_expiration_do_not_broadcast(self):
        p=copy.deepcopy(self.plan);p['quoted_at']=0
        self.machine.create('expired','alice',p)
        self.assertEqual(self.recovery.recover('expired')['state'],'EXPIRED')
        h=self.send(self.plan);contract=self.rpc.call('eth_getTransactionReceipt',[h])['contractAddress']
        # Without confirmations remain pending and timeout is operational, not a claim of failure.
        worker=self.worker(contract);worker.recovery.timeout=.000001
        r=worker.run_once()[0];self.assertEqual(r['state'],'PENDING')
        self.assertEqual(r['observation']['recovery'],'OPERATOR_INSPECTION_REQUIRED')
        self.assertEqual(len(self.rpc.local.sent),1)
    def test_http_queries_authenticated_and_owner_scoped(self):
        from backend.config import Config
        from backend.service import TrustaService
        from backend.server import create_server
        service=TrustaService(Config(db_path=str(self.root/'api/db.sqlite3'),signing_key=b'tx-api-test-key'))
        machine=TransactionStateMachine(service.db);h='0x'+'ab'*32
        r=machine.create('api','alice',self.plan)
        machine.transition('api','PLAN_APPROVED',{'tx_hash':h},r['version'])
        anchor_plan=copy.deepcopy(self.plan);anchor_plan['kind']='commit'
        anchor_plan['transaction']['data']=commit_data(self.bundle,self.pem)
        r=machine.create('api-anchor','alice',anchor_plan)
        machine.transition('api-anchor','PLAN_APPROVED',{'tx_hash':'0x'+'cd'*32},r['version'])
        server=create_server(service,'a',0,identities={'a':'alice','b':'bob'})
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for route in (f'/api/v1/tx/{h}',f'/api/v1/anchor/0x{self.bundle["merkle_root"]}'):
                for token,expected in [('a',200),('b',403),('unknown',403)]:
                    request=Request(f'http://127.0.0.1:{server.server_port}'+route,headers={'Authorization':'Bearer '+token})
                    try:
                        with urlopen(request,timeout=5) as response:status=response.status
                    except HTTPError as response:status=response.code;response.close()
                    self.assertEqual(status,expected)
        finally:server.shutdown();thread.join();server.server_close()
    def test_read_only_transport_rejects_broadcast_and_sign(self):
        for method in ('eth_sendRawTransaction','eth_sendTransaction','eth_signTransaction'):
            with self.assertRaises(Rejected):ReadOnlyRPC(self.rpc).call(method,[])

    def test_processes_use_durable_version_conflict_not_thread_lock(self):
        self.machine.create('competing','operator',self.plan)
        with ProcessPoolExecutor(2) as pool:
            results=list(pool.map(competing_transition,[self.db.path]*4))
        self.assertEqual(results.count('won'),1)
        self.assertEqual(self.machine.get('competing')['version'],1)

    def test_replacement_observed_after_real_reorg_without_automatic_send(self):
        snapshot=self.rpc.local.evm.take_snapshot();h=self.send(self.plan);self.rpc.local.evm.mine_blocks(2)
        contract=self.rpc.call('eth_getTransactionReceipt',[h])['contractAddress'];worker=self.worker(contract)
        worker.run_once();self.rpc.local.evm.revert_to_snapshot(snapshot);worker.run_once()
        replacement=copy.deepcopy(self.plan['transaction']);replacement['data']+='00'
        new_hash=self.rpc.local.call('eth_sendRawTransaction',['0x'+self.wallet.sign_transaction(replacement).raw_transaction.hex()])
        self.rpc.local.evm.mine_blocks(2)
        r=worker.recovery.inspect_replacement(plan_hash(self.plan),new_hash)
        self.assertEqual(r['state'],'REPLACED');self.assertEqual(r['tx_hash'],h)
        self.assertEqual(r['observation']['replacement_hash'],new_hash)
        self.assertFalse(r['observation']['original_execution_success'])

    def test_new_audit_event_checkpoint_uses_original_merkle_producer(self):
        from backend.config import Config
        from backend.service import TrustaService
        directory=self.root/'business'
        service=TrustaService(Config(db_path=self.db.path,crypto_key_dir=str(directory/'agent-keys'),
            signing_key=(directory/'core-root.key').read_bytes(),communication_version=3,banking_version=5,safety_profile='competition'))
        service.audit.append('local-operation',None,'SUPERVISION_TEST','operator',{'status':'observed'})
        worker=AnchorWorker(self.machine,self.recovery,service.anchors,'operator')
        checkpoint=worker.checkpoint_once()
        self.assertTrue(service.checkpoints.verify_checkpoint(checkpoint))
        self.assertEqual(service.anchors.status(checkpoint['checkpoint_id'])['status'],'PENDING')
        self.assertEqual(len(self.rpc.local.sent),0)

    def test_lost_broadcast_response_recovers_real_receipt_without_resend(self):
        original=self.rpc.call
        def lose(method,params):
            result=original(method,params)
            if method=='eth_sendRawTransaction':raise Rejected('ANCHOR_RPC_UNAVAILABLE')
            return result
        with patch.object(self.rpc,'call',side_effect=lose),self.assertRaises(Rejected):self.send(self.plan)
        self.rpc.local.evm.mine_blocks(2)
        h=self.rpc.local.sent[0];contract=self.rpc.call('eth_getTransactionReceipt',[h])['contractAddress']
        result=self.worker(contract).run_once()[0]
        self.assertEqual(result['state'],'CONFIRMED');self.assertEqual(len(self.rpc.local.sent),1)

    def test_observation_failure_does_not_starve_other_transactions(self):
        h=self.send(self.plan)
        second=prepare_transaction(self.rpc,self.wallet.address,DEPLOY,'deploy');other=self.send(second)
        self.rpc.local.evm.mine_blocks(2)
        contract=self.rpc.call('eth_getTransactionReceipt',[other])['contractAddress'];original=self.rpc.call
        def fail(method,params):
            if method=='eth_getTransactionReceipt' and params==[h]:raise Rejected('ANCHOR_RPC_UNAVAILABLE')
            return original(method,params)
        with patch.object(self.rpc,'call',side_effect=fail):results=self.worker(contract).run_once()
        self.assertEqual(results[0]['state'],'MONITOR_ERROR')
        self.assertEqual(results[1]['state'],'CONFIRMED');self.assertEqual(len(self.rpc.local.sent),2)

if __name__=='__main__':unittest.main()
