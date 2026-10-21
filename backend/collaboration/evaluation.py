"""Trusted historical-response evaluator. No inference, network or Agent input.

This re-verifies frozen real-model public execution proofs and raw-response
bindings. It does not claim to measure a new model call or prove model weights.
"""
import hashlib
import json
from pathlib import Path
from alignment.evaluator import verify_execution
from backend.domain import Rejected, digest
from evaluation.llm.semantic import equivalent
from runtime.task_graph import TaskPlan
from validator import PlanValidator
from llm_evaluation.evaluator import request_for


def evaluate_frozen_model(workspace, manifest):
    root=Path(workspace).resolve();rows=[];tp=fp=fn=0;models=set()
    gold_path=root/manifest['gold']['path']
    gold_raw=gold_path.read_bytes()
    if hashlib.sha256(gold_raw).hexdigest()!=manifest['gold']['sha256']:
        raise Rejected('ANCHOR_EVALUATOR_GOLD_CHANGED')
    gold_value=json.loads(gold_raw)
    gold_rows=gold_value['cases'] if isinstance(gold_value,dict) else gold_value
    gold={case['id']:case for case in gold_rows}
    for item in manifest['cases']:
        loaded={}
        for field in ('source','response','proof','key'):
            path=(root/item[field]['path']).resolve()
            if not path.is_relative_to(root):raise Rejected('ANCHOR_EVALUATOR_SOURCE_PATH')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=item[field]['sha256']:
                raise Rejected('ANCHOR_EVALUATOR_FROZEN_SOURCE_CHANGED')
            loaded[field]=raw.decode('utf-8') if field=='key' else json.loads(raw)
        result=loaded['source']['result'];record=loaded['proof']['record']
        response=loaded['response']['response'];payload=loaded['response']['provider_payload']
        if (result['source']!='REAL_PROVIDER_HTTP' or result['provider']!=manifest['provider']
                or result['model']!=manifest['model'] or response['model']!=manifest['model']
                or payload['model']!=manifest['model'] or result['case_id']!=item['case_id']
                or result['attack'] is not False or item['expected_complete'] is not True
                or response['content']!=record['llm_response']['content']
                or payload['choices'][0]['message']['content']!=response['content']):
            raise Rejected('ANCHOR_EVALUATOR_MODEL_PROVENANCE_MISMATCH')
        verified=verify_execution(loaded['proof'],loaded['key'],expected_plan_hash=result['plan_hash'],
                                  expected_request_hash=result['request_hash'])
        # Existing independent verifier validates response/request/plan binding.
        case=gold[item['case_id']]
        semantic=equivalent(TaskPlan.from_dict(record['plan']),TaskPlan.from_dict(case['expected_plan']),
                            request_for(case).context,PlanValidator())
        completed=bool(verified and semantic and record['execution']['status']=='COMPLETED')
        tp+=int(completed);fp+=int(not completed);fn+=int(not completed)
        models.add(response['model'])
        rows.append({'case_id':item['case_id'],'complete':completed,'independent_verified':verified,'gold_semantic_equivalent':semantic,
                     'request_hash':result['request_hash'],'plan_hash':result['plan_hash'],
                     'source_hash':item['source']['sha256'],'response_hash':item['response']['sha256']})
    if len(rows)<20 or len({r['case_id'] for r in rows})!=len(rows) or models!={manifest['model']}:
        raise Rejected('ANCHOR_EVALUATOR_INCOMPLETE_BENCHMARK')
    return {'tp':tp,'fp':fp,'fn':fn,'prediction_hash':digest(rows)},rows
