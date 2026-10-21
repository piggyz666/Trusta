"""Read-only public verification + local delivery attestation export, never sends."""
import argparse
import json
import os
import sqlite3
from pathlib import Path
from backend.domain import Rejected, canonical
from backend.audit_trust.external_anchor import EthereumRPC
from backend.audit_trust.transaction_safety import plan_hash
from backend.audit_trust.public_ingress import verify_signed_fields
from scripts.verify_sepolia_acceptance import verify
from scripts.estimate_sepolia_costs import load_local_environment


def explorer_transaction_fields(body):
    """Read transaction-specific DOM text, not unrelated scripts or metadata."""
    from html.parser import HTMLParser
    class Fields(HTMLParser):
        def __init__(self):
            super().__init__();self.spans=[];self.hidden=0;self.values={}
        def handle_starttag(self,tag,attrs):
            if tag in ('script','style'):self.hidden+=1
            if tag=='span':
                identifier=dict(attrs).get('id')
                self.spans.append(identifier if identifier in ('spanTxHash','data-status','data-blk') else None)
        def handle_endtag(self,tag):
            if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
            if tag=='span' and self.spans:self.spans.pop()
        def handle_data(self,data):
            if not self.hidden:
                for identifier in reversed(self.spans):
                    if identifier:
                        self.values[identifier]=self.values.get(identifier,'')+data;break
    parser=Fields();parser.feed(body)
    return {k:v.strip() for k,v in parser.values.items()}


def observe_explorer(tx_hash, block_number):
    """A URL alone is not explorer evidence. Fail closed on WAF/404/timeout."""
    import re
    import time
    from urllib.request import Request, build_opener
    from backend.bank_sdk.transport import NoRedirect
    from backend.network import read_bounded
    if not re.fullmatch(r'0x[0-9a-fA-F]{64}',tx_hash):raise Rejected('ANCHOR_TX_HASH_INVALID')
    url='https://sepolia.etherscan.io/tx/'+tx_hash
    try:
        request=Request(url,headers={'User-Agent':'TRUSTA-read-only-acceptance/1.0'},method='GET')
        with build_opener(NoRedirect()).open(request,timeout=15) as response:
            body=read_bounded(response,2_097_152,time.monotonic()+15).decode('utf-8',errors='replace')
            fields=explorer_transaction_fields(body)
            verified=(response.status==200 and fields.get('spanTxHash','').lower()==tx_hash.lower()
                and fields.get('data-status')=='Success'
                and fields.get('data-blk') in (str(block_number),f'{block_number:,}'))
        return {'url':url,'page_observed':verified,'method':'BOUNDED_READ_ONLY_HTTPS','block_number':block_number}
    except Exception:return {'url':url,'page_observed':False,'reason':'EXPLORER_UNAVAILABLE_OR_UNVERIFIED'}


def delivery_evidence(db_path, plan, tx_hash):
    # Operator attestation only. Third-party chain/proof verifier above never
    # opens this DB and must not mistake it for proof of network state.
    connection=sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    try:
        fingerprint=plan_hash(plan)
        capture=connection.execute('SELECT body FROM anchor_ingress_outbox WHERE plan_hash=?',(fingerprint,)).fetchone()
        delivery=connection.execute('SELECT body FROM public_anchor_delivery WHERE plan_hash=?',(fingerprint,)).fetchone()
        nonce=connection.execute('SELECT tx_hash FROM external_anchor_nonces WHERE chain_id=? AND sender=? AND nonce=?',
            (11155111,plan['sender'].lower(),plan['transaction']['nonce'])).fetchone()
        if not capture or not delivery or not nonce:raise Rejected('ANCHOR_DELIVERY_EVIDENCE_MISSING')
        captured=json.loads(capture['body']);sent=json.loads(delivery['body'])
        from eth_account import Account
        from eth_utils import keccak
        if (captured['unsigned_plan']!=plan or captured['plan_hash']!=fingerprint or sent['plan_hash']!=fingerprint
                or not tx_hash==captured['tx_hash']==sent['tx_hash']==nonce['tx_hash']
                or '0x'+keccak(hexstr=captured['raw_tx']).hex()!=tx_hash
                or Account.recover_transaction(captured['raw_tx']).lower()!=plan['sender'].lower()
                or sent['external_broadcasts']!=1 or not sent['captured_before_attempt']
                or not plan['quoted_at']<=sent['attempted_at']<=plan['quoted_at']+plan['quote_ttl']):
            raise Rejected('ANCHOR_DELIVERY_EVIDENCE_MISMATCH')
        return {'scope':'LOCAL_OPERATOR_PERSISTENCE_ATTESTATION_NOT_INDEPENDENT_CHAIN_PROOF',
            'capture':captured,'delivery':sent,'nonce_bound':True,'raw_signature_recovered':True}
    finally:connection.close()


