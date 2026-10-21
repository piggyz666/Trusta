"""Real FISCO + official WeID + Groth16 + existing readonly multi-Agent path.

No paid model calls, public-chain transactions, bank writes or existing wallets.
Historical model responses are re-verified, never presented as fresh inference.
"""
import hashlib
import json
from pathlib import Path
import secrets
import time
import argparse
from backend.config import Config
from backend.service import TrustaService
from backend.domain import digest,uid
from backend.security.crypto import LocalKeyStore,sign_object
from backend.security.local_files import protect_secret
from backend.security.crypto_registry import AUDIT_SIGNER
from backend.collaboration.weidentity import WeIdentityLab
from backend.collaboration.capability_proof import CapabilityProofEngine
from backend.collaboration.admission import CapabilityAdmission,EVALUATOR_DOMAIN
from backend.collaboration.evaluation import evaluate_frozen_model
from backend.collaboration.rounds import CollaborationRounds
from backend.collaboration.verify import verify_round
from backend.collaboration.otc import AdaptiveOTC,DeadlinePolicy
from backend.collaboration.fisco import FiscoVerifier,FiscoAnchorWorker
from backend.collaboration.workflow import ReadonlyCrosscheck,verify_business
from backend.collaboration.telemetry import CollaborationTelemetry,cohort_hash

ROOT=Path('data/anchor-integration')


def save(path,value):
    path.write_text(json.dumps(value,sort_keys=True,ensure_ascii=False,indent=2),encoding='utf-8')


