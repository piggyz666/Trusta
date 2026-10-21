"""Continuous read-only Sepolia recovery, using the existing capture/outbox DB."""
import argparse
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from backend.persistence import Database
from backend.audit_trust.external_anchor import EthereumRPC,ExternalBlockchainAnchor
from backend.audit_trust.anchor import AnchorWorker as CoreWorker
from backend.audit_trust.checkpoints import verify_checkpoint
from backend.tx.state_machine import TransactionStateMachine
from backend.tx.recovery_manager import RecoveryManager
from backend.monitor.receipt_watcher import ReceiptWatcher
from backend.anchor.worker import AnchorWorker
from backend.domain import Rejected
from scripts.estimate_sepolia_costs import load_local_environment

class ReadOnlyRPC:
    def __init__(self,rpc):self.rpc=rpc
    def call(self,method,params):
        if method not in {'eth_chainId','eth_getTransactionByHash','eth_getTransactionReceipt',
                'eth_getBlockByNumber','eth_blockNumber','eth_call','eth_getCode','eth_getTransactionCount'}:
            raise Rejected('SUPERVISOR_READ_ONLY_REQUIRED')
        return self.rpc.call(method,params)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',required=True);p.add_argument('--contract',required=True)
    p.add_argument('--public-key',required=True);p.add_argument('--owner',required=True)
    p.add_argument('--interval',type=float,default=15);p.add_argument('--once',action='store_true')
    p.add_argument('--output',type=Path)
    p.add_argument('--checkpoint-dir',type=Path,help='Existing local Core keys/config directory; generates pending checkpoints only')
    a=p.parse_args()
    if not 1<=a.interval<=300:raise Rejected('TX_INTERVAL_INVALID')
    load_local_environment()
    rpc=ReadOnlyRPC(EthereumRPC(os.environ['ANCHOR_RPC_URL'],timeout=15))
    db=Database(a.db);key=Path(a.public_key).read_text(encoding='utf-8')
    core=CoreWorker(db,SimpleNamespace(verify_checkpoint=lambda cp:verify_checkpoint(cp,key)),ExternalBlockchainAnchor(db,rpc,a.contract))
    if a.checkpoint_dir:
        from backend.config import Config
        from backend.service import TrustaService
        directory=a.checkpoint_dir.resolve()
        if (directory/'business.sqlite3').resolve()!=Path(a.db).resolve() or not (directory/'core-root.key').is_file():
            raise Rejected('TX_CHECKPOINT_CONFIGURATION_INVALID')
        service=TrustaService(Config(db_path=str(directory/'business.sqlite3'),crypto_key_dir=str(directory/'agent-keys'),
            signing_key=(directory/'core-root.key').read_bytes(),communication_version=3,banking_version=5,safety_profile='competition'))
        core.checkpoints=service.checkpoints
    machine=TransactionStateMachine(db)
    worker=AnchorWorker(machine,RecoveryManager(machine,ReceiptWatcher(machine,rpc)),core,a.owner)
    stop=threading.Event()
    try:
        while not stop.is_set():
            try:
                with core.claims.hold('anchor-job:continuous-supervisor',recover_metadata=True):
                    if a.checkpoint_dir:worker.checkpoint_once()
                    records=worker.run_once()
                result={'status':'degraded' if any(r['state']=='MONITOR_ERROR' for r in records) else 'ok',
                    'transactions':[{k:r[k] for k in ('tx_hash','state','error') if k in r} for r in records],'new_broadcasts':0}
            except Exception as exc:
                result={'status':'degraded','error':exc.code if isinstance(exc,Rejected) else 'SUPERVISOR_FAILED','new_broadcasts':0}
            if a.output:
                from backend.security.local_files import write_secret_atomic
                write_secret_atomic(a.output,(json.dumps(result)+'\n').encode())
            print(json.dumps(result),flush=True)
            if a.once:
                if result['status']!='ok':raise SystemExit(1)
                return
            stop.wait(a.interval)
    except KeyboardInterrupt:stop.set()

if __name__=='__main__':main()
