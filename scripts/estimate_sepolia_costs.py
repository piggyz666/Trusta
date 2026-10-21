"""Read-only RPC cost assessment. No real wallet signing or transaction broadcast.

The in-memory EVM uses disposable funded test accounts only, to obtain runtime
code and local gas measurements. It has no external network connection.
"""
import argparse
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.audit_trust.external_anchor import EthereumRPC
from backend.bank_sdk.transport import NoRedirect
from eth_abi import encode, decode
from eth_utils import keccak, is_address, to_checksum_address
from eth_tester import EthereumTester, PyEVMBackend


READ_ONLY = frozenset({
    'eth_chainId', 'eth_getBalance', 'eth_getBlockByNumber', 'eth_getCode',
    'eth_estimateGas', 'eth_call', 'eth_gasPrice', 'eth_maxPriorityFeePerGas',
    'eth_feeHistory', 'eth_getTransactionCount',
})


def load_local_environment():
    # Same .env syntax and environment precedence as Config.local(), without
    # initializing database directories, signing keys, agents or workers.
    path = ROOT / '.env'
    if path.exists():
        from backend.security.local_files import protect_secret
        protect_secret(path)
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())


class ReadOnlyRPC:
    def __init__(self, url):
        self.rpc = EthereumRPC(url, timeout=15)
        self.methods = []

    def call(self, method, params):
        if method not in READ_ONLY:
            raise ValueError('READ_ONLY_METHOD_REQUIRED')
        self.methods.append(method)
        return self.rpc.call(method, params)


def calldata():
    args = [keccak(text='TRUSTA estimate checkpoint'), keccak(text='TRUSTA estimate root'),
            keccak(text='TRUSTA estimate identifier'), 1, 1]
    return '0x' + (keccak(text='commit(bytes32,bytes32,bytes32,uint64,uint32)')[:4] +
                   encode(['bytes32', 'bytes32', 'bytes32', 'uint64', 'uint32'], args)).hex(), args


def local_measurements(artifact):
    evm = EthereumTester(backend=PyEVMBackend())
    owner = evm.get_accounts()[0]
    execute = evm.send_transaction
    creation = {'from': owner, 'data': '0x' + artifact['bin']}
    deploy_est = evm.estimate_gas(creation)
    receipt = evm.get_transaction_receipt(execute(creation | {'gas': deploy_est}))
    address = receipt['contract_address']
    data, args = calldata()
    tx = {'from': owner, 'to': address, 'data': data}
    commit_est = evm.estimate_gas(tx)
    commit = evm.get_transaction_receipt(execute(tx | {'gas': commit_est}))
    read = evm.call({'from': owner, 'to': address,
                     'data': '0x' + (keccak(text='read(bytes32)')[:4] + args[0]).hex()})
    if receipt['status'] != 1 or commit['status'] != 1 or len(commit['logs']) != 1:
        raise ValueError('LOCAL_SIMULATION_FAILED')
    if decode(['bytes32', 'bytes32', 'uint64', 'uint32'], bytes.fromhex(read[2:])) != tuple(args[1:]):
        raise ValueError('LOCAL_READBACK_FAILED')
    return {'scope': 'LOCAL_MEMORY_EVM_NOT_SEPOLIA',
            'vm': type(evm.backend.chain.get_vm()).__name__,
            'deploy_estimate_gas': deploy_est, 'deploy_local_gas_used': receipt['gas_used'],
            'commit_estimate_gas': commit_est, 'commit_local_gas_used': commit['gas_used'],
            'readback_equal': True}, evm.get_code(address)


def runtime_for_publisher(artifact, publisher):
    import solcx
    source = (ROOT / 'contracts/AuditAnchorContract.sol').read_text()
    output = solcx.compile_standard({
        'language': 'Solidity', 'sources': {'<stdin>': {'content': source}},
        'settings': {'outputSelection': {'*': {'*': [
            'evm.bytecode.object', 'evm.deployedBytecode.object',
            'evm.deployedBytecode.immutableReferences']}}},
    }, solc_version='0.8.24')['contracts']['<stdin>']['AuditAnchorContract']['evm']
    # Metadata may differ with output selection; compare executable initcode
    # excluding the standard Solidity CBOR trailer.
    def without_metadata(hexcode):
        value = bytes.fromhex(hexcode)
        length = int.from_bytes(value[-2:], 'big')
        return value[:-(length + 2)]
    if without_metadata(output['bytecode']['object']) != without_metadata(artifact['bin']):
        raise ValueError('ARTIFACT_SOURCE_EXECUTABLE_MISMATCH')
    deployed = output['deployedBytecode']
    runtime = bytearray.fromhex(deployed['object'])
    replacement = bytes.fromhex(publisher[2:]).rjust(32, b'\x00')
    patches = [r for refs in deployed['immutableReferences'].values() for r in refs]
    if not patches or any(r['length'] != 32 for r in patches):
        raise ValueError('UNEXPECTED_IMMUTABLE_LAYOUT')
    for ref in patches:
        runtime[ref['start']:ref['start'] + 32] = replacement
    return '0x' + runtime.hex()


