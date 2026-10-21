"""Unsigned preparation by default; execution requires an exact approved plan."""
import sys,json,os,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.audit_trust.external_anchor import EthereumRPC
from backend.audit_trust.transaction_safety import require_sepolia, load_anchor_artifact, prepare_transaction, plan_hash, sign_approved_plan, broadcast_approved
from backend.domain import Rejected
from scripts.estimate_sepolia_costs import load_local_environment

def execute_plan(rpc,plan,record_path,db,wallet_env='ANCHOR_WALLET_PRIVATE_KEY'):
    """Share the durable wallet nonce boundary with commit; no automatic re-sign/retry."""
    from types import SimpleNamespace
    from backend.runtime.graph_claims import GraphClaims
    from backend.audit import AuditEngine
    from backend.security.local_files import protect_secret,write_secret_atomic
    require_sepolia(rpc)
    if plan.get('kind')!='deploy' or plan.get('transaction',{}).get('data')!='0x'+load_anchor_artifact()['bin']:
        raise Rejected('ANCHOR_DEPLOYMENT_ARTIFACT_MISMATCH')
    if plan.get('sender','').lower()!=os.getenv('ANCHOR_WALLET_ADDRESS','').lower():raise Rejected('ANCHOR_SIGNER_MISMATCH')
    claims=GraphClaims(SimpleNamespace(db=db,audit=AuditEngine(db)))
    sender=plan['sender'].lower();nonce=plan['transaction']['nonce'];binding='deploy:'+plan_hash(plan)
    with claims.hold('anchor-wallet:11155111:'+sender):
        if record_path.exists():raise Rejected('ANCHOR_DEPLOYMENT_ALREADY_RECORDED')
        with db.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS external_anchor_nonces(chain_id INTEGER NOT NULL,sender TEXT NOT NULL,nonce INTEGER NOT NULL,checkpoint_hash TEXT NOT NULL,tx_hash TEXT NOT NULL,PRIMARY KEY(chain_id,sender,nonce))')
            if conn.execute('SELECT 1 FROM external_anchor_nonces WHERE chain_id=? AND sender=? AND nonce=?',(11155111,sender,nonce)).fetchone():raise Rejected('ANCHOR_NONCE_RESERVED')
        signed=sign_approved_plan(rpc,plan,wallet_env);known='0x'+signed.hash.hex().removeprefix('0x')
        record={'chain_id':11155111,'tx_hash':known,'unsigned_plan':plan,'raw_tx':'0x'+signed.raw_transaction.hex().removeprefix('0x'),'contract_source':'contracts/AuditAnchorContract.sol','status':'PENDING'}
        with db.transaction() as conn:
            conn.execute('INSERT INTO external_anchor_nonces VALUES(?,?,?,?,?)',(11155111,sender,nonce,binding,known))
        with record_path.open('x',encoding='utf-8') as stream:
            protect_secret(record_path)
            json.dump(record,stream,indent=2);stream.flush();os.fsync(stream.fileno())
        actual=broadcast_approved(rpc,plan,signed)
        if actual.lower()!=known.lower():raise Rejected('ANCHOR_TX_HASH_MISMATCH')
        receipt=rpc.call('eth_getTransactionReceipt',[known])
        if receipt and int(receipt['status'],16)==1:record.update(status='MINED',contract=receipt['contractAddress'],receipt=receipt)
        write_secret_atomic(record_path,json.dumps(record,indent=2).encode())
        return record
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);parser.add_argument('--execute-approved-plan',type=Path);args=parser.parse_args()
    load_local_environment()
    if not os.getenv('ANCHOR_RPC_URL'):raise Rejected('BLOCKED_BY_EXTERNAL_CREDENTIAL')
    rpc=EthereumRPC(os.environ['ANCHOR_RPC_URL'])
    require_sepolia(rpc);artifact=load_anchor_artifact()
    if not args.execute_approved_plan:
        plan=prepare_transaction(rpc,os.getenv('ANCHOR_WALLET_ADDRESS',''),'0x'+artifact['bin'],'deploy')
        report={'status':'UNSIGNED_PREPARATION_ONLY','plan_hash':plan_hash(plan),'plan':plan}
        if args.output:args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,indent=2));return
    report=json.loads(args.execute_approved_plan.read_text());plan=report.get('plan',report)
    if plan['kind']!='deploy' or plan['transaction']['data']!='0x'+artifact['bin']:raise Rejected('ANCHOR_DEPLOYMENT_ARTIFACT_MISMATCH')
    if plan['sender'].lower()!=os.getenv('ANCHOR_WALLET_ADDRESS','').lower():raise Rejected('ANCHOR_SIGNER_MISMATCH')
    record_path=ROOT/'data/audit-anchor-deployment.json'
    from backend.persistence import Database
    db=Database(os.getenv('TRUSTA_DB',str(ROOT/'data/trusta.sqlite3')))
    record=execute_plan(rpc,plan,record_path,db)
    print(json.dumps({k:v for k,v in record.items() if k!='raw_tx'}))
if __name__=='__main__':
    try:main()
    except Rejected as exc:
        print(json.dumps({'status':'REJECTED','reason':exc.code}));raise SystemExit(1)
