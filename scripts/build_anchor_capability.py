"""Build the independent M1 test artifacts; never a production MPC ceremony."""
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import requests

ROOT = Path(__file__).resolve().parents[1]
PTAU_HASH = '7e6a9c2e5f05179ddfc923f38f917c9e6831d16922a902b0b4758b8e79c2ab8a81bb5f29952e16ee6c5067ed044d7857b5de120a90704c1d3b637fd94b95b13e'
PTAU_URL = 'https://storage.googleapis.com/zkevm/ptau/powersOfTau28_hez_final_18.ptau'


def build(destination):
    destination = Path(destination).resolve()
    if not destination.is_relative_to(ROOT / 'data' / 'anchor-integration'):
        raise RuntimeError('Use the isolated ANCHOR artifact directory')
    destination.mkdir(parents=True, exist_ok=True)
    final = destination / 'anchor_capability_v1.zkey'
    if final.exists():
        raise RuntimeError('Existing proving key preserved; never overwrite pinned artifacts')
    node = shutil.which('node')
    cli = ROOT / 'crypto_trust/node_modules/snarkjs/cli.js'
    if not node or not cli.is_file():
        raise RuntimeError('Existing snarkjs runtime required')
    circuit = ROOT / 'crypto_trust/anchor_capability_v1.circom'
    r1cs = destination / 'anchor_capability_v1.r1cs'
    wasm = destination / 'anchor_capability_v1_js/anchor_capability_v1.wasm'
    if not r1cs.exists() or not wasm.exists():
        circom = shutil.which('circom')
        if not circom:
            raise RuntimeError('Install Circom 2.2.3 and add it to PATH first')
        subprocess.run([circom, str(circuit), '--r1cs', '--wasm', '--sym', '-o', str(destination)],
                       cwd=ROOT/'crypto_trust', check=True, timeout=600)
    def run(arguments, seconds=600):
        result = subprocess.run([node, str(cli), *arguments], capture_output=True,
                                text=True, encoding='utf-8', timeout=seconds)
        if result.returncode:
            raise RuntimeError('M1 artifact build failed: ' + arguments[0])
        # Public setup verification output only; never contribution entropy.
        if arguments[:2] in (['powersoftau', 'verify'], ['zkey', 'verify']):
            (destination / ('-'.join(arguments[:2]) + '.log')).write_text(result.stdout, encoding='utf-8')
    ptau = destination / 'public-phase1.ptau'
    if not ptau.exists():
        temporary = destination / 'public-phase1.download'
        with requests.get(PTAU_URL, stream=True, timeout=(15, 60)) as response:
            response.raise_for_status()
            with temporary.open('wb') as stream:
                for chunk in response.iter_content(1024*1024):
                    stream.write(chunk)
        temporary.rename(ptau)
    hasher = hashlib.blake2b()
    with ptau.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            hasher.update(chunk)
    if hasher.hexdigest() != PTAU_HASH:
        raise RuntimeError('Public Phase 1 digest mismatch')
    # Exact BLAKE2b pin is checked above against the public snarkjs ceremony
    # table. Full algebraic transcript verification is an independent expensive
    # preflight, not repeated for every development circuit build.
    initial = destination / 'initial.zkey'
    r1cs = destination / 'anchor_capability_v1.r1cs'
    run(['groth16', 'setup', str(r1cs), str(ptau), str(initial)])
    run(['zkey', 'contribute', str(initial), str(final), '--name=TRUSTA-ANCHOR-DEVELOPMENT-ONLY',
         '-e=' + secrets.token_hex(64)])
    run(['zkey', 'verify', str(r1cs), str(ptau), str(final)])
    run(['zkey', 'export', 'verificationkey', str(final), str(destination / 'verification_key.json')])
    run(['zkey', 'export', 'solidityverifier', str(final), str(destination / 'AnchorCapabilityVerifier.sol')])
    names = ['anchor_capability_v1.zkey', 'verification_key.json',
             'anchor_capability_v1_js/anchor_capability_v1.wasm', 'anchor_capability_v1.r1cs',
             'AnchorCapabilityVerifier.sol']
    manifest = {'version': 1, 'statement': 'TRUSTA-ANCHOR-M1-STATISTICS-V1',
                'scheme': 'Groth16', 'curve': 'bn128', 'public_input_count': 33,
                'phase1': 'PUBLIC_POWERS_OF_TAU', 'ptau_blake2b': PTAU_HASH,
                'phase1_authentication': 'OFFICIAL_SNARKJS_BLAKE2B_PIN',
                'setup': 'DEVELOPMENT_SINGLE_HOST_PHASE2', 'production_ready': False,
                'circuit_sha256': hashlib.sha256((ROOT/'crypto_trust/anchor_capability_v1.circom').read_bytes()).hexdigest(),
                'files': {n: hashlib.sha256((destination/n).read_bytes()).hexdigest() for n in names}}
    raw = (json.dumps(manifest, indent=2, sort_keys=True)+'\n').encode()
    (destination/'manifest.json').write_bytes(raw)
    print(json.dumps({'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'production_ready': False}))


if __name__ == '__main__':
    build(ROOT / 'data/anchor-integration/m1-artifacts')