def eth(wei):
    return str(Decimal(wei) / Decimal(10**18))


def cost_table(deploy_gas, commit_gas, base, tip, gas_price):
    total = deploy_gas + commit_gas
    margin_gas = math.ceil(deploy_gas * 1.2) + math.ceil(commit_gas * 1.2)
    cases = {}
    for name, price, gas in [
        ('protocol_base_fee_floor_no_inclusion_guarantee', base, total),
        ('current_eip1559_estimate', base + tip, total),
        ('suggested_reserve_20pct_gas_2x_base_plus_tip', 2 * base + tip, margin_gas),
        ('base_fee_rises_2x', 2 * base + tip, total),
        ('base_fee_rises_5x', 5 * base + tip, total),
        ('historical_legacy_scripts_estimated_spend', gas_price, total),
    ]:
        cases[name] = {'gas': gas, 'price_wei': price, 'cost_sepolia_eth': eth(gas * price)}
    # Gas LIMIT is used only here, explicitly for admission balance, not gas used.
    required = max(1000000, deploy_gas + 200000) * gas_price
    cases['historical_fixed_limit_scripts_upfront_balance_required'] = {
        'sepolia_eth': eth(required), 'basis': 'max(deploy limit, deploy estimate + commit limit) * legacy gasPrice'}
    budgets = {}
    for amount in ['0.0005', '0.001', '0.005', '0.05']:
        budget = int(Decimal(amount) * 10**18)
        budgets[amount] = {name: budget >= int(Decimal(c['cost_sepolia_eth']) * 10**18)
                           for name, c in cases.items() if 'cost_sepolia_eth' in c}
        budgets[amount]['historical_fixed_limit_scripts_upfront_balance_sufficient'] = budget >= required
        budgets[amount]['historical_fixed_limit_scripts_gas_price_break_even_gwei'] = str(Decimal(budget) / Decimal(max(1000000, deploy_gas + 200000)) / Decimal(10**9))
    return cases, budgets


