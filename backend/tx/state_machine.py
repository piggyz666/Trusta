import json
import re
import time
from backend.domain import Rejected, canonical

STATES = ('CREATED','PLAN_APPROVED','SIGNED','CAPTURED','BROADCASTED','PENDING',
          'CONFIRMED','ANCHORED','FINALIZED','FAILED','REORGED','EXPIRED','REPLACED')
EDGES = {
    'CREATED': {'PLAN_APPROVED','EXPIRED','FAILED'},
    'PLAN_APPROVED': {'SIGNED','EXPIRED','FAILED'}, 'SIGNED': {'CAPTURED','EXPIRED','FAILED'},
    'CAPTURED': {'BROADCASTED','PENDING','FAILED'}, 'BROADCASTED': {'PENDING','FAILED','REORGED'},
    'PENDING': {'CONFIRMED','FAILED','REORGED','REPLACED'},
    'CONFIRMED': {'ANCHORED','REORGED'}, 'ANCHORED': {'FINALIZED','REORGED'},
    'FINALIZED': {'REORGED'}, 'REORGED': {'PENDING','FAILED','REPLACED'},
    'FAILED': {'REORGED'}, 'EXPIRED': set(), 'REPLACED': set()}

def transaction_hash(value):
    if not isinstance(value,str) or not re.fullmatch('0x[0-9a-fA-F]{64}',value):
        raise Rejected('INVALID_TRANSACTION_HASH')
    return value.lower()

class TransactionStateMachine:
    def __init__(self,db):
        self.db=db
        with db.transaction() as c:
            c.execute('CREATE TABLE IF NOT EXISTS supervised_transactions(id TEXT PRIMARY KEY,owner TEXT NOT NULL,tx_hash TEXT UNIQUE,state TEXT NOT NULL,version INTEGER NOT NULL,body TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS transaction_transitions(id TEXT NOT NULL,version INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(id,version))')

    def create(self,identifier,owner,plan):
        if not isinstance(owner,str) or not owner or plan['transaction']['chainId']!=11155111:
            raise Rejected('TX_CONFIGURATION_INVALID')
        record={'id':identifier,'owner':owner,'state':'CREATED','version':0,'plan':plan,
                'tx_hash':None,'observation':{},'created_at':time.time(),'updated_at':time.time()}
        with self.db.transaction() as c:
            prior=c.execute('SELECT body FROM supervised_transactions WHERE id=?',(identifier,)).fetchone()
            if prior:
                prior=json.loads(prior['body'])
                if prior['owner']!=owner or prior['plan']!=plan:raise Rejected('TX_BINDING_CONFLICT')
                return prior
            c.execute('INSERT INTO supervised_transactions VALUES(?,?,?,?,?,?)',(identifier,owner,None,'CREATED',0,canonical(record)))
        return record

    def get(self,identifier):
        row=self.db.one('SELECT body FROM supervised_transactions WHERE id=?',(identifier,))
        if not row:raise Rejected('TX_NOT_FOUND')
        return json.loads(row['body'])

    def by_hash(self,value,owner):
        row=self.db.one('SELECT body FROM supervised_transactions WHERE tx_hash=? AND owner=?',(transaction_hash(value),owner))
        if not row:raise Rejected('TX_ACCESS_DENIED')
        record=json.loads(row['body'])
        return {k:record[k] for k in ('tx_hash','state','version','updated_at','observation')}

    def transition(self,identifier,target,evidence,expected_version=None):
        if target not in STATES or not isinstance(evidence,dict) or not evidence:raise Rejected('TX_EVIDENCE_REQUIRED')
        with self.db.transaction() as c:
            row=c.execute('SELECT body FROM supervised_transactions WHERE id=?',(identifier,)).fetchone()
            if not row:raise Rejected('TX_NOT_FOUND')
            record=json.loads(row['body']);old=record['state']
            if expected_version is not None and record['version']!=expected_version:raise Rejected('TX_VERSION_CONFLICT')
            if old==target:return record
            if target not in EDGES[old]:raise Rejected('TX_INVALID_TRANSITION')
            if target in ('CONFIRMED','ANCHORED','FINALIZED'):
                required={'CONFIRMED':'receipt_verified','ANCHORED':'anchor_verified','FINALIZED':'finality_verified'}[target]
                if evidence.get(required) is not True:raise Rejected('TX_EVIDENCE_REQUIRED')
            h=transaction_hash(evidence['tx_hash']) if 'tx_hash' in evidence else record['tx_hash']
            if record['tx_hash'] and h!=record['tx_hash']:raise Rejected('TX_HASH_CONFLICT')
            record.update(state=target,tx_hash=h,version=record['version']+1,observation=evidence,updated_at=time.time())
            c.execute('UPDATE supervised_transactions SET tx_hash=?,state=?,version=?,body=? WHERE id=?',(h,target,record['version'],canonical(record),identifier))
            c.execute('INSERT INTO transaction_transitions VALUES(?,?,?)',(identifier,record['version'],canonical({'from':old,'to':target,'evidence':evidence,'at':record['updated_at']})))
        return record

    def refresh(self,identifier,evidence,expected_version):
        with self.db.transaction() as c:
            record=json.loads(c.execute('SELECT body FROM supervised_transactions WHERE id=?',(identifier,)).fetchone()['body'])
            if record['version']!=expected_version:raise Rejected('TX_VERSION_CONFLICT')
            record.update(observation=evidence,updated_at=time.time(),version=record['version']+1)
            c.execute('UPDATE supervised_transactions SET version=?,body=? WHERE id=?',(record['version'],canonical(record),identifier))
            c.execute('INSERT INTO transaction_transitions VALUES(?,?,?)',(identifier,record['version'],canonical({'from':record['state'],'to':record['state'],'evidence':evidence,'at':record['updated_at']})))
        return record
