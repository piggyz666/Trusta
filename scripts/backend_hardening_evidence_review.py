"""Recalculate captured measurements and review historical external evidence only."""
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
from backend.audit_trust.records import verify_record
from backend.config import Config
from benchmarks.phase6_llm_metrics import validate_summary

ROOT=Path(__file__).resolve().parents[1]
def read(name):return json.loads((ROOT/name).read_text(encoding='utf-8'))
def write(name,value):(ROOT/'docs'/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def main():
    Config.local()
    proof=read('docs/revolut-transfer-receipt-proof.json')
    public=(ROOT/'docs/revolut-transfer-audit-public.pem').read_text(encoding='utf-8')
    bank=read('docs/revolut-settled-readback.json')
    verified=verify_record(proof,public)
    receipt=proof['receipt']
    matches=receipt['receipt_id']==bank['receipt_id'] and receipt['bank_transaction_id']==bank['bank_transaction_id'] and receipt['task_id']==bank['task_id']
    assert verified and matches
    external={'reviewed_at_utc':datetime.now(timezone.utc).isoformat(),'classification':'HISTORICAL_OFFICIAL_SANDBOX_EVIDENCE_REVERIFIED',
        'fresh_bank_http_calls_this_round':0,'production_funds_operations':0,'historical_readback_checks':{'passed':sum(v is True for v in bank['checks'].values()),'total':len(bank['checks'])},
        'pinned_public_key_receipt_proof_valid':verified,'receipt_identifiers_match':matches,
        'historical_task_status':bank['historical_task_status'],'supplementary_verification_status':bank['supplementary_verification_status'],
        'historical_payment_claims':bank['bank_pay_submission_claims'],'bank_signed_attestation':False,
        'source_hashes':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
            ('docs/revolut-settled-readback.json','docs/revolut-transfer-receipt-proof.json','docs/revolut-transfer-audit-public.pem')}}
    write('external-bank-evidence-review.json',external)
    names=['ANCHOR_RPC_URL','ANCHOR_CONTRACT_ADDRESS','ANCHOR_WALLET_PRIVATE_KEY']
    missing=[name for name in names if not os.getenv(name)]
    write('external-anchor-status.json',{'checked_at_utc':datetime.now(timezone.utc).isoformat(),'classification':'BLOCKED_BY_EXTERNAL_CREDENTIAL' if missing else 'CONFIGURED_NOT_LIVE_VERIFIED',
        'missing_configuration':missing,'live_submitted':False,'external_blockchain_verified':False,'chain_id':11155111,
        'tx_hash':None,'production_claim':False,'gas_and_deployed_contract_not_verified':True,
        'local_evm_evidence':'tests/test_phase6_anchor.py; not a Sepolia confirmation'})
    history=read('docs/real-llm-evaluation.json');validate_summary(history)
    write('historical-llm-metric-review.json',{'classification':'CAPTURED_EVIDENCE_RECALCULATED_NO_MODEL_CALL',
        'source_sha256':hashlib.sha256((ROOT/'docs/real-llm-evaluation.json').read_bytes()).hexdigest(),
        'summary_consistent':True,'metrics':history['detailed_metrics'],'outcomes':history['outcomes'],
        'raw_unsafe_definition':'legacy candidate flag: forbidden envelope OR grounding failure OR invalid graph; not bank execution or pure model unsafe output',
        'historical_files_modified':False})
    final={}
    for provider in ('deepseek','gemini'):
        report=read('docs/llm-final-holdout-'+provider+'.json');rows=report['results']
        assert len(rows)==120 and len({r['id'] for r in rows})==120
        final[provider]={'classification':report['classification'],'configured_model':report['configured_model'],
            'metrics':report['metrics'],'system_statuses':dict(Counter(r['system_status'] for r in rows)),
            'task_outcomes':dict(Counter(r['task_outcome'] for r in rows)),
            'valid_statuses':dict(Counter(r['system_status'] for r in rows if r['category']=='valid')),
            'failure_reasons':dict(Counter(error for r in rows if not r['passed'] for error in r['errors'])),
            'unauthorized_bank_writes':sum(r['unauthorized_bank_writes'] for r in rows),
            'expected_state_hit_not_raw_intent_accuracy':True,
            'dataset_sha256':report['dataset_sha256'],'source_hashes':report['source_hashes'],
            'all_case_ids_retained':True,'cost_usd':None}
    write('llm-holdout-summary.json',{'protocol':'compact_v2 -> original TaskGraph validator -> deterministic compiler -> bank grounding -> runtime',
        'final_independent_dataset':True,'first_pass_failures_retained':True,'fixed_gold_no_final_retuning':True,
        'gold_manual_synthetic_not_real_customer_distribution':True,'providers':final})
    print(json.dumps({'bank_proof_valid':verified,'anchor_missing_count':len(missing),'llm':{k:v['metrics'] for k,v in final.items()}}))

if __name__=='__main__':main()
