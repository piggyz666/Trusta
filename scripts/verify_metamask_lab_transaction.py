"""Read-only acceptance of one actual local MetaMask transaction; no signer/sends."""
import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit
import requests
import rlp
from trie import HexaryTrie
from eth_account import Account
from eth_account.typed_transactions import TypedTransaction
from eth_utils import keccak, to_checksum_address
from hexbytes import HexBytes
from backend.audit_trust.transaction_safety import load_anchor_artifact, plan_hash, AnchorFeePolicy

if not __debug__:
    raise RuntimeError('Acceptance checks require Python assertions; optimized execution is refused')


def verify(directory):
    directory=Path(directory)
    session=json.loads((directory/'operator-session.json').read_text())
    assert session['scope']=='LOCAL_PYEVM_ONLY'
    for route in ('ingress','bypass'):
        u=urlsplit(session[route+'_rpc'])
        assert u.scheme=='http' and u.hostname=='127.0.0.1' and u.path.startswith('/'+route+'/') and not u.query
    conn=sqlite3.connect('file:'+str((directory/'ingress.sqlite3').resolve()).replace('\\','/')+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    try:
        rows=conn.execute('SELECT body FROM anchor_ingress_outbox').fetchall()
        assert len(rows)==1,'Expected exactly the first experiment transaction'
        record=json.loads(rows[0]['body']);p=record['unsigned_plan'];tx=p['transaction'];h=record['tx_hash']
        reservation=dict(conn.execute('SELECT * FROM external_anchor_nonces WHERE chain_id=? AND sender=? AND nonce=?',
            (11155111,p['sender'].lower(),tx['nonce'])).fetchone())
    finally:conn.close()
    raw=HexBytes(record['raw_tx']);decoded=TypedTransaction.from_bytes(raw).as_dict();sender=Account.recover_transaction(raw)
    assert sender.lower()==p['sender'].lower()==session['wallet'].lower()
    assert sender.lower()!='0xf84c2e84f43460a94bb24ffeb6d4a61b75de86be'
    assert plan_hash(p)==record['plan_hash'] and '0x'+keccak(raw).hex()==h
    assert all(decoded[k]==tx[k] for k in ('type','chainId','nonce','value','gas','maxFeePerGas','maxPriorityFeePerGas'))
    assert decoded['type']==2 and decoded['chainId']==11155111 and decoded['value']==0
    assert decoded['accessList'] in ([],()) and bytes(decoded['to'])==b''
    assert bytes(decoded['data']).hex()==tx['data'][2:]==load_anchor_artifact()['bin']
    assert 0<decoded['s']<=0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0
    assert reservation['tx_hash']==h and reservation['checkpoint_hash']=='deploy:'+record['plan_hash']
    policy=AnchorFeePolicy()
    assert tx['gas']<=policy.deploy_max_gas and tx['maxFeePerGas']<=policy.max_fee_per_gas
    assert tx['maxPriorityFeePerGas']<=policy.max_priority_fee and tx['gas']*tx['maxFeePerGas']<=policy.deploy_max_cost
    events=json.loads((directory/'rpc-events.json').read_text())
    attempts=[(i,e) for i,e in enumerate(events) if e.get('hash')==h and e.get('attempted')]
    executions=[(i,e) for i,e in enumerate(events) if e.get('hash')==h and e.get('new_local_executions')]
    assert len(attempts)==len(executions)==1 and attempts[0][0]<executions[0][0]
    assert attempts[0][1]['persisted_before_execution'] and executions[0][1]['new_local_executions']==1
    assert all(e.get('route')=='ingress' for e in events if e.get('hash')==h)
    assert 0<=attempts[0][1]['observed_at']-p['quoted_at']<=p['quote_ttl']<=120
    client=events[attempts[0][0]-1]
    assert client['method']=='eth_sendRawTransaction' and client['origin']=='chrome-extension://nkbihfbeogaeaoehlefnkodbefgpgknn'
    http=requests.Session();http.trust_env=False
    def call(route,method,params):
        try:
            r=http.post(session[route+'_rpc'],json={'jsonrpc':'2.0','id':1,'method':method,'params':params},timeout=5)
            assert r.status_code==200
            return r.json()
        except requests.RequestException:raise RuntimeError('Local read failed; URL omitted') from None
    transaction=call('ingress','eth_getTransactionByHash',[h])['result']
    block=call('ingress','eth_getBlockByNumber',[transaction['blockNumber'],False])['result']
    assert block['transactions']==[h] and block['hash']==transaction['blockHash']
    assert call('bypass','eth_getBlockByNumber',[block['number'],False])['result']['hash']==block['hash']
    assert call('ingress','eth_chainId',[])['result']==call('bypass','eth_chainId',[])['result']=='0xaa36a7'
    gas=int(block['gasUsed'],16);assert gas<=tx['gas']
    # One transaction in this actual block: authenticate its typed receipt
    # directly against the running EVM's receiptsRoot; no replay or mining.
    receipt_bytes=b'\x02'+rlp.encode([1,gas,b'\x00'*256,[]])
    trie=HexaryTrie({});key=rlp.encode(0);trie[key]=receipt_bytes
    assert '0x'+trie.root_hash.hex()==block['receiptsRoot']
    proof=trie.get_proof(key)
    assert HexaryTrie.get_from_proof(trie.root_hash,key,proof)==receipt_bytes
    transaction_trie=HexaryTrie({});transaction_trie[key]=bytes(raw)
    assert '0x'+transaction_trie.root_hash.hex()==block['transactionsRoot']
    address=to_checksum_address(keccak(rlp.encode([bytes.fromhex(sender[2:]),tx['nonce']]))[-20:])
    code=call('ingress','eth_getCode',[address,block['number']])['result']
    assert code==call('bypass','eth_getCode',[address,block['number']])['result'] and code!='0x'
    publisher=call('ingress','eth_call',[{'from':sender,'to':address,'data':'0x'+keccak(text='publisher()')[:4].hex()},block['number']])['result']
    assert publisher=='0x'+'0'*24+sender[2:].lower()
    creation=bytes.fromhex(load_anchor_artifact()['bin'])
    # This pinned artifact copies 0x901 bytes from offset 0x63. Its two
    # immutable publisher PUSH32 words are filled by the constructor.
    assert bytes.fromhex('6109016100635f39') in creation[:99]
    template=creation[99:];placeholder=b'\x7f'+b'\x00'*32
    assert len(template)==0x901 and template.count(placeholder)==2
    expected=template.replace(placeholder,b'\x7f'+bytes.fromhex(publisher[2:]))
    assert bytes.fromhex(code[2:])==expected
    effective=min(tx['maxFeePerGas'],int(block['baseFeePerGas'],16)+tx['maxPriorityFeePerGas'])
    rpc_receipt=call('ingress','eth_getTransactionReceipt',[h])
    return {'scope':'LOCAL_PYEVM_ONLY','transaction_hash':h,'plan_hash':record['plan_hash'],
        'single_transaction_pre_execution_safety':'PASS','production_compatible':False,
        'contract_address':address,'publisher':sender,'gas_limit':tx['gas'],'gas_used':gas,
        'effective_gas_price':effective,'actual_local_cost_wei':gas*effective,
        'runtime_code_hash':'0x'+keccak(hexstr=code).hex(),'runtime_matches_pinned_artifact':True,
        'raw_signed_transaction':record['raw_tx'],'unsigned_plan':p,'nonce_reservation':reservation,
        'execution_evidence':[client,attempts[0][1],executions[0][1]],
        'receipt':{'transactionHash':h,'blockHash':block['hash'],'blockNumber':block['number'],
            'transactionIndex':'0x0','status':'0x1','gasUsed':hex(gas),'cumulativeGasUsed':hex(gas),
            'effectiveGasPrice':hex(effective),'contractAddress':address,'from':sender,'to':None,'logs':[],'type':'0x2'},
        'receipt_source':'CONSENSUS_RECEIPT_RECONSTRUCTED_AND_TRIE_AUTHENTICATED_NOT_RPC_JSON',
        'receipt_proof':{'root':block['receiptsRoot'],'key':'0x'+key.hex(),'value':'0x'+receipt_bytes.hex(),
            'nodes':['0x'+rlp.encode(node).hex() for node in proof]},'canonical_local_block':block,
        'rpc_receipt_response':rpc_receipt,'rpc_receipt_transport_passed':'result' in rpc_receipt,
        'bypass_observed_for_this_transaction':False,'external_broadcasts':0,
        'limitations':['Same-simulator routes are not independent Sepolia RPCs',
            'This transaction does not establish future Smart Transaction or RPC-switch routing safety',
            'Capture status denotes no public broadcast; it is not the local execution status']}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,default=Path('data/metamask-validation/lab2'))
    args=parser.parse_args();result=verify(args.directory)
    output=Path('data/metamask-validation/actual-transaction-acceptance.json')
    output.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:result[k] for k in ('transaction_hash','single_transaction_pre_execution_safety','contract_address','publisher','gas_used','runtime_code_hash','rpc_receipt_transport_passed','production_compatible')}))
