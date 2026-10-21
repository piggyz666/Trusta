"""Sepolia delivery of already approved MetaMask bytes; never holds a wallet key.

Capture and nonce reservation commit first. An uncertain send is reconciled by
hash using reads, never resubmitted automatically, including after restart.
"""
import json
import os
import time
from types import SimpleNamespace
from hexbytes import HexBytes
from backend.domain import Rejected, canonical, digest
from backend.audit_trust.metamask_ingress import SignedIngress, validate_signed_plan
from backend.audit_trust.transaction_safety import (
    CHAIN_ID, plan_hash, require_sepolia, broadcast_approved, load_anchor_artifact,
)


def expected_runtime(publisher):
    # This layout is valid only for the exact pinned compiler artifact.
    creation = bytes.fromhex(load_anchor_artifact()['bin'])
    runtime = creation[99:]
    placeholder = b'\x7f'+b'\0'*32
    if len(runtime) != 2305 or runtime.count(placeholder) != 2:
        raise Rejected('ANCHOR_ARTIFACT_LAYOUT_INVALID')
    return '0x'+runtime.replace(placeholder, b'\x7f'+bytes.fromhex(publisher[2:]).rjust(32,b'\0')).hex()


def verify_signed_fields(tx, plan, tx_hash):
    """Reconstruct typed public bytes, independently of provider's from field."""
    from eth_account import Account
    from eth_account.typed_transactions import TypedTransaction
    from eth_utils import keccak
    try:
        p=plan['transaction']
        fields={k:int(tx[k],16) for k in ('type','chainId','nonce','value','gas','maxFeePerGas','maxPriorityFeePerGas','v','r','s')}
        # RPC addresses are often lowercase. Rebuild the identical 20 address
        # bytes; eth-account rejects non-checksummed text for a contract call.
        fields.update(data=tx['input'],to=HexBytes(tx['to']) if tx.get('to') else b'',accessList=tx.get('accessList',[]))
        encoded=HexBytes(TypedTransaction.from_dict(fields).encode())
        return (fields['type']==2 and fields['chainId']==CHAIN_ID and fields['value']==0
            and 0<fields['s']<=0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0
            and tx.get('accessList')==[] and tx['input'].lower()==p['data'].lower()
            and (tx.get('to') or '').lower()==p.get('to','').lower()
            and all(fields[k]==p[k] for k in ('type','chainId','nonce','value','gas','maxFeePerGas','maxPriorityFeePerGas'))
            and Account.recover_transaction(encoded).lower()==plan['sender'].lower()
            and tx['from'].lower()==plan['sender'].lower()
            and '0x'+keccak(encoded).hex()==tx_hash.lower()==tx['hash'].lower())
    except (ValueError,KeyError,TypeError):return False


