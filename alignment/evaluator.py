"""Public offline verifier: native signed Merkle proofs + full plan/runtime binding."""
from backend.domain import digest
from backend.audit_trust.records import verify_record
from llm.hashing import digest as trace_digest
from llm.hashing import text_hash
from backend.llm.validation import strict_json
from alignment.matcher import match_execution


def verify_execution(bundle, pinned_public_key, *, expected_plan_hash, expected_request_hash):
    try:
        r = bundle['record']
        if bundle.get('schema_version') != 1 or bundle['scope'] != 'LOCAL_VIRTUAL_BANK_ONLY' or r['scope'] != bundle['scope']:
            return False
        if r['plan_hash'] != expected_plan_hash or r['request_hash'] != expected_request_hash or r['status'] != 'COMPLETED':
            return False
        proof = bundle['audit_proof']
        if not verify_record(proof, pinned_public_key) or proof['event']['payload_hash'] != digest(r):
            return False
        if (proof['event']['task_id'] != r['runtime_task_id'] or proof['event']['actor'] != 'alignment_adapter'
                or proof['event']['event_type'] != 'EXECUTION_ALIGNMENT_COMPLETED'):
            return False
        previous, states, request_binding = '0' * 64, {}, None
        for seq, e in enumerate(r['llm_events'], 1):
            if e['status'] not in {'RUNNING', 'SUCCEEDED', 'REJECTED', 'FAILED'}:
                return False
            payload = {k: v for k, v in e.items() if k not in {'seq', 'record_hash', 'previous_hash'}}
            if e['seq'] != seq or e['previous_hash'] != previous or trace_digest({'previous_hash': previous, 'payload': payload}) != e['record_hash']:
                return False
            prior = states.get(e['request_id'])
            if prior is None and e['status'] != 'RUNNING' or prior is not None and (prior != 'RUNNING' or e['status'] == 'RUNNING'):
                return False
            states[e['request_id']] = e['status']
            if e['request_id'] == r['request_id']:
                if e['status'] == 'RUNNING':
                    request_binding = e['request_hash']
                    prompt_hash = e['prompt_hash']
                elif (e['status'] != 'SUCCEEDED' or e['prompt_hash'] != prompt_hash or
                      e.get('validation_result') != {'schema': 'PASS', 'plan': 'PASS'} or
                      e['provider'] != r['provider'] or e['model'] != r['model'] or
                      e['response_hash'] != text_hash(r['llm_response']['content']) or
                      response_plan_hash(r) != expected_plan_hash):
                    return False
            previous = e['record_hash']
        if (previous != r['llm_trace_tail'] or request_binding != expected_request_hash
                or states.get(r['request_id']) != 'SUCCEEDED'):
            return False
        if not match_execution(r)['complete']:
            return False
        receipts = r['execution']['receipts']
        if len(bundle['receipt_proofs']) != len(receipts):
            return False
        proven = set()
        for receipt_proof in bundle['receipt_proofs']:
            if not verify_record(receipt_proof, pinned_public_key):
                return False
            receipt = receipt_proof.get('receipt')
            if not receipt or receipt not in receipts or receipt['receipt_id'] in proven:
                return False
            proven.add(receipt['receipt_id'])
        return len(proven) == len(receipts)
    except Exception:
        return False


def response_plan_hash(record):
    """Verify additive v3 proposal -> original TaskPlan binding, offline.

    Never substitutes a repaired response. Recompile the exact signed/audited
    raw proposal with the original local context and original Validator.
    """
    candidate = strict_json(record['llm_response']['content'])
    if isinstance(candidate, dict) and candidate.get('version') == 3:
        from llm.planner import UserRequest
        from llm.planner.semantic import compile_proposal
        from llm.router import RoutingContext
        from validator import PlanValidator, PlanningContext
        context = record['context']
        request = UserRequest(record['request_id'], record['plan']['goal'],
            PlanningContext(context['task_id'], context['inputs'], context['allowed_actions']),
            RoutingContext('BALANCE', max_latency_ms=60000))
        return compile_proposal(candidate, request, PlanValidator()).plan.plan_hash
    return trace_digest(candidate)