def source_manifest():
    path=ROOT/'m1-source-manifest-v2.json'
    if path.exists():return json.loads(path.read_text(encoding='utf-8'))
    old=json.loads(Path('data/llm-semantic-convergence/independent-verification.json').read_text(encoding='utf-8'))
    rows=[r for r in old['rows'] if r['provider']=='mimo' and not r['attack']]
    cases=[]
    for row in rows:
        item={'case_id':row['case_id'],'expected_complete':True}
        for key,source in [('source','source_path'),('response','response_path'),('proof','proof_path'),('key','proof_path')]:
            p=Path(row[source]);p=p.with_suffix('.pem') if key=='key' else p
            sha=hashlib.sha256(p.read_bytes()).hexdigest()
            if key!='key' and sha!=row[{'source':'source_sha256','response':'response_sha256','proof':'proof_sha256'}[key]]:
                raise RuntimeError('historical pinned model evidence changed')
            item[key]={'path':p.as_posix(),'sha256':sha}
        cases.append(item)
    gold=Path('evaluation_dataset/normal_cases.json')
    value={'version':'TRUSTA-FROZEN-MODEL-CAPABILITY-REPLAY-V2','provider':'mimo','model':'mimo-v2.6-flash',
           'scope':'HISTORICAL_REAL_RESPONSES_OFFLINE_REVERIFICATION','cases':cases,
           'gold':{'path':gold.as_posix(),'sha256':hashlib.sha256(gold.read_bytes()).hexdigest()}}
    save(path,value);return value


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--reuse-admission',action='store_true')
    options=parser.parse_args()
    run=ROOT/'acceptance'/uid();run.mkdir(parents=True)
    business=ROOT/'business';business.mkdir(exist_ok=True)
    seal=business/'local-seal.key'
    if not seal.exists():seal.write_bytes(secrets.token_bytes(32))
    protect_secret(seal)
    s=TrustaService(Config(db_path=str(business/'business.sqlite3'),banking_version=5,communication_version=3,
                          signing_key=seal.read_bytes(),strong_auth_secret='isolated-anchor-no-financial-writes'))
    source=source_manifest();evaluation,rows=evaluate_frozen_model(Path.cwd(),source)
    save(run/'historical-reverification.json',{'scope':source['scope'],'rows':rows,'new_model_calls':0})
    model=digest({'provider':source['provider'],'model':source['model'],'benchmark_root':digest(source),
                  'model_weights_available':False,'source':'PINNED_HISTORICAL_REAL_RESPONSES'})
    evaluator_dir=ROOT/'evaluator';evaluator_dir.mkdir(exist_ok=True)
    store=LocalKeyStore(evaluator_dir/'keys',seal.read_bytes());pin=evaluator_dir/'public-key.json'
    if pin.exists():evaluator_key=json.loads(pin.read_text())
    else:
        key_id='key-'+uid();evaluator_key={'key_id':key_id,'public_key':store.generate(key_id)};save(pin,evaluator_key)
    evaluator_private=store.load(evaluator_key)
    manifest=ROOT/'m1-artifacts/manifest.json'
    engine=CapabilityProofEngine(manifest.parent,hashlib.sha256(manifest.read_bytes()).hexdigest())
    policy={'version':'TRUSTA-M1-BANK-READONLY-V1','epoch':3,'threshold':800000,'benchmark_root':digest(source),
            'evaluator_version':digest({'source':hashlib.sha256(Path('backend/collaboration/evaluation.py').read_bytes()).hexdigest(),
                                        'scoring':'one expected completion per case: TP success, FP and FN failure'}),
            'max_age_seconds':86400}
    identity=WeIdentityLab(s)
    admission=CapabilityAdmission(s,identity,engine,policy=policy,evaluator_public_key=evaluator_key['public_key'])
    agents=['finance','wealth','transaction']
    for agent in agents:
        print('Verifying identity and M1:',agent,flush=True)
        if options.reuse_admission:
            admission._current(agent,admission.policy_id,model)
            continue
        binding=identity.register_binding(agent,epoch=policy['epoch'],model_commitment=model,policy_id=admission.policy_id)
        statement=admission.expected_statement(binding);proof=engine.prove(statement,evaluation)
        now=int(time.time());body={'version':1,'statement_hash':__import__('backend.collaboration.capability_proof',fromlist=['statement_hash']).statement_hash(statement),
            'public_signals_hash':digest(proof['public_signals']),'manifest_sha256':engine.pin,
            'evidence_hash':digest({'source':source,'rows':rows}),'issued_at':now,'expires_at':now+86400,
            'scope':'RESTRICTED_CAPABILITY_EVALUATION_NO_FINANCIAL_AUTHORITY'}
        attestation={'body':body,'signature':sign_object(evaluator_private,EVALUATOR_DOMAIN,body)}
        admission.admit(agent,binding=binding,proof=proof,attestation=attestation)
        save(run/(agent+'-admission.json'),{'binding':binding,'proof':proof,'attestation':attestation})
    controller=AdaptiveOTC(DeadlinePolicy(1000000000,120000000000,120000000000,1,120,
                                         (1000000000,30000000000,60000000000,120000000000)))
    rounds=CollaborationRounds(s,admission)
    telemetry=CollaborationTelemetry(rounds)
    history=telemetry.history()
    cohort={a:{'model_commitment':model,'capability_hash':s.crypto_registry.resolve(a)['capability_hash']} for a in agents}
    deadline=controller.select(history,next_sequence=max((r.sequence for r in history),default=0)+1,
                               configuration_hash=cohort_hash(cohort,2,admission.policy_id))
    workflow=ReadonlyCrosscheck(s,rounds)
    context=workflow.run('demo',account_id='acct-demo',month='2026-09',deadline=deadline,
                         policy_id=admission.policy_id,model_commitments={a:model for a in agents})
    state,task,graph=context['state'],context['task'],context['graph']
    task_id=task['task_id'];inputs=context['business']['inputs'];facts=digest(context['business']);elapsed=context['accepted']
    print('Accepted original Agent outputs:',[v['agent'] for v in elapsed],flush=True)
    while time.monotonic_ns()-state['started_ns']<state['duration_ns']:time.sleep(.25)
    bundle=rounds.export(state['round_id'],'demo')
    audit_key=s.crypto_registry.resolve(AUDIT_SIGNER)['public_key']
    if not verify_round(bundle,pinned_snapshot=state['snapshot'],pinned_audit_key=audit_key,
                        expected_record_hash=digest(bundle['record'])):raise RuntimeError('independent round proof failed')
    deployment=json.loads((ROOT/'weidentity-lab/round-contract.json').read_text())
    artifact=ROOT/'weidentity-lab/conf/roundanchor-artifact.json';artifact_pin=hashlib.sha256(artifact.read_bytes()).hexdigest()
    verifiers=[FiscoVerifier(port,artifact=artifact,artifact_sha256=artifact_pin,address=deployment['address'],
                            publisher=deployment['publisher']) for port in (9645,9646)]
    anchored=FiscoAnchorWorker(rounds,verifiers).process(state['round_id'],'demo')
    while time.monotonic_ns()-state['started_ns']<state['telemetry_horizon_ns']:time.sleep(.25)
    from dataclasses import asdict
    save(run/'otc-telemetry.json',asdict(telemetry.capture(state['round_id'],'demo')))
    business_proof=workflow.export_business(context,'demo')
    if not verify_business(business_proof,bundle,audit_key,pinned_registry_hash=digest(business_proof['runtime_registry'])):
        raise RuntimeError('business causal proof failed')
    save(run/'round-proof.json',bundle);save(run/'fisco-proof.json',anchored)
    save(run/'business-proof.json',business_proof)
    save(run/'business-evidence.json',{'scope':'LOCAL_VIRTUAL_BANK_READONLY_REAL_FISCO',
         'task':task,'graph':graph,'inputs':inputs,'facts_hash':facts,'accepted':elapsed,
         'bank_transactions':len(s.db.rows('SELECT * FROM transactions')),'new_model_calls':0,
         'public_chain_broadcasts':0,'financial_authority':False})
    save(run/'trust-pins.json',{'audit_public_key':audit_key,'evaluator_public_key':evaluator_key['public_key'],
        'policy':policy,'snapshot_hash':digest(state['snapshot']),'record_hash':digest(bundle['record']),
        'artifact_sha256':artifact_pin,'contract':deployment['address'],'publisher':deployment['publisher'],
        'm1_manifest_sha256':engine.pin})
    s.audit.append(task_id,None,'ANCHOR_READONLY_RESULT_VERIFIED','supervisor',
                    {'round_id':state['round_id'],'record_hash':digest(bundle['record']),
                     'financial_authority':False,'result_hash':digest(task['result'])})
    from scripts.finalize_anchor_evidence import finalize
    finalize(run)
    print(json.dumps({'scope':'REAL_FISCO_TEST_CONSORTIUM','round_id':state['round_id'],
        'record_hash':digest(bundle['record']),'run':str(run),'closed':bundle['record']['status'],
        'chain_verified':True,'financial_writes':0}),flush=True)


if __name__=='__main__':main()
