"""Anchor-only signed-byte ingress. Capture first; no network broadcast capability.

This transport is deliberately not a replacement for the existing broadcaster.
Its database must be the same database used by deployment/commit nonce claims.
Production activation is withheld until actual extension routing is verified.
"""
import copy
import json
import time
from types import SimpleNamespace
from eth_account import Account
from eth_account.typed_transactions import TypedTransaction
from eth_utils import keccak
from hexbytes import HexBytes
from backend.domain import Rejected, canonical
from backend.audit_trust.transaction_safety import (
    AnchorFeePolicy, CHAIN_ID, plan_hash, prepare_transaction, require_sepolia,
)
from backend.runtime.graph_claims import GraphClaims
from backend.audit import AuditEngine
from backend.security.local_files import protect_secret


def validate_signed_plan(rpc, plan, raw, approved_hash):
    """Validate public signed bytes without reading, requesting or exporting a key."""
    require_sepolia(rpc)
    if not approved_hash or plan_hash(plan) != approved_hash:
        raise Rejected('ANCHOR_TRANSACTION_APPROVAL_REQUIRED')
    tx = plan.get('transaction')
    fields = {'type', 'chainId', 'nonce', 'value', 'data', 'gas',
              'maxFeePerGas', 'maxPriorityFeePerGas', 'to'}
    if (not isinstance(tx, dict) or set(tx)-fields or not fields-{'to'} <= set(tx)
            or any(type(tx[k]) is not int or tx[k] < 0 for k in fields-{'data', 'to'})
            or tx['type'] != 2 or tx['chainId'] != CHAIN_ID or tx['value'] != 0):
        raise Rejected('ANCHOR_TRANSACTION_BINDING_INVALID')
    if (type(plan.get('quoted_at')) is not int or type(plan.get('quote_ttl')) is not int
            or not 0 <= time.time()-plan['quoted_at'] <= plan['quote_ttl'] <= 120):
        raise Rejected('ANCHOR_QUOTE_EXPIRED')
    if not isinstance(raw, str) or not raw.startswith('0x02') or len(raw) > 100_000:
        raise Rejected('ANCHOR_SIGNED_TRANSACTION_INVALID')
    try:
        payload = HexBytes(raw)
        decoded = TypedTransaction.from_bytes(payload).as_dict()
        recovered = Account.recover_transaction(payload)
    except Exception:
        raise Rejected('ANCHOR_SIGNED_TRANSACTION_INVALID') from None
    if recovered.lower() != plan['sender'].lower():
        raise Rejected('ANCHOR_SIGNER_MISMATCH')
    if (decoded.get('type') != 2 or decoded.get('accessList') not in ([], ())
            or not 0 < decoded['s'] <= 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0
            or any(decoded.get(k) != tx[k] for k in fields-{'data', 'to'})
            or bytes(decoded['data']) != bytes.fromhex(tx['data'][2:])
            or bytes(decoded.get('to', b'')) != bytes.fromhex(tx.get('to', '0x')[2:])):
        raise Rejected('ANCHOR_SIGNED_TRANSACTION_MISMATCH')
    fresh = prepare_transaction(rpc, recovered, tx['data'], plan['kind'], tx.get('to'))
    if (fresh['transaction']['nonce'] != tx['nonce']
            or fresh['transaction']['gas'] > tx['gas']
            or fresh['transaction']['maxFeePerGas'] > tx['maxFeePerGas']):
        raise Rejected('ANCHOR_QUOTE_CHANGED')
    policy = AnchorFeePolicy()
    gas_cap = policy.deploy_max_gas if plan['kind'] == 'deploy' else policy.commit_max_gas
    cost_cap = policy.deploy_max_cost if plan['kind'] == 'deploy' else policy.commit_max_cost
    if (not 21000 <= tx['gas'] <= gas_cap
            or not 0 <= tx['maxPriorityFeePerGas'] <= policy.max_priority_fee
            or not tx['maxPriorityFeePerGas'] <= tx['maxFeePerGas'] <= policy.max_fee_per_gas
            or tx['gas'] * tx['maxFeePerGas'] > cost_cap
            or fresh['balance_wei'] < tx['gas']*tx['maxFeePerGas']+policy.minimum_balance):
        raise Rejected('ANCHOR_APPROVED_BUDGET_INVALID')
    return '0x'+keccak(payload).hex()


