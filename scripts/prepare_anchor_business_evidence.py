"""Execute a real LOCAL Virtual Bank task through frozen Core and export its proof.

Not a real-money transfer or a made-up/random root. Does not load .env or call
any external bank/RPC. An existing receipt is reused, never executed twice.
"""
import argparse
import json
import secrets
from pathlib import Path
from backend.config import Config
from backend.service import TrustaService
from backend.domain import Rejected, canonical, digest
from backend.audit_trust.records import verify_record
from backend.security.local_files import protect_secret, write_secret_atomic


def prepare(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);protect_secret(directory)
    keyfile=directory/'core-root.key'
    if not keyfile.exists():write_secret_atomic(keyfile,secrets.token_bytes(32),create_only=True)
    config=Config(db_path=str(directory/'business.sqlite3'),crypto_key_dir=str(directory/'agent-keys'),
        signing_key=keyfile.read_bytes(),communication_version=3,banking_version=5,safety_profile='competition')
    service=TrustaService(config)
    # Durable task selection even if the process died before exporting files.
    existing=service.db.rows('SELECT body FROM receipts')
    if existing:receipt=json.loads(existing[0]['body'])
    else:
        task=service.create_task('给张三转0.01元')
        if task['status']!='WAITING_USER':raise Rejected('ANCHOR_LOCAL_TASK_NOT_AWAITING_CONFIRMATION')
        task=service.authorize(task['task_id'],'demo',digest(task['proposed_actions'][0]),True)
        if task['status']!='COMPLETED':raise Rejected('ANCHOR_LOCAL_TASK_FAILED')
        receipt=task['receipt']
    while service.checkpoints.generate_checkpoint() is not None:pass
    bundle=service.records.query('demo',receipt_id=receipt['receipt_id'])
    pinned,_=service.checkpoints.public_key(bundle['checkpoint'])
    if not verify_record(bundle,pinned):raise Rejected('ANCHOR_LOCAL_RECEIPT_PROOF_INVALID')
    for name,value in [('business-record.json',bundle),('checkpoint.json',bundle['checkpoint'])]:
        target=directory/name
        if target.exists() and json.loads(target.read_text(encoding='utf-8'))!=value:
            raise Rejected('ANCHOR_FROZEN_EVIDENCE_CHANGED')
        if not target.exists():write_secret_atomic(target,(canonical(value)+'\n').encode())
    target=directory/'audit-public-key.pem'
    if target.exists() and target.read_text(encoding='utf-8')!=pinned:raise Rejected('ANCHOR_PIN_CHANGED')
    if not target.exists():write_secret_atomic(target,pinned.encode())
    return {'scope':'REAL_LOCAL_VIRTUAL_BANK_EXECUTION_NOT_EXTERNAL_BANK_PAYMENT','receipt_id':receipt['receipt_id'],
        'task_id':receipt['task_id'],'receipt_commitment':digest({k:v for k,v in receipt.items() if k!='audit_ref'}),
        'checkpoint_hash':digest(bundle['checkpoint']),'root':bundle['checkpoint']['merkle_root'],
        'sequence':bundle['checkpoint']['last_event_sequence'],'record_verified':True,
        'external_bank_writes':0,'external_broadcasts':0}

def main():
    p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,default=Path('data/sepolia-public-acceptance/business'));a=p.parse_args()
    result=prepare(a.directory);write_secret_atomic(a.directory/'business-evidence-summary.json',(canonical(result)+'\n').encode());print(json.dumps(result))

if __name__=='__main__':main()
