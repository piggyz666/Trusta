"""Existing endpoints only; hard-deny every transaction/write/signing RPC."""
import json
import os
import sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.estimate_sepolia_costs import load_local_environment
from backend.audit_trust.external_anchor import EthereumRPC
from backend.audit_trust.transaction_safety import require_sepolia
from backend.domain import Rejected

READS={'eth_chainId','eth_getBlockByNumber','eth_getTransactionByHash','eth_getTransactionReceipt',
       'eth_blockNumber','eth_getCode','eth_call'}

class RPC:
    def __init__(self,url):self.rpc=EthereumRPC(url,timeout=15);self.methods=[]
    def call(self,method,params):
        if method not in READS:raise Rejected('READ_ONLY_METHOD_REQUIRED')
        self.methods.append(method);return self.rpc.call(method,params)

def main():
    load_local_environment()
    result={'measured_at':datetime.now(timezone.utc).isoformat(),'signatures':0,'broadcasts':0,'spent_wei':0,
        'scope':'LIVE_DUAL_RPC_PROTOCOL_CHECK_ONLY_NOT_AUDITANCHOR_ACCEPTANCE','external_blockchain_verified':False}
    primary=secondary=None
    try:
        first=os.environ.get('ANCHOR_RPC_URL','');second=os.environ.get('ANCHOR_VERIFY_RPC_URL','')
        if not first or not second:raise Rejected('ANCHOR_RPC_CONFIGURATION_MISSING')
        if urlsplit(first).hostname==urlsplit(second).hostname:raise Rejected('ANCHOR_RPC_INDEPENDENCE_REQUIRED')
        primary,secondary=RPC(first),RPC(second);require_sepolia(primary);require_sepolia(secondary)
        a=primary.call('eth_getBlockByNumber',['latest',False]);b=secondary.call('eth_getBlockByNumber',['latest',False])
        number=hex(min(int(a['number'],16),int(b['number'],16))-64)
        blocks=[r.call('eth_getBlockByNumber',[number,False]) for r in (primary,secondary)]
        checks={'chain_ids':11155111,'distinct_provider_hosts':True,'common_block':blocks[0]['hash']==blocks[1]['hash']}
        result.update(common_block_number=int(number,16),common_block_hash=blocks[0]['hash'])
        if not checks['common_block']:raise Rejected('ANCHOR_RPC_BLOCK_MISMATCH')
        if blocks[0]['transactions']:
            txhash=blocks[0]['transactions'][0]
            txs=[r.call('eth_getTransactionByHash',[txhash]) for r in (primary,secondary)]
            receipts=[r.call('eth_getTransactionReceipt',[txhash]) for r in (primary,secondary)]
            checks.update(transaction_binding=all(t and t['hash']==txhash and t['blockHash']==blocks[0]['hash'] for t in txs),
                receipt_binding=all(r and r['transactionHash']==txhash and r['blockHash']==blocks[0]['hash'] for r in receipts),
                receipt_logs_match=receipts[0]['logs']==receipts[1]['logs'],receipt_status_match=receipts[0]['status']==receipts[1]['status'])
            result.update(sample_transaction_hash=txhash,sample_event_count=len(receipts[0]['logs']))
            target=receipts[0].get('contractAddress') or txs[0].get('to')
            if target:
                codes=[r.call('eth_getCode',[target,number]) for r in (primary,secondary)]
                checks['code_match']=codes[0]==codes[1]
        checks['eth_call_read_protocol']=all(r.call('eth_call',[{'to':'0x'+'0'*40,'data':'0x'},number])=='0x' for r in (primary,secondary))
        result.update(checks=checks,status='LIVE_READS_VERIFIED' if all(checks.values()) else 'EXTERNAL_MISMATCH',
            operator_independence_basis='Configured Infura and Pocket provider hosts; underlying infrastructure independence is not proven.')
    except Exception as exc:
        result.update(status='BLOCKED_EXTERNAL',reason=exc.code if isinstance(exc,Rejected) else type(exc).__name__)
    result['primary_methods']=primary.methods if primary else []
    result['secondary_methods']=secondary.methods if secondary else []
    path=ROOT/'data/zero-defects/dual-rpc-readonly.json';path.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('status','reason','signatures','broadcasts') if k in result}))
    return 0 if result['status']=='LIVE_READS_VERIFIED' else 1

if __name__=='__main__':raise SystemExit(main())
