"""Read-only Sepolia verification from public evidence, with no outbox/database/wallet."""
import hashlib
import re
from backend.domain import Rejected, digest
from backend.audit_trust.checkpoints import verify_checkpoint
from backend.audit_trust.external_anchor import ExternalBlockchainAnchor

TXHASH=re.compile(r'0x[0-9a-fA-F]{64}\Z')


def verify_public_anchor(rpc,checkpoint,trusted_public_key,contract,publisher,tx_hash,confirmations=3,code_hash=None):
    from eth_utils import is_address, keccak, to_checksum_address
    if (type(confirmations) is not int or not 1<=confirmations<=64 or not is_address(contract)
            or not is_address(publisher) or not isinstance(tx_hash,str) or not TXHASH.fullmatch(tx_hash)
            or code_hash is not None and (not isinstance(code_hash,str) or not TXHASH.fullmatch(code_hash))):
        raise Rejected('ANCHOR_PUBLIC_CONFIG_INVALID')
    contract=to_checksum_address(contract);publisher=to_checksum_address(publisher)
    checks={'checkpoint_signature':verify_checkpoint(checkpoint,trusted_public_key)}
    proof={'schema_version':1,'chain_id':11155111,'contract':contract,'publisher':publisher,'tx_hash':tx_hash,
           'required_confirmations':confirmations,'confirmations':0,'checks':checks,
           'verification_scope':'public evidence only; no submitting process state','external_blockchain_verified':False}
    if not checks['checkpoint_signature']:
        proof['reason']='INVALID_CHECKPOINT_SIGNATURE';return proof
    cp_hash=digest(checkpoint);identifier=hashlib.sha256(checkpoint['checkpoint_id'].encode()).hexdigest()
    sequence=checkpoint['last_event_sequence'];root=checkpoint['merkle_root']
    proof.update(checkpoint_hash=cp_hash,merkle_root=root,identifier_commitment=identifier,sequence=sequence)
    if sequence>=2**64:
        proof['reason']='ANCHOR_SEQUENCE_INVALID';return proof
    event_data='0x'+root+identifier+f'{sequence:064x}'+f'{1:064x}'
    calldata='0x'+ExternalBlockchainAnchor.selector('commit(bytes32,bytes32,bytes32,uint64,uint32)')+cp_hash+event_data[2:]
    topic='0x'+keccak(text='Committed(bytes32,bytes32,bytes32,uint64,uint32)').hex()
    read={'to':contract,'data':'0x'+ExternalBlockchainAnchor.selector('read(bytes32)')+cp_hash}
    try:
        checks['chain_id']=int(rpc.call('eth_chainId',[]),16)==11155111
        if not checks['chain_id']: raise Rejected('ANCHOR_CHAIN_MISMATCH')
        code=rpc.call('eth_getCode',[contract,'latest'])
        checks['contract_deployed']=isinstance(code,str) and bool(re.fullmatch(r'0x(?:[0-9a-fA-F]{2})+',code))
        if not checks['contract_deployed']: raise Rejected('ANCHOR_CONTRACT_NOT_DEPLOYED')
        proof['observed_code_hash']='0x'+keccak(hexstr=code).hex()
        if code_hash is not None: checks['pinned_code_hash']=proof['observed_code_hash'].lower()==code_hash.lower()
        onchain_publisher=rpc.call('eth_call',[{'to':contract,'data':'0x'+ExternalBlockchainAnchor.selector('publisher()')},'latest'])
        checks['contract_publisher']=onchain_publisher.lower()=='0x'+'0'*24+publisher[2:].lower()
        tx=rpc.call('eth_getTransactionByHash',[tx_hash]);receipt=rpc.call('eth_getTransactionReceipt',[tx_hash])
        if not tx or not receipt: raise Rejected('ANCHOR_NOT_MINED')
        checks['transaction_hash']=tx['hash'].lower()==receipt['transactionHash'].lower()==tx_hash.lower()
        checks['transaction_chain']=int(tx.get('chainId','0x0'),16)==11155111
        checks['destination']=tx['to'].lower()==receipt['to'].lower()==contract.lower()
        checks['publisher']=tx['from'].lower()==publisher.lower()
        checks['zero_value']=int(tx.get('value','0x0'),16)==0
        checks['input']=tx['input'].lower()==calldata.lower()
        checks['success']=int(receipt['status'],16)==1
        block_no=int(receipt['blockNumber'],16);block_hash=receipt['blockHash'];block=rpc.call('eth_getBlockByNumber',[receipt['blockNumber'],False])
        checks['canonical_block']=bool(block and block['hash'].lower()==block_hash.lower()==tx['blockHash'].lower()
            and int(block['number'],16)==block_no==int(tx['blockNumber'],16) and tx_hash.lower() in [h.lower() for h in block['transactions']])
        depth=max(0,int(rpc.call('eth_blockNumber',[]),16)-block_no+1)
        proof.update(block_number=block_no,block_hash=block_hash,confirmations=depth)
        checks['confirmations']=depth>=confirmations
        events=[log for log in receipt['logs'] if log.get('address','').lower()==contract.lower()
            and not log.get('removed',False) and [t.lower() for t in log.get('topics',[])]==[topic.lower(),'0x'+cp_hash]
            and log.get('data','').lower()==event_data.lower()
            and log.get('transactionHash','').lower()==tx_hash.lower() and log.get('blockHash','').lower()==block_hash.lower()
            and int(log.get('blockNumber','0x0'),16)==block_no]
        checks['event']=len(events)==1
        checks['historical_readback']=rpc.call('eth_call',[read,receipt['blockNumber']]).lower()==event_data.lower()
        checks['current_readback']=rpc.call('eth_call',[read,'latest']).lower()==event_data.lower()
        second=rpc.call('eth_getTransactionReceipt',[tx_hash]);second_block=rpc.call('eth_getBlockByNumber',[receipt['blockNumber'],False])
        checks['stable_receipt']=bool(second and digest(second)==digest(receipt))
        checks['stable_canonical_block']=bool(second_block and digest(second_block)==digest(block))
        proof['external_blockchain_verified']=all(checks.values())
        proof['reason']='VERIFIED' if proof['external_blockchain_verified'] else 'ANCHOR_EVIDENCE_MISMATCH'
    except Exception as exc:
        proof['reason']=exc.code if isinstance(exc,Rejected) else 'ANCHOR_RPC_OR_EVIDENCE_INVALID'
    return proof


def verify_two_rpcs(primary,independent,**public):
    from urllib.parse import urlsplit
    first_url=getattr(primary,'url',None);second_url=getattr(independent,'url',None)
    if primary is independent or (first_url and second_url
            and urlsplit(first_url).hostname==urlsplit(second_url).hostname):
        raise Rejected('ANCHOR_RPC_INDEPENDENCE_REQUIRED')
    first=verify_public_anchor(primary,**public);second=verify_public_anchor(independent,**public)
    same=all(first.get(k)==second.get(k) for k in ('checkpoint_hash','merkle_root','block_number','block_hash','observed_code_hash'))
    return {'primary':first,'independent':second,'consistent':same,
            'external_blockchain_verified':first['external_blockchain_verified'] and second['external_blockchain_verified'] and same}
