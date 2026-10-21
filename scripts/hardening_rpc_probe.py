"""Read-only RPC discovery; locally configure a verified public second provider.

No transaction signing, broadcasting, or changes to primary/private RPC values.
"""
import json, os, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.estimate_sepolia_costs import ReadOnlyRPC, load_local_environment
from backend.audit_trust.transaction_safety import require_sepolia
from backend.audit_trust.external_anchor import EthereumRPC
from backend.domain import Rejected

CANDIDATES=[
    ('Alchemy','https://eth-sepolia.g.alchemy.com/v2/demo','https://www.alchemy.com/docs/choosing-a-web3-network'),
    ('OMNIA','https://endpoints.omniatech.io/v1/eth/sepolia/public','https://docs.omniatech.io/public-rpc-endpoints'),
    ('Pocket Network','https://eth-sepolia-testnet.api.pocket.network','https://docs.pocket.network/developers/supported-chains/'),
]

def main():
    load_local_environment()
    primary=ReadOnlyRPC(os.environ['ANCHOR_RPC_URL']);require_sepolia(primary)
    head=primary.call('eth_getBlockByNumber',['latest',False])
    block=primary.call('eth_getBlockByNumber',[hex(int(head['number'],16)-64),False])
    number=block['number']
    def probe(candidate):
        name,url,source=candidate
        row={'provider':name,'public_endpoint':url,'official_source':source,'methods':[]}
        rpc=ReadOnlyRPC(url)
        try:
            require_sepolia(rpc)
            common=rpc.call('eth_getBlockByNumber',[number,False])
            if common is None:raise Rejected('RPC_BLOCK_NOT_YET_AVAILABLE')
            row.update(chain_id=11155111,common_block_number=int(number,16),
                       common_block_hash=common['hash'],matches_primary=common['hash']==block['hash'],
                       status='LIVE_TESTED' if common['hash']==block['hash'] else 'FAILED')
        except (Rejected,ValueError,TypeError,KeyError) as exc:
            row.update(status='BLOCKED_EXTERNAL',reason=exc.code if isinstance(exc,Rejected) else type(exc).__name__)
            # Diagnose only public URLs, suppress all bodies and exception URLs.
            from urllib.request import Request, build_opener
            from urllib.error import HTTPError
            from backend.bank_sdk.transport import NoRedirect
            try:
                request=Request(url,data=b'{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}',headers={'Content-Type':'application/json'},method='POST')
                with build_opener(NoRedirect()).open(request,timeout=15) as response:
                    row['diagnostic_http_status']=response.status
            except HTTPError as error:row['diagnostic_http_status']=error.code
            except Exception as error:row['diagnostic_transport_type']=type(error).__name__
        row['methods']=rpc.methods
        return row
    with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(probe,CANDIDATES))
    working=next((r for r in rows if r['status']=='LIVE_TESTED'),None)
    checks={};configured=False
    if working:
        secondary=EthereumRPC(working['public_endpoint'],timeout=15)
        primary_full=EthereumRPC(os.environ['ANCHOR_RPC_URL'],timeout=15)
        stable_number=number
        first=primary_full.call('eth_getBlockByNumber',[stable_number,False])
        second=secondary.call('eth_getBlockByNumber',[stable_number,False])
        checks['stable_block_match']=first['hash']==second['hash']
        checks['stable_block_number']=int(stable_number,16)
        if first['transactions']:
            txhash=first['transactions'][0]
            tx=secondary.call('eth_getTransactionByHash',[txhash])
            a=primary_full.call('eth_getTransactionReceipt',[txhash])
            b=secondary.call('eth_getTransactionReceipt',[txhash])
            checks.update(sample_transaction_hash=txhash,transaction_query=tx['hash']==txhash,
                receipt_query=a==b,canonical_receipt=b['blockHash']==second['hash'],
                event_logs_match=a['logs']==b['logs'],sample_event_count=len(b['logs']),
                confirmations=int(head['number'],16)-int(b['blockNumber'],16)+1)
            address=b.get('contractAddress') or tx.get('to')
            if address:
                code_a=primary_full.call('eth_getCode',[address,stable_number])
                code_b=secondary.call('eth_getCode',[address,stable_number])
                checks.update(sample_code_query_match=code_a==code_b,sample_code_nonempty=code_b!='0x')
            # Protocol liveness only: this is not an AuditAnchor read-back.
            checks['eth_call_protocol']=secondary.call('eth_call',[{'to':'0x0000000000000000000000000000000000000000','data':'0x'},stable_number])=='0x'
        if all(checks.get(k) is True for k in ('stable_block_match','transaction_query','receipt_query','canonical_receipt','event_logs_match','sample_code_query_match','eth_call_protocol')):
            path=ROOT/'.env';lines=path.read_text(encoding='utf-8').splitlines()
            existing=[line.split('=',1)[1].strip() for line in lines if line.startswith('ANCHOR_VERIFY_RPC_URL=')]
            if not any(existing):
                lines=[line for line in lines if not line.startswith('ANCHOR_VERIFY_RPC_URL=')]
                lines.append('ANCHOR_VERIFY_RPC_URL='+working['public_endpoint'])
                path.write_text('\n'.join(lines)+'\n',encoding='utf-8');configured=True
    report={'measured_at':datetime.now(timezone.utc).isoformat(),'status':'LIVE_TESTED' if any(r['status']=='LIVE_TESTED' for r in rows) else 'BLOCKED_EXTERNAL',
            'scope':'PUBLIC_RPC_CHAIN_AND_COMMON_BLOCK_ONLY_NOT_ANCHOR_ACCEPTANCE',
            'primary_chain_id':11155111,'primary_common_block_number':int(number,16),
            'primary_common_block_hash':block['hash'],'candidates':rows,
            'actual_contract_verification':False,'signatures':0,'broadcasts':0,'spent_wei':0,
            'configuration_changed':configured,'read_protocol_checks':checks,
            'provider_independence_basis':'Pocket Network Foundation public portal vs Infura; independently named operator. This does not prove all underlying nodes have no shared infrastructure.',
            'anchor_readback_status':'BLOCKED_EXTERNAL_CONTRACT_NOT_DEPLOYED'}
    output=ROOT/'docs/backend-final-hardening/sepolia-independent-verification.json'
    if output.exists():
        prior=json.loads(output.read_text(encoding='utf-8'))
        report['prior_attempts']=prior.get('prior_attempts',[])+[{k:v for k,v in prior.items() if k!='prior_attempts'}]
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'providers':[{'provider':r['provider'],'status':r['status']} for r in rows]}))

if __name__=='__main__':main()
