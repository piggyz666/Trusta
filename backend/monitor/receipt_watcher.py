from backend.domain import Rejected
from backend.audit_trust.transaction_safety import require_sepolia
from backend.audit_trust.public_ingress import verify_signed_fields

class ReceiptWatcher:
    def __init__(self,machine,rpc,confirmations=3):
        if type(confirmations) is not int or not 3<=confirmations<=64:raise Rejected('TX_CONFIRMATIONS_INVALID')
        self.machine,self.rpc,self.confirmations=machine,rpc,confirmations

    def poll(self,identifier):
        require_sepolia(self.rpc)
        record=self.machine.get(identifier);h=record['tx_hash']
        if not h:return record
        tx=self.rpc.call('eth_getTransactionByHash',[h]);receipt=self.rpc.call('eth_getTransactionReceipt',[h])
        prior=record['observation'];e={'tx_hash':h,'confirmations':0,'receipt_verified':False}
        if record['state'] in ('ANCHORED','FINALIZED'):
            e.update({k:prior[k] for k in ('root','anchor_verified','finality_verified') if k in prior})
        target=record['state']
        if receipt:
            if not tx or not verify_signed_fields(tx,record['plan'],h):raise Rejected('TX_CHAIN_EVIDENCE_MISMATCH')
            if receipt['transactionHash'].lower()!=h or receipt['blockHash'].lower()!=tx['blockHash'].lower() or receipt['blockNumber']!=tx['blockNumber']:
                raise Rejected('TX_CHAIN_EVIDENCE_MISMATCH')
            block=self.rpc.call('eth_getBlockByNumber',[receipt['blockNumber'],False])
            stable=self.rpc.call('eth_getTransactionReceipt',[h])
            canonical=bool(block and stable and block['hash']==receipt['blockHash']==stable['blockHash'])
            e.update(block_hash=receipt['blockHash'],block_number=int(receipt['blockNumber'],16),canonical=canonical,receipt=receipt)
            if not canonical:target='REORGED'
            elif record['state'] in ('CONFIRMED','ANCHORED','FINALIZED') and prior.get('block_hash') and prior['block_hash']!=receipt['blockHash']:target='REORGED'
            elif int(receipt['status'],16)==0:target='FAILED'
            else:
                head=int(self.rpc.call('eth_blockNumber',[]),16)
                e['confirmations']=max(0,head-e['block_number']+1)
                e['receipt_verified']=e['confirmations']>=self.confirmations
                if e['receipt_verified'] and record['state'] in ('PENDING','BROADCASTED','CAPTURED','REORGED'):target='CONFIRMED'
                elif not e['receipt_verified']:target='PENDING' if record['state'] not in ('CONFIRMED','ANCHORED','FINALIZED') else 'REORGED'
        elif record['state'] in ('CONFIRMED','ANCHORED','FINALIZED','FAILED') and prior.get('block_hash'):target='REORGED'
        elif tx:target='PENDING'
        if target=='CONFIRMED' and record['state'] not in ('PENDING','CONFIRMED'):
            record=self.machine.transition(identifier,'PENDING',e,record['version'])
        if target!=record['state']:return self.machine.transition(identifier,target,e,record['version'])
        return self.machine.refresh(identifier,e,record['version'])
