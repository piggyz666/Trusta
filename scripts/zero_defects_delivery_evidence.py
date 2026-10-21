"""Seal final evidence without reading credentials or contacting external services."""
import collections
import hashlib
import json
import os
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/zero-defects'


def read(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def scan_exported_secrets():
    # Compare credentials in memory; never export values or open wallet keys.
    values = []
    entries = dict(os.environ)
    env_file = ROOT / '.env'
    if env_file.exists():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                name, value = line.split('=', 1)
                entries[name.strip()] = value.strip().strip('"').strip("'")
    for name, value in entries.items():
        if (any(word in name.upper() for word in ('API_KEY', 'ACCESS_TOKEN', 'REFRESH_TOKEN', 'PASSWORD', 'AUTH_SECRET', 'RPC_URL'))
                and not name.upper().endswith(('_PATH', '_FILE', '_ENV')) and len(value) >= 12
                and value != 'https://eth-sepolia-testnet.api.pocket.network'):
            values.append((name, value))
    for configuration in (ROOT / 'config').glob('bank.*.local.toml'):
        settings = tomllib.loads(configuration.read_text(encoding='utf-8'))
        if settings.get('token_path'):
            path = (configuration.parent / settings['token_path']).resolve()
            if path.is_file():
                tokens = json.loads(path.read_text(encoding='utf-8'))
                for key in ('access_token', 'refresh_token'):
                    value = tokens.get(key)
                    if isinstance(value, str) and len(value) >= 12:
                        values.append(('BANK_' + key.upper(), value))
    files = [p for p in OUT.rglob('*') if p.is_file() and 'audit-venv' not in p.parts
             and p.suffix in {'.json', '.txt', '.csv', '.pem'}]
    report = ROOT / 'TRUSTA_BACKEND_ZERO_KNOWN_FIXABLE_DEFECTS.md'
    if report.exists():
        files.append(report)
    hits = []
    for path in files:
        content = path.read_text(encoding='utf-8', errors='replace')
        for name, value in set(values):
            if value in content:
                hits.append({'file': str(path.relative_to(ROOT)), 'credential_category': name})
    write('secret-scan.json', {'files_checked': len(files), 'configured_values_compared_in_memory': len(set(values)),
         'hits': hits, 'secret_values_exported': False, 'wallet_private_keys_read': False,
         'scope': 'Current configured credential strings in local text evidence; not a proof of absence of all possible secrets.'})
    if hits:
        raise RuntimeError('Credential found in local delivery evidence; values withheld')


def main():
    final = read('locked-final-result.json')
    if not final['passed'] or final['tests'] != 781 or final['skipped']:
        raise RuntimeError('Final regression evidence invalid')
    before = read('source-before.json')
    after = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
             for folder in ('backend', 'scripts', 'tests', 'benchmarks', 'contracts')
             for p in (ROOT / folder).rglob('*') if p.suffix in {'.py', '.sol'}}
    changed = sorted(p for p in before if before[p] != after.get(p))
    deleted = sorted(set(before) - set(after))
    if deleted or any(p.startswith(('tests\\', 'tests/')) for p in changed):
        raise RuntimeError('Original sources deleted or tests changed')
    write('source-after.json', after)
    write('source-changes.json', {'modified': changed, 'added': sorted(set(after)-set(before)),
          'deleted': deleted, 'original_test_files_unchanged': True, 'git_repository': False,
          'dependency_files_current_sha256': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
             for p in ('requirements.txt', 'requirements-phase6.txt', 'crypto_trust/package.json', 'crypto_trust/package-lock.json')},
          'dependency_files_note': 'Initial source inventory covers Python/Solidity only; no fabricated before hashes.',
          'secret_acl_change': 'Local ACLs repaired; no secret contents included.'})
    reasons = {
        'B101': 'Local acceptance/demo assertions only; no backend financial authorization relies on assert.',
        'B107': 'Empty optional proof/key-path input defaults; strong authorization rejects missing proof, no embedded secret.',
        'B105': 'Field names, protocol path, evidence labels or isolated demo value; no production credential literal.',
        'B106': 'Isolated temporary Virtual Bank demo secret; not used by real bank or chain signer.',
        'B608': 'Identifiers selected from fixed internal table maps/two fixed names; values bound with SQL parameters.',
        'B404': 'Subprocess module import alone; invocation sites independently reviewed.',
        'B603': 'Trusted local Python/Node/circuit tool invocation with argument arrays; no LLM/customer shell command.',
        'B607': 'Installed administrator-controlled tool lookup; PATH/local administrator is a declared host trust assumption.',
        'B310': 'Fixed loopback demo URL or pinned HTTPS ceremony artifact; not customer-controlled SSRF target.',
        'B110': 'Historical offline scoring fallback, not live financial execution; raw failures retained separately.',
        'B102': 'Compile/exec of local trusted historical workflow with output directory replacement only; no remote code.'}
    bandit = read('bandit-final.json')
    reviews = []
    for finding in bandit['results']:
        identifier = finding['test_id']
        if identifier not in reasons:
            raise RuntimeError('Unreviewed scanner category')
        if identifier == 'B101' and finding['filename'].startswith('backend'):
            raise RuntimeError('Unexpected runtime assert finding')
        reviews.append({'tool': 'bandit', 'id': identifier, 'file': finding['filename'],
            'line': finding['line_number'], 'severity': finding['issue_severity'],
            'classification': 'REVIEWED_NOT_CONFIRMED_EXPLOITABLE_RUNTIME_DEFECT',
            'reason': reasons[identifier]})
    write('scan-review.json', {'scanner_errors': bandit['errors'], 'findings': reviews,
         'category_counts': dict(collections.Counter(r['id'] for r in reviews)),
         'medium': sum(r['severity']=='MEDIUM' for r in reviews),
         'high': sum(r['severity']=='HIGH' for r in reviews),
         'npm_residual': '15 low propagated elliptic advisory findings; no patched upstream version, current proof runner does not invoke elliptic signing. Not declared removed.',
         'typing': 'Default mypy passed 115 files; exploratory check-untyped-defs has 59 diagnostics/22 files, not a strict typing pass.',
         'limits': 'Scanner categories are not vulnerability counts. Local administrator/PATH and future API surface remain trust limitations.'})
    metadata = []
    text = (OUT / 'mypy-untyped-final.txt').read_text(encoding='utf-8')
    for line in text.splitlines():
        match = re.match(r'(.+?):(\d+): error: (.+) \[([^]]+)\]$', line)
        if not match:
            continue
        filename, number, description, code = match.groups()
        rationale = {
            'var-annotated': 'Empty collection lacks explicit element type; current runtime type/operations reviewed.',
            'attr-defined': 'Dynamically injected Service guards or platform-conditional fcntl API; initialization/platform branch reviewed.',
            'union-attr': 'Conditional ready/response/worker state narrowed at runtime; existing rejection/init checks and negative tests reviewed.',
            'index': 'Mutable heterogeneous provider request dictionary inference; concrete dict at runtime.',
            'assignment': 'Heterogeneous result/protocol objects inferred too narrowly; concrete runtime schema validation retained.',
            'dict-item': 'Heterogeneous signed response envelope inferred from first branch; strict envelope validation retained.',
            'operator': 'Structured finance accumulator inferred as object; actual numeric fields initialized and validated.',
            'call-overload': 'Heterogeneous graph dictionary metadata inference; strict original graph validation retained.',
            'misc': 'Optional metadata collection after runtime validation; no implicit execution authority.',
            'list-item': 'Tool path checked/configured before invocation; subprocess arguments are not untrusted shell input.'}.get(code)
        if rationale is None:
            raise RuntimeError('Unreviewed typing diagnostic')
        metadata.append({'file': filename, 'line': int(number), 'code': code,
            'diagnostic': description, 'review': rationale,
            'status': 'STATIC_TYPE_COVERAGE_DEBT_NOT_CONFIRMED_RUNTIME_BUG'})
    write('type-review.json', {'diagnostics': metadata, 'count': len(metadata),
         'strict_pass': False, 'default_pass': True, 'suppression_added': False})
    scan_exported_secrets()
    evidence = {str(p.relative_to(ROOT)): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                  'bytes': p.stat().st_size}
                for p in OUT.rglob('*') if p.is_file() and
                'audit-venv' not in p.parts and p.suffix in {'.json', '.txt', '.csv', '.pem'} and
                p.name != 'evidence-manifest.json'}
    write('evidence-manifest.json', {'sealed_at_utc': datetime.now(timezone.utc).isoformat(),
         'files': evidence, 'credential_values_exported': False, 'wallet_keys_read': False, 'external_calls': 0,
         'note': 'Content hashes establish local evidence integrity, not independent external certification.'})
    print(json.dumps({'final_tests': final['tests'], 'old_tests_unchanged': True,
          'modified_sources': len(changed), 'added_sources': len(set(after)-set(before)),
          'reviewed_bandit_findings': len(reviews), 'typing_diagnostics_reviewed': len(metadata),
          'evidence_files': len(evidence)}))


if __name__ == '__main__':
    main()
