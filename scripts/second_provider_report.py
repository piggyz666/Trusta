"""Publish only after full regression and frozen live captures are verified."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.config import Config
from backend.llm.configuration import LLMSettings, ProviderConfig
from backend.llm.contracts import ProviderResponse
from backend.llm.gateway import LLMGateway
from backend.runtime.taskgraph import SAFETY
from backend.service import TrustaService
from benchmarks.taskgraph_live_evaluation import score
from benchmarks.second_provider_evaluation import summarize


def load(path):return json.loads((ROOT/path).read_text(encoding='utf-8'))


def main():
    log=(ROOT/'docs/phase6-second-provider-tests.txt').read_text(encoding='utf-8')
    match=re.search(r'Ran (\d+) tests in ([\d.]+)s\s+OK\s*$',log)
    if not match:raise SystemExit('Full regression must pass first')
    test_hashes=load('data/phase6-original-test-hashes.json')
    assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==value for path,value in test_hashes.items())
    baseline=load('data/phase6-taskgraph-usability-before/manifest.json')['files']
    assert hashlib.sha256((ROOT/'backend/runtime/taskgraph.py').read_bytes()).hexdigest()==baseline['backend/runtime/taskgraph.py']
    dataset=ROOT/'benchmarks/taskgraph_holdout_v2.json'
    live=load('docs/phase6-second-provider-regression.json')
    routes=load('docs/phase6-second-provider-routes.json')
    smoke=load('data/deepseek-smoke.json')
    assert live['dataset_hash']==hashlib.sha256(dataset.read_bytes()).hexdigest()
    assert live['metrics']==summarize(live['results'])
    assert routes['metrics']==summarize(routes['results'])
    assert len(live['results'])==24 and live['metrics']['all']['real_model_responses']==24
    assert len(routes['results'])==7 and all(row['passed'] for row in routes['results'])
    assert smoke['metrics']['all']['real_model_responses']==1
    live_counts={'preflight':smoke['metrics']['all']['real_model_responses'],
        'deepseek_regression':live['metrics']['all']['real_model_responses'],
        'route_scenarios':routes['metrics']['all']['real_model_responses']}
    live_counts['total']=sum(live_counts.values())
    prior=load('docs/phase6-taskgraph-usability-validation.json')['source_hashes']
    assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==value for path,value in prior.items())
    # No keys in any persisted live evidence; contents are strictly synthetic.
    for env in ('DEEPSEEK_API_KEY','GEMINI_API_KEY'):
        key=os.getenv(env)
        if key:
            assert key not in json.dumps(live) and key not in json.dumps(routes)
    replays=[]
    for row in live['results']:
        assert row['fault_injection'] is None and row['real_model_responses']==1
        captures=row['captured_synthetic_outputs'];assert len(captures)==1
        text=captures[0]['raw_synthetic_output']
        class Replay:
            def infer(self,request):return ProviderResponse(text)
        with tempfile.TemporaryDirectory() as directory:
            s=TrustaService(Config(db_path=directory+'/bank.sqlite', banking_version=5,
                communication_version=3,signing_key=b'second-provider-replay-synthetic'))
            s.llm=LLMGateway(LLMSettings(mode='assisted',max_output_tokens=4096,
                providers=(ProviderConfig('replay',max_sensitivity='restricted'),)),
                s.db,s.audit,{'replay':Replay()})
            s.llm.identity_guard=s.crypto_registry.require_active
            case=row['gold'];task=s.graphs.propose('demo',case['goal'],case['inputs'])
            assert task['status']==row['status'] and score(case,task)==row['passed']
            writes=len(s.db.rows('SELECT * FROM transactions'))+len(s.db.rows('SELECT * FROM bank_operations'))
            auth_statuses=[json.loads(record['body'])['status'] for record in s.db.rows('SELECT body FROM authorizations')]
            assert writes==0 and all(status in {'PENDING_CONFIRMATION','PENDING_STRONG_AUTH'} for status in auth_statuses)
            step_names=[step['name'] for step in task['steps']]
            if case['expected_status']=='WAITING_USER':assert set(SAFETY)<=set(step_names)
            replays.append({'id':case['id'],'same_outcome':True,'status':task['status'],
                'compiled_step_names':step_names,'bank_writes':writes,'user_authorizations':0,'new_live_calls':0})
            replays[-1]['authorization_request_statuses']=auth_statuses
    source_paths=['backend/llm/configuration.py','backend/llm/providers/openai_compatible.py',
        'backend/llm/graph_protocol.py','backend/llm/graph_proposal.py','backend/runtime/taskgraph.py',
        'backend/runtime/graph_runtime.py','backend/llm/providers/gemini.py',
        'benchmarks/second_provider_evaluation.py','tests/test_second_provider.py']
    usage={name:0 for name in ('input_tokens','output_tokens')}
    for row in live['results']:
        for cap in row['captured_synthetic_outputs']:
            for name in usage:usage[name]+=(cap.get('usage') or {}).get(name,0)
    report={'passed':True,'full_tests':int(match[1]),'duration_seconds':float(match[2]),
        'original_test_files_byte_identical':len(test_hashes),
        'original_validator_and_security_compiler_unchanged':True,
        'previous_graph_protocol_runtime_and_gemini_unchanged':True,
        'live_deepseek_regression':live['metrics'],'route_scenarios':routes['metrics']['all'],
        'configured_models':live['configured_models'],
        'returned_deepseek_models':sorted({cap['returned_model'] for row in live['results'] for cap in row['captured_synthetic_outputs']}),
        'regression_usage_measured':usage,'measured_cost_usd':None,
        'final_core_captured_replays':replays,
        'live_calls':live_counts,
        'source_hashes':{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in source_paths},
        'limits':['Reused 24-case graph regression, not new independent intent gold or production accuracy',
            'Outages are local test doubles; healthy backup inference is real, remote outages were not observed',
            'No official bank sandbox, external Sepolia, DID or ZKP validation added',
            'Customer goals stay restricted; public example remains shadow and cannot route these goals',
            'Default runtime remains disabled; synthetic review does not authorize customer data egress',
            'No financial approval or bank write in model evaluations; cost is unknown']}
    (ROOT/'docs/phase6-second-provider-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'full_tests':int(match[1]),'replays':len(replays),'new_live_calls':0}))


if __name__=='__main__':main()
