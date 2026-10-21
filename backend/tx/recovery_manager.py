import time
from backend.domain import Rejected

class RecoveryManager:
    def __init__(self,machine,watcher,timeout=300):
        if type(timeout) not in (int,float) or timeout<=0:raise Rejected('TX_TIMEOUT_INVALID')
        self.machine,self.watcher,self.timeout=machine,watcher,timeout

    def recover(self,identifier):
        record=self.machine.get(identifier)
        if record['state'] in ('CREATED','PLAN_APPROVED','SIGNED') and time.time()>record['plan']['quoted_at']+record['plan']['quote_ttl']:
            return self.machine.transition(identifier,'EXPIRED',{'reason':'QUOTE_EXPIRED'},record['version'])
        if record['tx_hash'] and record['state'] not in ('EXPIRED','REPLACED'):
            record=self.watcher.poll(identifier)
            if record['state'] in ('CAPTURED','BROADCASTED','PENDING','REORGED') and time.time()-record['created_at']>self.timeout:
                evidence=record['observation']|{'recovery':'OPERATOR_INSPECTION_REQUIRED','automatic_rebroadcast':False}
                record=self.machine.refresh(identifier,evidence,record['version'])
        return record

    def inspect_replacement(self,identifier,replacement_hash):
        """Observe an independently signed same-nonce tx, never create one."""
        from backend.tx.state_machine import transaction_hash
        from backend.audit_trust.transaction_safety import require_sepolia
        from backend.audit_trust.public_ingress import verify_signed_fields
        rpc=self.watcher.rpc;require_sepolia(rpc)
        record=self.machine.get(identifier);h=transaction_hash(replacement_hash)
        if h==record['tx_hash']:raise Rejected('TX_REPLACEMENT_INVALID')
        tx=rpc.call('eth_getTransactionByHash',[h])
        if not tx:raise Rejected('TX_REPLACEMENT_UNOBSERVED')
        fields={k:int(tx[k],16) for k in ('type','chainId','nonce','value','gas','maxFeePerGas','maxPriorityFeePerGas')}
        fields.update(data=tx['input'])
        if tx.get('to'):fields['to']=tx['to']
        plan={'sender':record['plan']['sender'],'transaction':fields}
        if fields['nonce']!=record['plan']['transaction']['nonce'] or not verify_signed_fields(tx,plan,h):raise Rejected('TX_REPLACEMENT_INVALID')
        receipt=rpc.call('eth_getTransactionReceipt',[h])
        if not receipt:raise Rejected('TX_REPLACEMENT_UNCONFIRMED')
        block=rpc.call('eth_getBlockByNumber',[receipt['blockNumber'],False])
        head=int(rpc.call('eth_blockNumber',[]),16)
        if receipt['transactionHash']!=h or not block or block['hash']!=receipt['blockHash'] or head-int(receipt['blockNumber'],16)+1<self.watcher.confirmations:
            raise Rejected('TX_REPLACEMENT_UNCONFIRMED')
        return self.machine.transition(identifier,'REPLACED',{'replacement_hash':h,'reason':'CANONICAL_SAME_NONCE_TRANSACTION','original_execution_success':False},record['version'])
