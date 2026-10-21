"""Independent verifier: public bundle + explicit trust pins + two read-only RPCs.

Does not instantiate TrustaService, access a business DB, import control keys or
call the Java signer. Current WeID validity is a separate live check.
"""
import argparse
import hashlib
import json
from pathlib import Path
from backend.domain import digest,Rejected
from backend.security.crypto import verify_object,fingerprint
from backend.collaboration.capability_proof import CapabilityProofEngine,statement_hash
from backend.collaboration.admission import EVALUATOR_DOMAIN
from backend.collaboration.weidentity import DOMAIN
from backend.collaboration.verify import verify_round
from backend.collaboration.fisco import FiscoVerifier
from backend.collaboration.workflow import verify_business


def verify(run,*,record_hash,snapshot_hash,audit_key_hash,evaluator_key_hash,artifact_hash,m1_manifest_hash,
           identity_address,identity_code_hash,runtime_registry_hash,contract_address,publisher,deployment_tx):
    pins=json.loads((run/'trust-pins.json').read_text(encoding='utf-8'))
    bundle=json.loads((run/'round-proof.json').read_text(encoding='utf-8'))
    chain=json.loads((run/'fisco-proof.json').read_text(encoding='utf-8'))
    if (fingerprint(pins['audit_public_key'])!=audit_key_hash or fingerprint(pins['evaluator_public_key'])!=evaluator_key_hash
            or digest(bundle['snapshot'])!=snapshot_hash or digest(bundle['record'])!=record_hash
            or pins['artifact_sha256']!=artifact_hash or pins['m1_manifest_sha256']!=m1_manifest_hash
            or pins['contract'].lower()!=contract_address.lower() or pins['publisher'].lower()!=publisher.lower()):
        raise Rejected('ANCHOR_INDEPENDENT_TRUST_PIN_MISMATCH')
    if not verify_round(bundle,pinned_snapshot=bundle['snapshot'],pinned_audit_key=pins['audit_public_key'],
                        expected_record_hash=record_hash):raise Rejected('ANCHOR_INDEPENDENT_ROUND_INVALID')
    business=json.loads((run/'business-proof.json').read_text(encoding='utf-8'))
    if not verify_business(business,bundle,pins['audit_public_key'],pinned_registry_hash=runtime_registry_hash):
        raise Rejected('ANCHOR_INDEPENDENT_BUSINESS_INVALID')
    engine=CapabilityProofEngine('data/anchor-integration/m1-artifacts',m1_manifest_hash,verification_only=True)
    checked=[]
    for agent,member in bundle['snapshot']['members'].items():
        record=member['admission_evidence'];binding=record['binding']['binding'];proof=record['proof']
        policy=pins['policy'];statement=proof['statement'];attestation=record['attestation']['body']
        verify_object(pins['evaluator_public_key'],EVALUATOR_DOMAIN,attestation,record['attestation']['signature'])
        verify_object(member['public_key'],DOMAIN,binding,record['binding']['signature'])
        if (digest(record)!=member['admission_hash'] or record['revoked'] is not False
                or record['policy_id']!=digest(policy) or bundle['record']['policy_id']!=digest(policy)
                or statement['policy_id']!=digest(policy) or binding['policy_id']!=digest(policy)
                or statement['chain_id']!=101 or statement['protocol_domain']!='TRUSTA-ANCHOR-P256-V1'
                or statement['epoch']!=policy['epoch'] or statement['benchmark_root']!=policy['benchmark_root']
                or statement['evaluator_version']!=policy['evaluator_version'] or statement['threshold']!=policy['threshold']
                or statement['model_commitment']!=member['model_commitment'] or statement['did']!=member['did']
                or statement['message_key_hash']!=fingerprint(member['public_key'])
                or binding['public_key']!=member['public_key'] or binding['key_id']!=member['key_id']
                or binding['did']!=member['did'] or binding['epoch']!=member['epoch']
                or binding['capability_hash']!=member['capability_hash'] or binding['financial_authority'] is not False
                or attestation['statement_hash']!=statement_hash(statement)
                or attestation['manifest_sha256']!=m1_manifest_hash
                or attestation['version']!=1 or attestation['scope']!='RESTRICTED_CAPABILITY_EVALUATION_NO_FINANCIAL_AUTHORITY'
                or type(attestation['issued_at']) is not int or type(attestation['expires_at']) is not int
                or not 0<attestation['expires_at']-attestation['issued_at']<=policy['max_age_seconds']
                or attestation['public_signals_hash']!=digest(proof['public_signals'])
                or not attestation['issued_at']*1000 <= bundle['record']['started_at_ms'] < attestation['expires_at']*1000):
            raise Rejected('ANCHOR_INDEPENDENT_ADMISSION_INVALID')
        engine.verify(proof,statement,proof['public_signals']);checked.append(agent)
    verifiers=[FiscoVerifier(p,artifact='data/anchor-integration/weidentity-lab/conf/roundanchor-artifact.json',
        artifact_sha256=artifact_hash,address=pins['contract'],publisher=pins['publisher']) for p in (9645,9646)]
    checks=[]
    tx=chain.get('recovered_transaction_hash') or chain['submission']['receipt']['transactionHash']
    for verifier in verifiers:
        checks.append(verifier.verify(bundle['record'],tx))
        checks[-1]['deployment']=verifier.verify_deployment(deployment_tx)
        verifier.address=identity_address.lower()
        code=verifier.rpc('getCode',[1,identity_address])
        if hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()!=identity_code_hash:
            raise Rejected('ANCHOR_INDEPENDENT_IDENTITY_CODE_MISMATCH')
        for member in bundle['snapshot']['members'].values():
            b=member['admission_evidence']['binding']['binding'];address=b['did'].split(':')[-1]
            state=verifier.call('resolve(address)',['address'],[address],
                                ['string','string','bool','uint256','string[]','string[]'])
            expected=b['did']+'#trusta-p256-'+str(b['epoch'])+',TRUSTA-P256-Binding,urn:trusta:anchor:binding:'+digest(b)
            if state[2] is not False or list(state[5]).count(expected)!=1:
                raise Rejected('ANCHOR_INDEPENDENT_LIVE_WEID_INVALID')
    return {'verified':True,'scope':'REAL_FISCO_TEST_CONSORTIUM','record_hash':record_hash,
            'members_verified':checked,'rpc_checks':checks,'private_key_required':False,'business_db_required':False,
            'new_model_calls':0,'arbitrary_model_inference_zk_proven':False,'production_setup':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    for key in ('record-hash','snapshot-hash','audit-key-hash','evaluator-key-hash','artifact-hash',
                'm1-manifest-hash','identity-address','identity-code-hash','runtime-registry-hash',
                'contract-address','publisher','deployment-tx'):p.add_argument('--'+key,required=True)
    p.add_argument('--output',type=Path,required=True);args=vars(p.parse_args());output=args.pop('output')
    try:result=verify(**args)
    except (Rejected,ValueError,KeyError,TypeError,OSError) as exc:
        result={'verified':False,'error':getattr(exc,'code',type(exc).__name__)}
    output.write_text(json.dumps(result,sort_keys=True,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='rpc_checks'}));return 0 if result['verified'] else 1


if __name__=='__main__':raise SystemExit(main())