class PublicDelivery:
    def __init__(self, ingress):
        self.ingress = ingress
        self.db, self.rpc = ingress.db, ingress.rpc
        with self.db.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS public_anchor_delivery(plan_hash TEXT PRIMARY KEY,tx_hash TEXT NOT NULL UNIQUE,body TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS external_anchor_submissions(hash TEXT PRIMARY KEY,root TEXT NOT NULL,tx_hash TEXT NOT NULL,body TEXT NOT NULL)')

    def inspect(self):
        row = self.db.one('SELECT body FROM public_anchor_delivery WHERE plan_hash=?', (self.ingress.approved_hash,))
        return json.loads(row['body']) if row else None

    def _save(self, record):
        with self.db.transaction() as conn:
            conn.execute('UPDATE public_anchor_delivery SET body=? WHERE plan_hash=?', (canonical(record),record['plan_hash']))

    def reconcile(self, raw=None):
        """Read-only recovery works even with a dead process's conservative claim.

        It does not release that claim, sign, renew, send, or allocate a nonce.
        """
        from eth_utils import keccak
        record=self.inspect();captured=self.ingress.inspect()
        if not record or not captured:raise Rejected('ANCHOR_CAPTURE_NOT_FOUND')
        plan=self.ingress.plan;fingerprint=self.ingress.approved_hash
        reservation=self.db.one('SELECT tx_hash FROM external_anchor_nonces WHERE chain_id=? AND sender=? AND nonce=?',
            (CHAIN_ID,plan['sender'].lower(),plan['transaction']['nonce']))
        if (record['plan_hash']!=fingerprint or captured['plan_hash']!=fingerprint
                or captured['unsigned_plan']!=plan or record['tx_hash']!=captured['tx_hash']
                or captured['tx_hash']!='0x'+keccak(hexstr=captured['raw_tx']).hex()
                or raw is not None and raw!=captured['raw_tx']
                or not reservation or reservation['tx_hash']!=captured['tx_hash']):
            raise Rejected('ANCHOR_SIGNED_TRANSACTION_MISMATCH')
        require_sepolia(self.rpc)
        observed=self.rpc.call('eth_getTransactionByHash',[record['tx_hash']])
        return {'tx_hash':record['tx_hash'],'known_to_rpc':bool(observed and observed.get('hash','').lower()==record['tx_hash'].lower()),
            'new_broadcasts':0,'record':record}

    def send(self, raw):
        # Approval is independently required by broadcast_approved, even if a
        # capture-only transport was accidentally used to construct this object.
        plan, approved = self.ingress.plan, self.ingress.approved_hash
        if (os.getenv('ANCHOR_ALLOW_SIGNING') != 'true' or os.getenv('ANCHOR_ALLOW_BROADCAST') != 'true'
                or os.getenv('ANCHOR_APPROVED_PLAN_HASH') != approved):
            raise Rejected('ANCHOR_BROADCAST_APPROVAL_REQUIRED')
        if self.inspect():
            reconciled=self.reconcile(raw)
            if reconciled['known_to_rpc']:return reconciled['tx_hash']
            raise Rejected('ANCHOR_UNCERTAIN_SEND_REQUIRES_INSPECTION')
        captured = self.ingress.capture(raw)
        with self.ingress.claims.hold('anchor-wallet:11155111:'+plan['sender'].lower()):
            prior = self.inspect()
            if prior:
                if prior['tx_hash'] != captured['tx_hash']:
                    raise Rejected('ANCHOR_SIGNED_TRANSACTION_MISMATCH')
                # Even SENT is re-checked on network; never claim a mined result
                # from SQLite. This branch cannot invoke a sending RPC method.
                require_sepolia(self.rpc)
                observed = self.rpc.call('eth_getTransactionByHash',[prior['tx_hash']])
                if observed and observed.get('hash','').lower() == prior['tx_hash'].lower():
                    return prior['tx_hash']
                raise Rejected('ANCHOR_UNCERTAIN_SEND_REQUIRES_INSPECTION')
            validate_signed_plan(self.rpc, plan, raw, approved)
            record = {'schema_version':1,'scope':'ETHEREUM_SEPOLIA_PUBLIC',
                'plan_hash':approved,'tx_hash':captured['tx_hash'],
                'status':'ATTEMPT_COMMITTED','external_broadcasts':0,
                'captured_before_attempt':True,'attempted_at':int(time.time())}
            with self.db.transaction() as conn:
                reservation = conn.execute('SELECT tx_hash FROM external_anchor_nonces WHERE chain_id=? AND sender=? AND nonce=?',
                    (CHAIN_ID,plan['sender'].lower(),plan['transaction']['nonce'])).fetchone()
                if not reservation or reservation['tx_hash'] != captured['tx_hash']:
                    raise Rejected('ANCHOR_NONCE_RESERVED')
                conn.execute('INSERT INTO public_anchor_delivery VALUES(?,?,?)',
                    (approved,captured['tx_hash'],canonical(record)))
                if plan['kind']=='commit':
                    data=plan['transaction']['data'];checkpoint_hash=data[10:74];root=data[74:138]
                    body={'checkpoint_hash':checkpoint_hash,'root':root,'identifier':data[138:202],
                        'sequence':int(data[202:266],16),'version':1,'chain_id':CHAIN_ID,
                        'contract':plan['transaction']['to'],'publisher':plan['sender'],'input':data,
                        'unsigned_plan':plan,'raw_tx':raw}
                    # Original ExternalBlockchainAnchor/AnchorWorker can inspect
                    # this delivery without a keystore or a second transaction.
                    conn.execute('INSERT INTO external_anchor_submissions VALUES(?,?,?,?)',
                        (checkpoint_hash,root,captured['tx_hash'],canonical(body)))
            parent = self
            class TrackedRPC:
                def call(self, method, params):
                    if method == 'eth_sendRawTransaction':
                        if record['external_broadcasts'] != 0:
                            raise Rejected('ANCHOR_REPLAY_DENIED')
                        # Durable upper bound on attempted network sends: one.
                        # A crash between marker and socket write is uncertain.
                        record['external_broadcasts']=1
                        record['status']='SEND_IN_FLIGHT'
                        parent._save(record)
                        parent.rpc._approved_raw_transaction_hash = self._approved_raw_transaction_hash
                        try: return parent.rpc.call(method,params)
                        finally: del parent.rpc._approved_raw_transaction_hash
                    return parent.rpc.call(method,params)
            signed = SimpleNamespace(raw_transaction=HexBytes(raw),hash=HexBytes(captured['tx_hash']))
            try:
                returned = broadcast_approved(TrackedRPC(),plan,signed)
                if not isinstance(returned,str) or returned.lower()!=captured['tx_hash'].lower():
                    raise Rejected('ANCHOR_TX_HASH_MISMATCH')
                record['status']='SENT'
                self._save(record)
                return returned
            except Exception:
                record['status']='UNCERTAIN'
                self._save(record)
                raise


def verify_deployment(rpc, plan, tx_hash, confirmations=3):
    """Fresh public RPC evidence only; no signing, database or operator secrets."""
    from eth_utils import keccak, to_checksum_address
    import rlp
    checks = {}
    report = {'scope':'ETHEREUM_SEPOLIA_PUBLIC','tx_hash':tx_hash,'checks':checks,'verified':False}
    try:
        if type(confirmations) is not int or not 3 <= confirmations <= 64 or plan['kind'] != 'deploy':
            raise Rejected('ANCHOR_PUBLIC_CONFIG_INVALID')
        require_sepolia(rpc); checks['chain_id']=True
        p = plan['transaction']
        if p['chainId'] != CHAIN_ID or p['data'] != '0x'+load_anchor_artifact()['bin']:
            raise Rejected('ANCHOR_DEPLOYMENT_ARTIFACT_MISMATCH')
        tx = rpc.call('eth_getTransactionByHash',[tx_hash])
        receipt = rpc.call('eth_getTransactionReceipt',[tx_hash])
        if not tx or not receipt: raise Rejected('ANCHOR_NOT_MINED')
        checks['hash']=tx['hash'].lower()==receipt['transactionHash'].lower()==tx_hash.lower()
        checks['sender']=tx['from'].lower()==receipt['from'].lower()==plan['sender'].lower()
        checks['creation']=tx.get('to') is None and receipt.get('to') is None
        checks['input']=tx['input'].lower()==p['data'].lower()
        checks['exact_fields']=all(int(tx[k],16)==p[k] for k in
            ('chainId','type','nonce','value','gas','maxFeePerGas','maxPriorityFeePerGas'))
        checks['empty_access_list']=tx.get('accessList')==[]
        from eth_account import Account
        from eth_account.typed_transactions import TypedTransaction
        signed_fields={k:int(tx[k],16) for k in ('type','chainId','nonce','value','gas','maxFeePerGas','maxPriorityFeePerGas','v','r','s')}
        signed_fields.update(data=tx['input'],to=b'',accessList=tx.get('accessList',[]))
        encoded=HexBytes(TypedTransaction.from_dict(signed_fields).encode())
        checks['signature']=Account.recover_transaction(encoded).lower()==plan['sender'].lower()
        checks['signed_hash']='0x'+keccak(encoded).hex()==tx_hash.lower()
        checks['low_s']=0<signed_fields['s']<=0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0
        checks['success']=int(receipt['status'],16)==1
        address = to_checksum_address(keccak(rlp.encode([bytes.fromhex(plan['sender'][2:]),p['nonce']]))[-20:])
        checks['contract_address']=receipt['contractAddress'].lower()==address.lower()
        block = rpc.call('eth_getBlockByNumber',[receipt['blockNumber'],False])
        checks['canonical_block']=bool(block and block['hash'].lower()==receipt['blockHash'].lower()==tx['blockHash'].lower()
            and block['number']==receipt['blockNumber']==tx['blockNumber'] and tx_hash.lower() in [h.lower() for h in block['transactions']])
        depth=int(rpc.call('eth_blockNumber',[]),16)-int(receipt['blockNumber'],16)+1
        checks['confirmations']=depth>=confirmations
        runtime=expected_runtime(plan['sender'])
        code=rpc.call('eth_getCode',[address,receipt['blockNumber']])
        checks['runtime_artifact']=code.lower()==runtime.lower()
        checks['current_runtime']=rpc.call('eth_getCode',[address,'latest']).lower()==runtime.lower()
        publisher={'to':address,'data':'0x'+keccak(text='publisher()')[:4].hex()}
        checks['publisher']=rpc.call('eth_call',[publisher,'latest']).lower()=='0x'+'0'*24+plan['sender'][2:].lower()
        checks['gas_bound']=0<int(receipt['gasUsed'],16)<=p['gas']
        price=int(receipt['effectiveGasPrice'],16)
        checks['fee_bound']=0<=price<=p['maxFeePerGas']
        checks['stable_receipt']=digest(rpc.call('eth_getTransactionReceipt',[tx_hash]))==digest(receipt)
        checks['stable_block']=digest(rpc.call('eth_getBlockByNumber',[receipt['blockNumber'],False]))==digest(block)
        report.update(contract=address,code_hash='0x'+keccak(hexstr=runtime).hex(),publisher=plan['sender'],
            block_number=int(receipt['blockNumber'],16),block_hash=receipt['blockHash'],confirmations=depth,
            gas_used=int(receipt['gasUsed'],16),actual_cost_wei=int(receipt['gasUsed'],16)*price,
            receipt=receipt,receipt_commitment=digest(receipt),transaction=tx,
            plan_hash=plan_hash(plan),verified=all(checks.values()))
        report['reason']='VERIFIED' if report['verified'] else 'ANCHOR_EVIDENCE_MISMATCH'
    except Exception as exc:
        report['reason']=exc.code if isinstance(exc,Rejected) else 'ANCHOR_RPC_OR_EVIDENCE_INVALID'
    return report
