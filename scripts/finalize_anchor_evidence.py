"""Export existing runtime evidence, then execute public-only verification.

Export requires the isolated business DB; the child verifier does not open it.
No transaction or identity modification is performed by this command.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from backend.service import TrustaService
from backend.config import Config
from backend.domain import digest
from backend.security.crypto import fingerprint
from backend.collaboration.rounds import CollaborationRounds
from backend.collaboration.workflow import ReadonlyCrosscheck,verify_business
from backend.collaboration.fisco import FiscoVerifier


def finalize(run):
    root=Path('data/anchor-integration');business=root/'business'
    pins=json.loads((run/'trust-pins.json').read_text(encoding='utf-8'))
    bundle=json.loads((run/'round-proof.json').read_text(encoding='utf-8'))
    service=TrustaService(Config(db_path=str(business/'business.sqlite3'),signing_key=(business/'local-seal.key').read_bytes(),
        banking_version=5,communication_version=3,strong_auth_secret='isolated-anchor-no-financial-writes'))
    task=service.blackboard.get(bundle['record']['task_id'])
    context={'business':{'task_id':task['task_id'],'inputs':task['graph_state']['inputs'],'result':task['result'],
                        'compiler_hash':task['graph_state']['compiler_hash']},'graph':task['graph_state']['graph']}
    proof=ReadonlyCrosscheck(service,CollaborationRounds(service,None)).export_business(context,'demo')
    registry_hash=digest(proof['runtime_registry'])
    if not verify_business(proof,bundle,pins['audit_public_key'],pinned_registry_hash=registry_hash):
        raise RuntimeError('existing runtime causal verification failed')
    target=run/'business-proof.json'
    if target.exists() and json.loads(target.read_text(encoding='utf-8'))!=proof:
        raise RuntimeError('refusing to overwrite different public business evidence')
    target.write_text(json.dumps(proof,sort_keys=True,ensure_ascii=False,indent=2),encoding='utf-8')
    deployment=json.loads((root/'weidentity-lab/identity-contract.json').read_text(encoding='utf-8'))
    round_deployment=json.loads((root/'weidentity-lab/round-contract.json').read_text(encoding='utf-8'))
    rpc=FiscoVerifier(9645,artifact=root/'weidentity-lab/conf/roundanchor-artifact.json',artifact_sha256=pins['artifact_sha256'],
                       address=pins['contract'],publisher=pins['publisher'])
    identity_code=rpc.rpc('getCode',[1,deployment['address']])
    arguments={'record-hash':pins['record_hash'],'snapshot-hash':pins['snapshot_hash'],
       'audit-key-hash':fingerprint(pins['audit_public_key']),'evaluator-key-hash':fingerprint(pins['evaluator_public_key']),
       'artifact-hash':pins['artifact_sha256'],'m1-manifest-hash':pins['m1_manifest_sha256'],
       'identity-address':deployment['address'],'identity-code-hash':hashlib.sha256(bytes.fromhex(identity_code[2:])).hexdigest(),
       'runtime-registry-hash':registry_hash}
    arguments|={'contract-address':pins['contract'],'publisher':pins['publisher'],
                'deployment-tx':round_deployment['receipt']['transactionHash']}
    path=run/'verification-arguments.json'
    if path.exists() and json.loads(path.read_text())!=arguments:
        # Additive verifier trust-pin version; retain the original file intact.
        path=run/'verification-arguments-v2.json'
        if path.exists() and json.loads(path.read_text())!=arguments:raise RuntimeError('verification trust pins changed')
    path.write_text(json.dumps(arguments,sort_keys=True,indent=2),encoding='utf-8')
    command=[sys.executable,'-u','-m','scripts.verify_anchor_collaboration','--run',str(run),
             '--output',str(run/'independent-verification.json')]
    for key,value in arguments.items():command+=['--'+key,value]
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=180)
    (run/'independent-verification.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    print(result.stdout,flush=True)
    if result.returncode:raise RuntimeError('public-only independent verifier rejected evidence')
    return json.loads((run/'independent-verification.json').read_text(encoding='utf-8'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True,type=Path)
    finalize(parser.parse_args().run)
