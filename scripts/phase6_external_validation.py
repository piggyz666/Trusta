"""Read-only live checks and honest external blockers. Does not submit a bank payment."""
import sys,json,os,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.config import Config
from backend.service import TrustaService
from backend.bank_sdk.configuration import reference_from_config
from backend.bank_sdk.contracts import BankRequest
from backend.domain import Rejected

def main():
    # Read .env without starting the ordinary on-disk banking service.
    for line in (ROOT/'.env').read_text(encoding='utf-8').splitlines() if (ROOT/'.env').exists() else []:
        if '=' in line and not line.lstrip().startswith('#'):
            k,v=line.split('=',1);os.environ.setdefault(k.strip(),v.strip())
    report={'provider':'starling','adapter_status':'ADAPTER_IMPLEMENTED_NOT_LIVE_VERIFIED','bank_status':'BLOCKED_BY_EXTERNAL_CREDENTIAL','real_bank_connected':False,'official_bank_sandbox_verified':False,'live_attempted':False,'local_contract_and_transport_tests':'tests/test_phase6_sdk.py',
      'REQUIRED_EXTERNAL_CONFIGURATION':['TRUSTA_BANK_CONFIG (path to config/bank.starling.example.toml copy)','BANK_ACCESS_TOKEN (Starling PAT)','customer_id (local owner mapping)','key_path/key_id (Starling registered signing key, writes only)','source_accounts/payees/maximum_amount/writes_enabled (allowlists, writes only)'],'missing_configuration':[]}
    path=os.getenv('TRUSTA_BANK_CONFIG','')
    if not path:report['missing_configuration'].append('TRUSTA_BANK_CONFIG')
    if not os.getenv('BANK_ACCESS_TOKEN'):report['missing_configuration'].append('BANK_ACCESS_TOKEN')
    with tempfile.TemporaryDirectory() as root:
        s=TrustaService(Config(db_path=root+'/bank.sqlite',signing_key=b'phase6-external-probe-local-keyx',communication_version=3,banking_version=5))
        if path:
            try:
                p=reference_from_config(path,s.db);report['live_attempted']=True
                result=p.request(BankRequest('accounts.read',p.customer_id));report['account_count']=len(result.data);report['environment']=p.manifest.environment
                if result.data:
                    balance=p.request(BankRequest('balance.read',p.customer_id,{'account_id':result.data[0]['id']}));report['balance_currency']=balance.data['currency'];report['balance_read_verified']=True
                report['bank_status']='OFFICIAL_BANK_SANDBOX_VERIFIED' if p.manifest.environment=='sandbox' else 'PRODUCTION_REAL_BANK_VERIFIED'
                report['official_bank_sandbox_verified']=p.manifest.environment=='sandbox';report['real_bank_connected']=p.manifest.environment=='production';report['verified_scope']='READ_ONLY; payment/status/webhook not live verified'
            except Exception as e:report['error_code']=e.code if isinstance(e,Rejected) else 'BANK_LIVE_READ_UNAVAILABLE'
        required=['ANCHOR_RPC_URL','ANCHOR_CONTRACT_ADDRESS','ANCHOR_WALLET_PRIVATE_KEY']
        anchor={'classification':'BLOCKED_BY_EXTERNAL_CREDENTIAL','external_blockchain_verified':False,'live_submitted':False,'tx_hash':None,'receipt':None,'chain_id':11155111,'contract':None,'read_back':None,'REQUIRED_EXTERNAL_CONFIGURATION':required+['testnet ETH for gas','ANCHOR_CONFIRMATIONS (default 3)','ANCHOR_REQUIRE_FINALIZED (optional)'],'missing_configuration':[k for k in required if not os.getenv(k)],'local_evm_test_scope':'tests/test_phase6_anchor.py; EthereumTester is not an external testnet'}
        if not anchor['missing_configuration']:
            from backend.audit_trust.external_anchor import ExternalBlockchainAnchor,EthereumRPC
            from backend.audit_trust.anchor import AnchorWorker
            try:
                adapter=ExternalBlockchainAnchor(s.db,EthereumRPC(os.environ['ANCHOR_RPC_URL']),os.environ['ANCHOR_CONTRACT_ADDRESS'])
                s.anchors=AnchorWorker(s.db,s.checkpoints,adapter)
                s.audit.append(None,None,'PHASE6_EXTERNAL_AUDIT_PROBE','runtime',{'synthetic':True})
                cp=s.checkpoints.generate_checkpoint();job=s.anchors.process_one(cp['checkpoint_id'])
                anchor['live_submitted']=bool(job.get('anchor_ref'));anchor['tx_hash']=job.get('anchor_ref')
                if job.get('anchor_ref'):
                    observation=adapter.inspect(job['anchor_ref']);anchor['proof']=observation.get('proof');anchor['external_blockchain_verified']=bool(observation.get('externally_verified'))
                    anchor['classification']='REAL_EXTERNAL_VERIFIED' if anchor['external_blockchain_verified'] else 'IMPLEMENTED_NOT_LIVE_VERIFIED'
                else:anchor['error_code']=job.get('error')
            except Exception as e:anchor['error_code']=e.code if isinstance(e,Rejected) else 'EXTERNAL_ANCHOR_UNAVAILABLE'
    (ROOT/'docs/reference-bank-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'docs/external-anchor-proof.json').write_text(json.dumps(anchor,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'bank_status':report['bank_status'],'external_anchor':anchor['classification']}))
if __name__=='__main__':main()
