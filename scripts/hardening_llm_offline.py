"""Saved-response replay/root-cause audit. Never invokes a remote provider."""
import hashlib,json,shutil,tempfile
from pathlib import Path
from scripts import amend_final_llm_scores as replay
from backend.llm.validation import strict_json
from backend.llm.compact_graph import normalize
from backend.llm.grounded_request import normalize_request,matches_request
from backend.domain import Rejected
from tests.phase6_helpers import service
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'docs/backend-final-hardening'

def main():
    original=ROOT/'docs/final-backend/LLM_Live_Benchmark.json'
    with tempfile.TemporaryDirectory(prefix='trusta-historical-replay-') as directory:
        replay.OUT=Path(directory);shutil.copy2(original,replay.OUT/'LLM_Live_Benchmark.json');replay.main()
        doc=json.loads((replay.OUT/'LLM_Score_Amendment.json').read_text(encoding='utf-8'))
    doc.update(evidence_kind='HISTORICAL_REAL_RESPONSE_CURRENT_CODE_OFFLINE_REPLAY',remote_requests_this_run=0,
               live_model_retested=False,old_holdout_now_used=True)
    (OUT/'llm-historical-replay.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='trusta-failure-analysis-') as directory:
        s=service(directory)
        for r in doc['results']:
            raw=r['captured'][0];case=r['case'];causes=[];candidate=strict_json(raw['raw_output'])
            if raw['error']:causes.append('PROVIDER_TRANSPORT_OR_RATE_LIMIT')
            if isinstance(candidate,dict) and {'properties','type'}<=set(candidate):causes.append('SCHEMA_ECHO_NOT_INSTANCE')
            if case['mode']=='B_INTENT':
                expected=r['expected']
                if candidate.get('task_type')!=expected['task_type']:causes.append('INTENT_SEMANTICS')
                if any(candidate.get(k)!=expected.get(k,'') for k in ('recipient_text','amount_text','month_text')):
                    causes.append('SLOT_SUBSTRING_OR_CONTEXT')
                    if candidate.get('amount_text')==expected.get('amount_text','')+'元':causes.append('PROMPT_AMOUNT_UNIT_SPAN_AMBIGUITY')
                if candidate.get('needs_clarification')!=expected['needs_clarification']:causes.append('CLARIFICATION_SEMANTICS')
            elif case['mode']=='B_GRAPH':
                expected=normalize_request(s,case['text'],'acct-demo')
                try:
                    status,graph=normalize(candidate,s.registry,expected['inputs'])
                    if status!='SUCCEEDED':causes.append('OVER_CLARIFICATION_OF_COMPLETE_REQUEST')
                    elif not matches_request(graph,expected['graph']):causes.append('VALID_SCHEMA_WRONG_OPERATIONS_OR_ORDER')
                except Rejected as exc:
                    causes.append('VALIDATOR_'+str(exc))
                if candidate.get('schema_version')!=2:causes.append('MISSING_OR_WRONG_VERSION_ENVELOPE')
            if not r['score']['raw_correct'] and not causes:causes.append('SEMANTIC_OR_FORMAT_FAILURE_REQUIRES_MANUAL_REVIEW')
            rows.append({'provider':r['provider'],'case_id':case['id'],'mode':case['mode'],
                         'raw_output_sha256':hashlib.sha256(raw['raw_output'].encode()).hexdigest(),
                         'raw_correct':r['score']['raw_correct'],'root_causes':causes,
                         'finish_reason':raw['raw_response']['choices'][0]['finish_reason'] if raw.get('raw_response') else None,
                         'returned_model':raw['returned_model'],'usage':raw['usage']})
    report={'status':'LOCAL_VERIFIED','method':'Classification from actual saved raw content and current deterministic decoder. No retry/repair/model call.',
        'source_path':'docs/final-backend/LLM_Live_Benchmark.json','source_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),
        'cases':rows,'remote_requests':0,'new_cost_usd':0,
        'limits':['Historical failures are not a fresh blind evaluation','Earlier GLM 4.7 API 429/truncation probes are separate historical evidence',
                  'Working glm-4-flash quality cannot characterize all GLM models','No financial rules or missing fields auto-repaired']}
    (OUT/'llm-failure-analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'LOCAL_VERIFIED','historical_cases':len(rows),'new_remote_requests':0}))

if __name__=='__main__':main()
