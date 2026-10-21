import json
from backend.domain import Rejected, digest
from backend.audit_trust.transaction_safety import plan_hash
from backend.audit_trust.public_ingress import verify_signed_fields
from eth_account import Account
from eth_utils import keccak

class AnchorWorker:
    """Observe frozen ingress captures. No alternate signer or broadcast path."""
    def __init__(self,machine,recovery,core_worker,owner):
        self.machine,self.recovery,self.core_worker,self.owner=machine,recovery,core_worker,owner
        with machine.db.transaction() as c:
            c.execute('CREATE TABLE IF NOT EXISTS transaction_scan_cursor(owner TEXT PRIMARY KEY,last_row INTEGER NOT NULL)')

    def run_once(self,limit=64):
        if type(limit) is not int or not 1<=limit<=256:raise Rejected('TX_BATCH_INVALID')
        db=self.machine.db
        if not db.one("SELECT name FROM sqlite_master WHERE name='anchor_ingress_outbox'"):return []
        cursor=db.one('SELECT last_row FROM transaction_scan_cursor WHERE owner=?',(self.owner,))
        start=cursor['last_row'] if cursor else 0
        rows=db.rows('SELECT rowid,body FROM anchor_ingress_outbox WHERE rowid>? ORDER BY rowid LIMIT ?',(start,limit))
        if not rows:rows=db.rows('SELECT rowid,body FROM anchor_ingress_outbox ORDER BY rowid LIMIT ?',(limit,))
        results=[]
        for row in rows:
            try:
                record=self.observe(json.loads(row['body']))
            except Exception as exc:
                record={'tx_hash':None,'state':'MONITOR_ERROR','error':exc.code if isinstance(exc,Rejected) else 'OBSERVATION_FAILED'}
            results.append(record)
            with db.transaction() as c:
                c.execute('INSERT INTO transaction_scan_cursor VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET last_row=excluded.last_row',(self.owner,row['rowid']))
        return results

    def observe(self,capture):
        db=self.machine.db
        plan=capture['unsigned_plan'];identifier=plan_hash(plan)
        if capture['plan_hash']!=identifier or '0x'+keccak(hexstr=capture['raw_tx']).hex()!=capture['tx_hash'] or Account.recover_transaction(capture['raw_tx']).lower()!=plan['sender'].lower():raise Rejected('TX_CAPTURE_INVALID')
        from eth_account.typed_transactions import TypedTransaction
        from hexbytes import HexBytes
        decoded=TypedTransaction.from_bytes(HexBytes(capture['raw_tx'])).as_dict()
        tx={k:hex(decoded[k]) for k in ('type','chainId','nonce','value','gas','maxFeePerGas','maxPriorityFeePerGas','v','r','s')}
        tx.update(input='0x'+bytes(decoded['data']).hex(),to='0x'+bytes(decoded['to']).hex() if decoded['to'] else None,
                  accessList=list(decoded['accessList']),hash=capture['tx_hash'],**{'from':plan['sender']})
        if not verify_signed_fields(tx,plan,capture['tx_hash']):raise Rejected('TX_CAPTURE_INVALID')
        reservation=db.one('SELECT tx_hash FROM external_anchor_nonces WHERE chain_id=11155111 AND sender=? AND nonce=?',
            (plan['sender'].lower(),plan['transaction']['nonce']))
        if not reservation or reservation['tx_hash']!=capture['tx_hash']:raise Rejected('TX_CAPTURE_INVALID')
        record=self.machine.create(identifier,self.owner,plan)
        for source,target in [('CREATED','PLAN_APPROVED'),('PLAN_APPROVED','SIGNED'),('SIGNED','CAPTURED')]:
            if record['state']==source:record=self.machine.transition(identifier,target,{'tx_hash':capture['tx_hash'],'captured_signature_verified':True},record['version'])
        delivery=db.one('SELECT body FROM public_anchor_delivery WHERE plan_hash=?',(identifier,))
        if delivery and record['state']=='CAPTURED':
            sent=json.loads(delivery['body'])
            if sent['tx_hash']!=capture['tx_hash']:raise Rejected('TX_CAPTURE_INVALID')
            if sent['external_broadcasts']==1 and sent['status']=='SENT':record=self.machine.transition(identifier,'BROADCASTED',{'tx_hash':capture['tx_hash'],'attempt_persisted':True},record['version'])
        record=self.recovery.recover(identifier)
        if plan['kind']=='commit' and record['state'] in ('CONFIRMED','ANCHORED','FINALIZED'):
            cp_hash=plan['transaction']['data'][10:74]
            cp=next((json.loads(r['body']) for r in db.rows('SELECT body FROM audit_checkpoints') if digest(json.loads(r['body']))==cp_hash),None)
            if not cp:raise Rejected('ANCHOR_CHECKPOINT_NOT_FOUND')
            submission=db.one('SELECT tx_hash FROM external_anchor_submissions WHERE hash=?',(cp_hash,))
            if not submission or submission['tx_hash']!=record['tx_hash']:raise Rejected('TX_CAPTURE_INVALID')
            job=self.core_worker.process_one(cp['checkpoint_id'])
            if job['status']=='CONFIRMED' and self.core_worker.verify_anchor(cp['checkpoint_id']):
                e=record['observation']|{'anchor_verified':True,'root':cp['merkle_root']}
                if record['state']=='CONFIRMED':record=self.machine.transition(identifier,'ANCHORED',e,record['version'])
                final=self.recovery.watcher.rpc.call('eth_getBlockByNumber',['finalized',False])
                if final and int(final['number'],16)>=e['block_number'] and record['state']=='ANCHORED':
                    record=self.machine.transition(identifier,'FINALIZED',e|{'finality_verified':True},record['version'])
        return record

    def by_root(self,root,owner):
        import re
        if isinstance(root,str) and root.startswith('0x'):root=root[2:]
        if not re.fullmatch('[0-9a-fA-F]{64}',root):raise Rejected('INVALID_ANCHOR_ROOT')
        rows=self.machine.db.rows('SELECT body FROM supervised_transactions WHERE owner=?',(owner,))
        matched=[]
        for row in rows:
            r=json.loads(row['body']);p=r['plan']
            if p['kind']=='commit' and p['transaction']['data'][74:138].lower()==root.lower():
                matched.append({k:r[k] for k in ('tx_hash','state','observation','updated_at')})
        if not matched:raise Rejected('TX_ACCESS_DENIED')
        return {'root':root.lower(),'transactions':matched}

    def checkpoint_once(self,max_events=256):
        """Original Merkle algorithm writes a pending job, never submits here."""
        if type(max_events) is not int or not 1<=max_events<=4096:raise Rejected('TX_BATCH_INVALID')
        return self.core_worker.checkpoints.generate_checkpoint(max_events)
