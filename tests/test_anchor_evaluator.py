"""Replay frozen real responses without API calls; recompute original Gold."""
import hashlib
import json
from pathlib import Path
import unittest
from backend.collaboration.evaluation import evaluate_frozen_model
from backend.domain import Rejected


def manifest(provider):
    records=json.loads(Path('data/llm-semantic-convergence/independent-verification.json').read_text(encoding='utf-8'))
    cases=[];model=None
    for row in records['rows']:
        if row['provider']!=provider or row['attack']:continue
        item={'case_id':row['case_id'],'expected_complete':True}
        for field,source in [('source','source_path'),('response','response_path'),('proof','proof_path'),('key','proof_path')]:
            path=Path(row[source]);path=path.with_suffix('.pem') if field=='key' else path
            item[field]={'path':path.as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        model=json.loads(Path(row['source_path']).read_text(encoding='utf-8'))['result']['model'];cases.append(item)
    gold=Path('evaluation_dataset/normal_cases.json')
    return {'provider':provider,'model':model,'cases':cases,
            'gold':{'path':gold.as_posix(),'sha256':hashlib.sha256(gold.read_bytes()).hexdigest()}}


class TrustedCapabilityEvaluatorTests(unittest.TestCase):
    def test_frozen_real_mimo_twenty_goals_recomputed(self):
        counts,rows=evaluate_frozen_model(Path.cwd(),manifest('mimo'))
        self.assertEqual((counts['tp'],counts['fp'],counts['fn']),(20,0,0))
        self.assertTrue(all(r['gold_semantic_equivalent'] and r['independent_verified'] for r in rows))

    def test_real_execution_of_incomplete_deepseek_plan_is_not_goal_success(self):
        counts,rows=evaluate_frozen_model(Path.cwd(),manifest('deepseek'))
        failure=next(r for r in rows if r['case_id']=='analysis03')
        self.assertTrue(failure['independent_verified']);self.assertFalse(failure['gold_semantic_equivalent'])
        self.assertFalse(failure['complete']);self.assertEqual(counts['tp'],19)

    def test_frozen_raw_source_hash_mismatch_rejected(self):
        value=manifest('mimo');value['cases'][0]['source']['sha256']='0'*64
        with self.assertRaises(Rejected):evaluate_frozen_model(Path.cwd(),value)

    def test_gold_substitution_rejected(self):
        value=manifest('mimo');value['gold']['sha256']='0'*64
        with self.assertRaises(Rejected):evaluate_frozen_model(Path.cwd(),value)