def main():
    p=argparse.ArgumentParser()
    for name in ('deployment-plan','commit-plan','deployment-tx','commit-tx','bundle','public-key','db','output-directory'):
        p.add_argument('--'+name,required=True)
    a=p.parse_args();load_local_environment()
    plan=json.loads(Path(a.deployment_plan).read_text(encoding='utf-8'));commit=json.loads(Path(a.commit_plan).read_text(encoding='utf-8'))
    bundle=json.loads(Path(a.bundle).read_text(encoding='utf-8'));pinned=Path(a.public_key).read_text(encoding='utf-8')
    rpc=[EthereumRPC(os.environ[name],timeout=15) for name in ('ANCHOR_RPC_URL','ANCHOR_VERIFY_RPC_URL')]
    proof=verify(*rpc,plan,a.deployment_tx,a.commit_tx,bundle,pinned,commit)
    if not proof['external_blockchain_verified']:raise Rejected('ANCHOR_PUBLIC_ACCEPTANCE_NOT_VERIFIED')
    local=[delivery_evidence(a.db,p,h) for p,h in ((plan,a.deployment_tx),(commit,a.commit_tx))]
    commit_receipts=[r.call('eth_getTransactionReceipt',[a.commit_tx]) for r in rpc]
    if commit_receipts[0]!=commit_receipts[1]:raise Rejected('ANCHOR_RPC_EVIDENCE_MISMATCH')
    explorers=[observe_explorer(a.deployment_tx,proof['deployment_primary']['block_number']),
        observe_explorer(a.commit_tx,proof['root_commit']['primary']['block_number'])]
    explorer_passed=all(item['page_observed'] for item in explorers)
    result={'scope':'ETHEREUM_SEPOLIA_PUBLIC','external_blockchain_verified':True,
        'production_compatible':explorer_passed,'ingress_deliveries_verified':True,
        'production_compatibility_scope':'These two exact approved ingress deliveries only; not universal MetaMask routing certification.',
        'external_broadcasts_per_transaction':[1,1],'external_broadcasts_total':2,
        'local_operator_attestation':local,'independent_public_proof':proof,
        'explorer_urls':{'deployment':'https://sepolia.etherscan.io/tx/'+a.deployment_tx,
            'commit':'https://sepolia.etherscan.io/tx/'+a.commit_tx},
        'explorer_observations':explorers,'explorer_page_observed':explorer_passed,
        'final_acceptance':'PASS' if explorer_passed else 'CHAIN_AND_INGRESS_VERIFIED_EXPLORER_REVIEW_PENDING'}
    output=Path(a.output_directory);output.mkdir(parents=True,exist_ok=True)
    artifacts={'Sepolia_Deployment_Receipt.json':proof['deployment_primary']['receipt'],
        'Sepolia_Root_Commit_Receipt.json':commit_receipts[0],'ExternalAnchorProof.json':proof,
        'Independent_RPC_Verification.json':proof,'Ingress_Delivery_Evidence.json':result,
        'Business_Record_Proof.json':bundle}
    for name,value in artifacts.items():
        path=output/name
        if path.exists():raise Rejected('ANCHOR_ACCEPTANCE_OUTPUT_ALREADY_EXISTS')
    for name,value in artifacts.items():(output/name).write_text(canonical(value)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('scope','external_blockchain_verified','production_compatible','external_broadcasts_total','final_acceptance')}))

if __name__=='__main__':main()