class SignedIngress:
    """Single immutable approved plan, shared nonce table, transactional outbox.

    A duplicate returns the existing capture hash, not a new nonce or broadcast.
    Expired approvals are rejected even for duplicates; operator recovery reads
    the durable record instead of silently obtaining a fresh transaction.
    """
    def __init__(self, db, rpc, plan, approved_hash):
        self.db, self.rpc = db, rpc
        self.plan, self.approved_hash = copy.deepcopy(plan), approved_hash
        if not approved_hash or plan_hash(self.plan) != approved_hash:
            raise Rejected('ANCHOR_TRANSACTION_APPROVAL_REQUIRED')
        self.claims = GraphClaims(SimpleNamespace(db=db, audit=AuditEngine(db)))
        # Applying the ACL before storage also protects the SQLite journal/WAL.
        from pathlib import Path
        location = Path(db.path)
        protect_secret(location.parent)
        protect_secret(location)
        with db.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS external_anchor_nonces(chain_id INTEGER NOT NULL,sender TEXT NOT NULL,nonce INTEGER NOT NULL,checkpoint_hash TEXT NOT NULL,tx_hash TEXT NOT NULL,PRIMARY KEY(chain_id,sender,nonce))')
            conn.execute('CREATE TABLE IF NOT EXISTS anchor_ingress_outbox(plan_hash TEXT PRIMARY KEY,tx_hash TEXT NOT NULL UNIQUE,body TEXT NOT NULL)')

    def capture(self, raw):
        sender = self.plan['sender'].lower()
        with self.claims.hold('anchor-wallet:11155111:'+sender):
            old = self.inspect()
            if old:
                # Reconciliation of the already captured bytes is not a new
                # send permission. It remains valid when chain nonce advances.
                require_sepolia(self.rpc)
                if (plan_hash(self.plan) != self.approved_hash or old['plan_hash'] != self.approved_hash
                        or old['unsigned_plan'] != self.plan or old['raw_tx'] != raw
                        or old['status'] != 'CAPTURED_NOT_BROADCAST'
                        or old['tx_hash'] != '0x'+keccak(hexstr=raw).hex()):
                    raise Rejected('ANCHOR_SIGNED_TRANSACTION_MISMATCH')
                reservation = self.db.one('SELECT tx_hash FROM external_anchor_nonces WHERE chain_id=? AND sender=? AND nonce=?',
                                          (CHAIN_ID, sender, self.plan['transaction']['nonce']))
                if not reservation or reservation['tx_hash'] != old['tx_hash']:
                    raise Rejected('ANCHOR_NONCE_RESERVED')
                return old
            tx_hash = validate_signed_plan(self.rpc, self.plan, raw, self.approved_hash)
            tx = self.plan['transaction']
            record = {'schema_version': 1, 'plan_hash': self.approved_hash,
                      'tx_hash': tx_hash, 'raw_tx': raw, 'unsigned_plan': self.plan,
                      'status': 'CAPTURED_NOT_BROADCAST', 'broadcasts': 0}
            binding = ('deploy:'+self.approved_hash if self.plan['kind'] == 'deploy'
                       else tx['data'][10:74])
            # Both records commit in the same SQLite FULL synchronous transaction.
            with self.db.transaction() as conn:
                prior = conn.execute('SELECT body FROM anchor_ingress_outbox WHERE plan_hash=?', (self.approved_hash,)).fetchone()
                if prior:
                    old = json.loads(prior['body'])
                    if old['tx_hash'] != tx_hash or old['raw_tx'] != raw:
                        raise Rejected('ANCHOR_SIGNED_TRANSACTION_MISMATCH')
                    return old
                reservation = conn.execute('SELECT tx_hash FROM external_anchor_nonces WHERE chain_id=? AND sender=? AND nonce=?', (CHAIN_ID, sender, tx['nonce'])).fetchone()
                if reservation:
                    raise Rejected('ANCHOR_NONCE_RESERVED')
                conn.execute('INSERT INTO external_anchor_nonces VALUES(?,?,?,?,?)',
                             (CHAIN_ID, sender, tx['nonce'], binding, tx_hash))
                conn.execute('INSERT INTO anchor_ingress_outbox VALUES(?,?,?)',
                             (self.approved_hash, tx_hash, canonical(record)))
            return record

    def inspect(self):
        row = self.db.one('SELECT body FROM anchor_ingress_outbox WHERE plan_hash=?', (self.approved_hash,))
        return json.loads(row['body']) if row else None
