"""Sepolia-only unsigned plans and bounded local signing, outside bank execution."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import time
from backend.domain import Rejected, canonical

CHAIN_ID = 11155111
CREATION_SHA256 = 'c7c2ab864bed3a2cb49a72e6ca702adc0d356899849444f2d6d39f573d63da17'
SOURCE_SHA256 = 'c18ab76d2f695f6c119aa6ad78ee574067e4524baef8cf2c166b18e3f0b580dc'


def load_anchor_artifact():
    root = Path(__file__).resolve().parents[2]
    artifact = json.loads((root / 'contracts/AuditAnchorContract.json').read_text())
    if (hashlib.sha256(bytes.fromhex(artifact['bin'])).hexdigest() != CREATION_SHA256
            or hashlib.sha256((root / 'contracts/AuditAnchorContract.sol').read_bytes()).hexdigest() != SOURCE_SHA256):
        raise Rejected('ANCHOR_ARTIFACT_CHANGED')
    return artifact


def require_sepolia(rpc):
    try:
        chain = int(rpc.call('eth_chainId', []), 16)
    except Exception:
        raise Rejected('ANCHOR_CHAIN_UNVERIFIED') from None
    if chain != CHAIN_ID:
        raise Rejected('ANCHOR_CHAIN_MISMATCH')


@dataclass(frozen=True)
class AnchorFeePolicy:
    margin_percent: int = 20
    deploy_max_gas: int = 5_000_000
    commit_max_gas: int = 600_000
    max_priority_fee: int = 100_000_000  # 0.1 gwei; no automatic bidding loop.
    minimum_priority_fee: int = 1_000_000  # 0.001 gwei bounded floor, not a mining guarantee.
    max_fee_per_gas: int = 2_000_000_000
    deploy_max_cost: int = 8_500_000_000_000_000  # 0.0085 ETH
    commit_max_cost: int = 1_500_000_000_000_000  # 0.0015 ETH
    minimum_balance: int = 200_000_000_000_000  # Leave >=0.0002 ETH.
    quote_ttl: int = 120


def plan_hash(plan):
    return hashlib.sha256(canonical(plan).encode()).hexdigest()


def prepare_transaction(rpc, sender, data, kind, to=None, policy=None):
    from eth_utils import is_address, to_checksum_address
    policy = policy or AnchorFeePolicy()
    require_sepolia(rpc)  # Before any estimation, nonce, wallet or signing operation.
    if kind not in {'deploy', 'commit'} or (kind == 'deploy') != (to is None):
        raise Rejected('ANCHOR_TRANSACTION_KIND_INVALID')
    if not is_address(sender) or to is not None and not is_address(to):
        raise Rejected('ANCHOR_PUBLIC_ADDRESS_INVALID')
    if not isinstance(data, str) or not data.startswith('0x') or len(data) <= 2:
        raise Rejected('ANCHOR_CALLDATA_INVALID')
    try:
        bytes.fromhex(data[2:])
    except ValueError:
        raise Rejected('ANCHOR_CALLDATA_INVALID') from None
    sender = to_checksum_address(sender)
    if kind == 'deploy':
        if data != '0x' + load_anchor_artifact()['bin']:
            raise Rejected('ANCHOR_DEPLOYMENT_ARTIFACT_MISMATCH')
    else:
        from eth_utils import keccak
        if (len(data) != 330 or data[2:10] != keccak(text='commit(bytes32,bytes32,bytes32,uint64,uint32)')[:4].hex()
                or int(data[10:74], 16) == 0 or int(data[74:138], 16) == 0
                or int(data[202:266], 16) >= 2**64 or int(data[266:], 16) != 1):
            raise Rejected('ANCHOR_COMMIT_CALLDATA_INVALID')
    head = rpc.call('eth_getBlockByNumber', ['latest', False])
    base = int(head['baseFeePerGas'], 16)
    suggested_tip = int(rpc.call('eth_maxPriorityFeePerGas', []), 16)
    if not 0 <= suggested_tip <= policy.max_priority_fee or base < 0:
        raise Rejected('ANCHOR_PRIORITY_FEE_LIMIT')
    suggested_tip = max(suggested_tip, policy.minimum_priority_fee)
    max_fee = 2 * base + suggested_tip
    if not 0 < max_fee <= policy.max_fee_per_gas:
        raise Rejected('ANCHOR_FEE_LIMIT')
    tx = {'from': sender, 'value': '0x0', 'data': data,
          'maxFeePerGas': hex(max_fee), 'maxPriorityFeePerGas': hex(suggested_tip)}
    if to:
        tx['to'] = to_checksum_address(to)
        code = rpc.call('eth_getCode', [tx['to'], head['number']])
        if code in {'0x', '0x0'}:
            raise Rejected('ANCHOR_CONTRACT_NOT_DEPLOYED')
        from eth_utils import keccak
        pinned = os.getenv('ANCHOR_CONTRACT_CODE_HASH', '')
        if not pinned or ('0x' + keccak(hexstr=code).hex()).lower() != pinned.lower():
            raise Rejected('ANCHOR_CONTRACT_CODE_MISMATCH')
        publisher = rpc.call('eth_call', [{'to': tx['to'], 'data': '0x' + keccak(text='publisher()')[:4].hex()}, head['number']])
        if publisher.lower() != '0x' + '0' * 24 + sender[2:].lower():
            raise Rejected('ANCHOR_PUBLISHER_MISMATCH')
    estimated = int(rpc.call('eth_estimateGas', [tx, head['number']]), 16)
    gas = (estimated * (100 + policy.margin_percent) + 99) // 100
    gas_limit = policy.deploy_max_gas if kind == 'deploy' else policy.commit_max_gas
    cost_limit = policy.deploy_max_cost if kind == 'deploy' else policy.commit_max_cost
    if not 21000 <= estimated <= gas <= gas_limit:
        raise Rejected('ANCHOR_GAS_LIMIT')
    worst_cost = gas * max_fee
    if worst_cost > cost_limit:
        raise Rejected('ANCHOR_TOTAL_FEE_LIMIT')
    balance = int(rpc.call('eth_getBalance', [sender, 'pending']), 16)
    if balance < worst_cost + policy.minimum_balance:
        raise Rejected('ANCHOR_BALANCE_RESERVE')
    nonce = int(rpc.call('eth_getTransactionCount', [sender, 'pending']), 16)
    unsigned = {'type': 2, 'chainId': CHAIN_ID, 'nonce': nonce, 'value': 0,
                'data': data, 'gas': gas, 'maxFeePerGas': max_fee,
                'maxPriorityFeePerGas': suggested_tip}
    if to:
        unsigned['to'] = tx['to']
    return {'schema_version': 1, 'kind': kind, 'sender': sender, 'transaction': unsigned,
            'estimated_gas': estimated, 'worst_cost_wei': worst_cost,
            'expected_cost_wei': estimated * (base + suggested_tip),
            'balance_wei': balance, 'minimum_remaining_wei': policy.minimum_balance,
            'quoted_at': int(time.time()), 'quote_ttl': policy.quote_ttl,
            'block_number': head['number'], 'block_hash': head['hash']}


def signing_ready():
    # Presence only: no decryption or private-key reads during readiness checks.
    return {'encrypted_keystore_configured': bool(os.getenv('ANCHOR_KEYSTORE_PATH')),
            'keystore_password_environment_present': bool(os.getenv('ANCHOR_KEYSTORE_PASSWORD')),
            'legacy_private_key_environment_present': bool(os.getenv('ANCHOR_WALLET_PRIVATE_KEY')),
            'signing_enabled': os.getenv('ANCHOR_ALLOW_SIGNING') == 'true',
            'broadcast_enabled': os.getenv('ANCHOR_ALLOW_BROADCAST') == 'true'}


def sign_approved_plan(rpc, plan, wallet_env='ANCHOR_WALLET_PRIVATE_KEY'):
    from eth_account import Account
    require_sepolia(rpc)
    # Exact immutable quote fingerprint, not a global permission to spend.
    if os.getenv('ANCHOR_ALLOW_SIGNING') != 'true' or os.getenv('ANCHOR_APPROVED_PLAN_HASH') != plan_hash(plan):
        raise Rejected('ANCHOR_TRANSACTION_APPROVAL_REQUIRED')
    tx = plan['transaction']
    allowed={'type','chainId','nonce','value','data','gas','maxFeePerGas','maxPriorityFeePerGas','to'}
    required=allowed-{'to'}
    if (not isinstance(tx,dict) or set(tx)-allowed or not required<=set(tx)
            or any(type(tx[k]) is not int or tx[k]<0 for k in ('type','chainId','nonce','value','gas','maxFeePerGas','maxPriorityFeePerGas'))):
        raise Rejected('ANCHOR_TRANSACTION_BINDING_INVALID')
    if type(tx.get('chainId')) is not int or tx['chainId'] != CHAIN_ID or tx.get('type') != 2 or tx.get('value') != 0:
        raise Rejected('ANCHOR_TRANSACTION_BINDING_INVALID')
    if (type(plan.get('quoted_at')) is not int or type(plan.get('quote_ttl')) is not int
            or not 0 <= time.time() - plan['quoted_at'] <= plan['quote_ttl'] <= 120):
        raise Rejected('ANCHOR_QUOTE_EXPIRED')
    # Re-estimation must pass again. Sender, nonce, calldata and fee/gas bounds
    # from the approved quote may not silently change before signing.
    fresh = prepare_transaction(rpc, plan['sender'], tx['data'], plan['kind'], tx.get('to'))
    if fresh['transaction']['nonce'] != tx['nonce'] or fresh['transaction']['gas'] > tx['gas'] or fresh['transaction']['maxFeePerGas'] > tx['maxFeePerGas']:
        raise Rejected('ANCHOR_QUOTE_CHANGED')
    policy = AnchorFeePolicy()
    max_gas = policy.deploy_max_gas if plan['kind'] == 'deploy' else policy.commit_max_gas
    max_cost = policy.deploy_max_cost if plan['kind'] == 'deploy' else policy.commit_max_cost
    if (not 21000 <= tx['gas'] <= max_gas or not 0 <= tx['maxPriorityFeePerGas'] <= policy.max_priority_fee
            or not tx['maxPriorityFeePerGas'] <= tx['maxFeePerGas'] <= policy.max_fee_per_gas
            or tx['gas'] * tx['maxFeePerGas'] > max_cost
            or fresh['balance_wei'] < tx['gas'] * tx['maxFeePerGas'] + policy.minimum_balance):
        raise Rejected('ANCHOR_APPROVED_BUDGET_INVALID')
    keyfile = os.getenv('ANCHOR_KEYSTORE_PATH', '')
    try:
        if keyfile:
            from backend.security.local_files import protect_secret
            protect_secret(keyfile)
            password = os.getenv('ANCHOR_KEYSTORE_PASSWORD', '')
            if not password:
                raise Rejected('BLOCKED_BY_EXTERNAL_CREDENTIAL')
            wallet = Account.from_key(Account.decrypt(json.loads(Path(keyfile).read_text()), password))
        else:
            secret = os.getenv(wallet_env, '')
            if not secret:
                raise Rejected('BLOCKED_BY_EXTERNAL_CREDENTIAL')
            wallet = Account.from_key(secret)  # Legacy operator-only compatibility.
    except Rejected:
        raise
    except Exception:
        raise Rejected('ANCHOR_LOCAL_SIGNER_UNAVAILABLE') from None
    if wallet.address.lower() != plan['sender'].lower():
        raise Rejected('ANCHOR_SIGNER_MISMATCH')
    require_sepolia(rpc)
    return wallet.sign_transaction(tx)


def broadcast_approved(rpc, plan, signed):
    from eth_account import Account
    require_sepolia(rpc)
    if os.getenv('ANCHOR_ALLOW_BROADCAST') != 'true' or os.getenv('ANCHOR_ALLOW_SIGNING') != 'true' or os.getenv('ANCHOR_APPROVED_PLAN_HASH') != plan_hash(plan):
        raise Rejected('ANCHOR_BROADCAST_APPROVAL_REQUIRED')
    if not 0 <= time.time() - plan['quoted_at'] <= plan['quote_ttl'] <= 120:
        raise Rejected('ANCHOR_QUOTE_EXPIRED')
    if Account.recover_transaction(signed.raw_transaction).lower() != plan['sender'].lower():
        raise Rejected('ANCHOR_SIGNER_MISMATCH')
    fresh = prepare_transaction(rpc, plan['sender'], plan['transaction']['data'], plan['kind'], plan['transaction'].get('to'))
    if (fresh['transaction']['nonce'] != plan['transaction']['nonce']
            or fresh['transaction']['gas'] > plan['transaction']['gas']
            or fresh['transaction']['maxFeePerGas'] > plan['transaction']['maxFeePerGas']):
        raise Rejected('ANCHOR_QUOTE_CHANGED')
    if fresh['balance_wei'] < plan['transaction']['gas'] * plan['transaction']['maxFeePerGas'] + AnchorFeePolicy().minimum_balance:
        raise Rejected('ANCHOR_BALANCE_RESERVE')
    # Bind raw signed bytes to this exact unsigned transaction (not just signer).
    from eth_account.typed_transactions import TypedTransaction
    from hexbytes import HexBytes
    decoded = TypedTransaction.from_bytes(HexBytes(signed.raw_transaction)).as_dict()
    if decoded.get('type')!=2 or decoded.get('accessList') not in ([],()):
        raise Rejected('ANCHOR_SIGNED_TRANSACTION_MISMATCH')
    tx = plan['transaction']
    for name in ('chainId', 'nonce', 'value', 'gas', 'maxFeePerGas', 'maxPriorityFeePerGas'):
        if decoded.get(name) != tx[name]:
            raise Rejected('ANCHOR_SIGNED_TRANSACTION_MISMATCH')
    if bytes(decoded['data']) != bytes.fromhex(tx['data'][2:]) or bytes(decoded.get('to', b'')) != bytes.fromhex(tx.get('to', '0x')[2:]):
        raise Rejected('ANCHOR_SIGNED_TRANSACTION_MISMATCH')
    rpc._approved_raw_transaction_hash = signed.hash.hex().removeprefix('0x')
    try:
        return rpc.call('eth_sendRawTransaction', ['0x' + signed.raw_transaction.hex().removeprefix('0x')])
    finally:
        del rpc._approved_raw_transaction_hash
