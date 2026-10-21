import re
import time
from backend.domain import Rejected
from backend.audit_trust.transaction_safety import require_sepolia

class NonceManager:
    """Never releases a captured/uncertain nonce, including after restart."""
    def __init__(self,db,rpc):
        self.db,self.rpc=db,rpc
        with db.transaction() as c:
            c.execute('CREATE TABLE IF NOT EXISTS supervised_nonces(sender TEXT NOT NULL,nonce INTEGER NOT NULL,plan_hash TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(sender,nonce),UNIQUE(sender,plan_hash))')

    def reserve(self,sender,plan_hash,exact_nonce=None):
        if exact_nonce is not None and (type(exact_nonce) is not int or exact_nonce<0):raise Rejected('NONCE_BINDING_INVALID')
        if not re.fullmatch('0x[0-9a-fA-F]{40}',sender) or not re.fullmatch('[0-9a-f]{64}',plan_hash):raise Rejected('NONCE_BINDING_INVALID')
        require_sepolia(self.rpc)
        pending=int(self.rpc.call('eth_getTransactionCount',[sender,'pending']),16)
        sender=sender.lower()
        with self.db.transaction() as c:
            existing=c.execute('SELECT nonce FROM supervised_nonces WHERE sender=? AND plan_hash=?',(sender,plan_hash)).fetchone()
            if existing:
                if exact_nonce is not None and exact_nonce!=existing['nonce']:raise Rejected('NONCE_CONFLICT')
                return existing['nonce']
            used={r['nonce'] for r in c.execute('SELECT nonce FROM supervised_nonces WHERE sender=?',(sender,))}
            legacy=c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='external_anchor_nonces'").fetchone()
            if legacy:used.update(r['nonce'] for r in c.execute('SELECT nonce FROM external_anchor_nonces WHERE chain_id=11155111 AND sender=?',(sender,)))
            nonce=exact_nonce if exact_nonce is not None else pending
            if type(nonce) is not int or nonce<pending:raise Rejected('NONCE_STALE')
            if exact_nonce is None:
                while nonce in used:nonce+=1
            if nonce in used:raise Rejected('NONCE_CONFLICT')
            c.execute('INSERT INTO supervised_nonces VALUES(?,?,?,?)',(sender,nonce,plan_hash,time.time()))
        return nonce