def assess(offline=False, infura_sepolia=False):
    load_local_environment()
    artifact = json.loads((ROOT / 'contracts/AuditAnchorContract.json').read_text())
    report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'chain_id_expected': 11155111,
              'broadcasts': 0, 'external_deployments': 0, 'test_eth_spent': 0,
              'artifact_creation_sha256': hashlib.sha256(bytes.fromhex(artifact['bin'])).hexdigest(),
              'independent_acceptance_gas': 0,
              'acceptance_note': 'Read-only receipts/events/code/publisher/readback/confirmations via independent RPC; no on-chain verification transaction.'}
    url, wallet = os.getenv('ANCHOR_RPC_URL', ''), os.getenv('ANCHOR_WALLET_ADDRESS', '')
    report['endpoint_selection'] = 'configured_url'
    if infura_sepolia:
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.hostname != 'mainnet.infura.io' or not parts.path.startswith('/v3/') or parts.query or parts.username:
            raise ValueError('INFURA_MAINNET_URL_REQUIRED_FOR_TEMPORARY_SEPOLIA_SELECTION')
        url = urlunsplit((parts.scheme, 'sepolia.infura.io', parts.path, '', ''))
        report['endpoint_selection'] = 'temporary_infura_sepolia_same_key_no_config_edit'
    report['configuration_presence'] = {'rpc': bool(url), 'wallet_public_address': bool(wallet),
                                        'independent_rpc': bool(os.getenv('ANCHOR_VERIFY_RPC_URL'))}
    local, runtime = local_measurements(artifact)
    report['local_simulation'] = local
    if offline or not url or not is_address(wallet):
        report.update(status='LOCAL_ONLY_WAITING_FOR_RPC_AND_PUBLIC_WALLET', current_eip1559=None)
        total = local['deploy_local_gas_used'] + local['commit_local_gas_used']
        report['hypothetical_price_sensitivity_not_current_network'] = {
            str(gwei): eth(total * gwei * 10**9) for gwei in [1, 2, 5, 10, 20, 50, 100]}
        return report
    rpc = None
    try:
        rpc = ReadOnlyRPC(url)
        chain_id = int(rpc.call('eth_chainId', []), 16)
        report['chain_id_observed'] = chain_id
        if chain_id != 11155111:
            raise ValueError('CHAIN_MISMATCH')
        report['chain_id_verified'] = True
        wallet = to_checksum_address(wallet)
        head = rpc.call('eth_getBlockByNumber', ['latest', False])
        block = head['number']
        base = int(head['baseFeePerGas'], 16)
        balance = int(rpc.call('eth_getBalance', [wallet, block]), 16)
        price = int(rpc.call('eth_gasPrice', []), 16)
        history = rpc.call('eth_feeHistory', ['0x5', block, [20, 50, 90]])
        tips = sorted(int(row[1], 16) for row in history.get('reward', []) if len(row) >= 2)
        provider_tip = int(rpc.call('eth_maxPriorityFeePerGas', []), 16)
        tip = provider_tip
        if tips:
            tip = max(tip, tips[len(tips) // 2])
        report.update(block_number=int(block, 16), block_hash=head['hash'], wallet_public_address=wallet,
                      wallet_balance_sepolia_eth=eth(balance),
                      current_eip1559={'base_fee_wei': base, 'selected_tip_wei': tip,
                                       'provider_suggested_tip_wei': provider_tip,
                                       'legacy_gas_price_wei': price, 'suggested_max_fee_wei': 2 * base + tip,
                                       'fee_history': history})
        report['snapshot_block_timestamp_utc'] = datetime.fromtimestamp(int(head['timestamp'], 16), timezone.utc).isoformat()
        # Deployment estimation first uses actual balance/state without overrides.
        creation = {'from': wallet, 'data': '0x' + artifact['bin'], 'value': '0x0'}
        try:
            deploy = int(rpc.call('eth_estimateGas', [creation, block]), 16)
            report['deploy_estimation_scope'] = 'SEPOLIA_RPC_ACTUAL_STATE'
        except Exception:
            deploy = int(rpc.call('eth_estimateGas', [creation, block, {wallet: {'balance': hex(10**24)}}]), 16)
            report['deploy_estimation_scope'] = 'SEPOLIA_RPC_SYNTHETIC_BALANCE_ONLY'
        report['deploy_rpc_estimate_gas'] = deploy
        try:
            runtime = runtime_for_publisher(artifact, wallet)
            target = to_checksum_address('0x' + keccak(text='TRUSTA estimate-only contract')[-20:].hex())
            if rpc.call('eth_getCode', [target, block]) not in ['0x', '0x0']:
                raise ValueError('SIMULATION_TARGET_NOT_EMPTY')
            overrides = {target: {'code': runtime, 'state': {}}, wallet: {'balance': hex(10**24)}}
            observed_publisher = rpc.call('eth_call', [{'to': target, 'data': '0x' + keccak(text='publisher()')[:4].hex()}, block, overrides])
            if to_checksum_address('0x' + observed_publisher[-40:]) != wallet:
                raise ValueError('SIMULATION_PUBLISHER_MISMATCH')
            commit_tx = {'from': wallet, 'to': target, 'value': '0x0', 'data': calldata()[0]}
            commit = int(rpc.call('eth_estimateGas', [commit_tx, block, overrides]), 16)
            report['commit_estimation_scope'] = 'SEPOLIA_RPC_STATE_OVERRIDE_FRESH_CONTRACT'
            report['commit_rpc_estimate_gas'] = commit
        except Exception as exc:
            commit = local['commit_estimate_gas']
            report['commit_estimation_scope'] = 'LOCAL_EVM_FALLBACK_NOT_LIVE_RPC_ESTIMATE'
            report['commit_estimation_failure_type'] = type(exc).__name__
        report['costs'], report['hypothetical_balances'] = cost_table(deploy, commit, base, tip, price)
        needed = Decimal(report['costs']['historical_fixed_limit_scripts_upfront_balance_required']['sepolia_eth'])
        report['actual_wallet_can_fund_historical_fixed_limit_scripts_at_snapshot'] = Decimal(eth(balance)) >= needed
        report['historical_fixed_limit_scripts_price_guard_allows_snapshot'] = price <= 100 * 10**9
        report['historical_fixed_limit_scripts_gas_limits_sufficient'] = {'deploy': deploy <= 1000000, 'commit': commit <= 200000}
        report['historical_fixed_limit_scripts_ready_to_execute'] = (
            report['actual_wallet_can_fund_historical_fixed_limit_scripts_at_snapshot'] and
            report['historical_fixed_limit_scripts_price_guard_allows_snapshot'] and
            all(report['historical_fixed_limit_scripts_gas_limits_sufficient'].values()) and
            'commit_rpc_estimate_gas' in report)
        provider_price = base + provider_tip
        margin_gas = math.ceil(deploy * 1.2) + math.ceil(commit * 1.2)
        report['provider_tip_budget_scenario'] = {
            'effective_price_wei': provider_price,
            'estimated_deploy_cost': eth(deploy * provider_price),
            'estimated_commit_cost': eth(commit * provider_price),
            'estimated_total_cost': eth((deploy + commit) * provider_price),
            'reserve_20pct_gas_2x_base_plus_provider_tip': eth(margin_gas * (2 * base + provider_tip))}
        report['effective_price_rise_scenarios'] = {
            str(g): {'estimated_cost_sepolia_eth': eth((deploy + commit) * g * 10**9),
                     'reserve_20pct_gas_sepolia_eth': eth(margin_gas * g * 10**9)} for g in [1, 2, 5, 10]}
        report['status'] = 'RPC_ESTIMATED' if 'commit_rpc_estimate_gas' in report else 'PARTIAL_RPC_ESTIMATED_COMMIT_LOCAL_ONLY'
    except Exception as exc:
        # Never emit transport exceptions, provider response strings or secret URL.
        report.update(status='RPC_ESTIMATION_FAILED', failure_type=type(exc).__name__)
    report['rpc_methods_attempted'] = rpc.methods if rpc else []
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--infura-sepolia', action='store_true',
                        help='Temporarily select Sepolia with a configured Infura mainnet key; never edit .env.')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = assess(args.offline, args.infura_sepolia)
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
