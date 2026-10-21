"""Build local development Groth16 assets; no bank connection and no production ceremony claim."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
PTAU_URL='https://circom.info/powersOfTau28_hez_final_11.ptau'
# BLAKE2b-512 published in the primary snarkjs ceremony table.
PTAU_HASH='47c282116b892e5ac92ca238578006e31a47e7c7e70f0baa8b687f0a5203e28ea07bbbec765a98dcd654bad618475d4661bfaec3bd9ad2ed12e7abc251d94d33'


def run(command,cwd):
    result=subprocess.run(command,cwd=cwd,capture_output=True,text=True,timeout=180)
    if result.returncode: raise RuntimeError('Proof asset build step failed: '+command[1])


def build(destination,local_phase1=False):
    destination=Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()): raise RuntimeError('Use a new empty artifact directory; existing pinned assets are never overwritten.')
    destination.mkdir(parents=True,exist_ok=True)
    crypto=ROOT/'crypto_trust'
    cli=crypto/'node_modules'/'snarkjs'/'cli.js'
    node=shutil.which('node');circom=shutil.which('circom')
    if not node or not circom or not cli.exists(): raise RuntimeError('Install Node, Circom 2.2.3 and crypto_trust dependencies first.')
    ptau=destination/'ceremony.ptau'
    snark=[node,str(cli)]
    if local_phase1:
        # Explicit offline development fallback. Both phases have a single host;
        # this is NOT a public multi-party ceremony or production trust root.
        phase1=destination/'phase1.ptau';contributed=destination/'phase1_contributed.ptau'
        run(snark+['powersoftau','new','bn128','11',str(phase1)],crypto)
        run(snark+['powersoftau','contribute',str(phase1),str(contributed),'--name=TRUSTA-development-only','-e='+secrets.token_hex(64)],crypto)
        run(snark+['powersoftau','prepare','phase2',str(contributed),str(ptau)],crypto)
    else:
        with urllib.request.urlopen(PTAU_URL,timeout=60) as response,ptau.open('xb') as stream:
            shutil.copyfileobj(response,stream)
        if hashlib.blake2b(ptau.read_bytes()).hexdigest()!=PTAU_HASH: raise RuntimeError('Public ceremony digest mismatch.')
    run([circom,str(crypto/'banking_eligibility.circom'),'--r1cs','--wasm','--sym','-o',str(destination)],crypto)
    run(snark+['powersoftau','verify',str(ptau)],crypto)
    initial=destination/'initial.zkey';final=destination/'banking_eligibility.zkey'
    run(snark+['groth16','setup',str(destination/'banking_eligibility.r1cs'),str(ptau),str(initial)],crypto)
    # OS entropy is never printed or persisted. This is a single-host development
    # contribution, not a claim of independent circuit-specific MPC auditing.
    run(snark+['zkey','contribute',str(initial),str(final),'--name=TRUSTA-local-development','-e='+secrets.token_hex(64)],crypto)
    run(snark+['zkey','verify',str(destination/'banking_eligibility.r1cs'),str(ptau),str(final)],crypto)
    run(snark+['zkey','export','verificationkey',str(final),str(destination/'verification_key.json')],crypto)
    files=['banking_eligibility_js/banking_eligibility.wasm','banking_eligibility.zkey','verification_key.json']
    manifest={'schema_version':1,'scheme':'Groth16','curve':'bn128','statement':'BANKING_ELIGIBILITY_V1',
              'setup':'DEVELOPMENT_SINGLE_HOST_CONTRIBUTION','production_ready':False,
              'circuit_sha256':hashlib.sha256((crypto/'banking_eligibility.circom').read_bytes()).hexdigest(),
              'phase1':'LOCAL_SINGLE_HOST' if local_phase1 else 'PUBLIC_POWERS_OF_TAU',
              'ptau_blake2b':hashlib.blake2b(ptau.read_bytes()).hexdigest(),'files':{f:hashlib.sha256((destination/f).read_bytes()).hexdigest() for f in files}}
    raw=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
    (destination/'manifest.json').write_bytes(raw)
    return {'artifacts':str(destination),'manifest_sha256':hashlib.sha256(raw).hexdigest(),'setup':manifest['setup'],'production_ready':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(ROOT/'data'/'zkp-dev-v1'))
    parser.add_argument('--local-phase1',action='store_true',help='Single-host development setup; never production-ready.')
    args=parser.parse_args();print(json.dumps(build(args.output,args.local_phase1)))
