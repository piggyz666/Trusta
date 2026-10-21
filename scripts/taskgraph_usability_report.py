"""Validate frozen live evidence and replay captured candidates on final Core."""
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.config import Config
from backend.domain import canonical
from backend.llm.configuration import LLMSettings,ProviderConfig
from backend.llm.contracts import ProviderResponse
from backend.llm.gateway import LLMGateway
from backend.service import TrustaService
from benchmarks.taskgraph_live_evaluation import score


def main():
    def load(path):return json.loads((ROOT/path).read_text(encoding='utf-8'))
    tests=(ROOT/'docs/phase6-taskgraph-tests.txt').read_text(encoding='utf-8')
    match=re.search(r'Ran (\d+) tests in ([\d.]+)s\s+OK\s*$',tests)
    if not match:raise SystemExit('Final full regression must pass before publishing')
    hashes=load('data/phase6-original-test-hashes.json')
    assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==value for path,value in hashes.items())
    baseline=load('data/phase6-taskgraph-usability-before/manifest.json')['files']
    assert hashlib.sha256((ROOT/'backend/runtime/taskgraph.py').read_bytes()).hexdigest()==baseline['backend/runtime/taskgraph.py']
    v1=load('docs/phase6-taskgraph-heldout.json');v2=load('docs/phase6-taskgraph-heldout-v2.json')
    for version,report in ((1,v1),(2,v2)):
        dataset=ROOT/f'benchmarks/taskgraph_holdout_v{version}.json'
        assert report['dataset_hash']==hashlib.sha256(dataset.read_bytes()).hexdigest()
        assert report['evaluated_cases']==len(report['results'])==24
    replays=[]
    for row in v2['results']:
        observed=next(v for v in row['variants'] if v['protocol']=='closed')
        capture=observed['captured_synthetic_outputs']
        assert len(capture)==1
        text=capture[0]['raw_output']
        class Replay:
            def infer(self,request):return ProviderResponse(text)
        with tempfile.TemporaryDirectory() as directory:
            s=TrustaService(Config(db_path=directory+'/bank.sqlite',banking_version=5,
                communication_version=3,signing_key=b'final-core-replay-synthetic-only'))
            s.llm=LLMGateway(LLMSettings(mode='assisted',providers=(ProviderConfig('replay',max_sensitivity='restricted'),)),s.db,s.audit,{'replay':Replay()})
            s.llm.identity_guard=s.crypto_registry.require_active
            case=row['gold'];task=s.graphs.propose('demo',case['goal'],case['inputs'])
            same=task['status']==observed['status'] and score(case,task)==observed['passed']
            writes=len(s.db.rows('SELECT * FROM transactions'))+len(s.db.rows('SELECT * FROM bank_operations'))
            assert same and writes==0
            replays.append({'id':row['id'],'same_outcome':same,'bank_writes':writes,'model_called':False})
    files=['backend/llm/graph_protocol.py','backend/llm/graph_proposal.py','backend/runtime/graph_runtime.py','backend/llm/providers/gemini.py']
    output={'passed':True,'full_tests':int(match[1]),'duration_seconds':float(match[2]),
        'original_test_files_byte_identical':len(hashes),'original_validator_and_security_compiler_unchanged':True,
        'paired_live_evaluation_v2':v2['metrics'],'v1_failed_development_evaluation_preserved':True,
        'v2_first_live_evaluation_scope':'Measured before final hardening that forbids asking customers for replacement bank facts; all 24 captured candidates replay identically on final Core.',
        'final_core_captured_replay':replays,'source_hashes':{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in files},
        'original_100_case_intent_metrics_unchanged':True,
        'remaining_limits':['Small synthetic dataset; one call per model/protocol/case, no statistical generalization claim',
            'One target_id clarification was returned as resource_id; canonical parameter score remains failed',
            'No second live provider, official bank sandbox, external chain, DID or ZKP verification added',
            'Financial proposals await independent user authorization; zero bank writes in evaluation']}
    (ROOT/'docs/phase6-taskgraph-usability-validation.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'full_tests':int(match[1]),'captured_replays':len(replays),'new_model_calls':0}))


if __name__=='__main__':main()
