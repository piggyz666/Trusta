"""Measured pipeline stability. Legal JSON is never counted as task completion."""
from dataclasses import dataclass
from backend.llm.contracts import InferenceError
from runtime.task_graph import TaskGraph


def execution_alignment(validated, observations=None, *, evidence_verifier=None):
    """Only a trusted external verifier can establish actual execution evidence.

    Observations must identify deterministic compiled step, plan hash and status.
    No observations means NOT_RUN, never a successful simulated payment.
    """
    if observations is None:
        return {'status': 'NOT_RUN', 'aligned': None}
    if not callable(evidence_verifier):
        return {'status': 'UNVERIFIED', 'aligned': False}
    expected = {r['step_id']: r for r in validated.compiled['steps']}
    if not isinstance(observations, (tuple, list)) or not observations:
        return {'status': 'MISMATCH', 'aligned': False}
    seen = set()
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != {'step_id', 'plan_hash', 'owner', 'status', 'evidence'}:
            return {'status': 'MISMATCH', 'aligned': False}
        identifier = observation['step_id']
        if not isinstance(identifier, str) or identifier not in expected or identifier in seen:
            return {'status': 'MISMATCH', 'aligned': False}
        step = expected[identifier]
        if (observation['plan_hash'] != validated.plan.plan_hash or observation['owner'] != step['owner']
                or observation['status'] != 'CONFIRMED' or not set(step['dependencies']) <= seen):
            return {'status': 'MISMATCH', 'aligned': False}
        try:
            if evidence_verifier(observation) is not True:
                return {'status': 'UNVERIFIED', 'aligned': False}
        except Exception:
            return {'status': 'UNVERIFIED', 'aligned': False}
        seen.add(identifier)
    return {'status': 'ALIGNED' if seen == set(expected) else 'INCOMPLETE', 'aligned': seen == set(expected)}


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    request: object
    expected_plan: object | None = None


def plan_signature(plan, specifications):
    rows = TaskGraph.from_plan(plan).topological_sort()
    positions = {n.node_id: i for i, n in enumerate(rows)}
    return tuple((n.agent, specifications[(n.agent, n.action)].permission_action,
        tuple(sorted((k, ('input', v) if v.startswith('input:') else ('node', positions[v[:-7]]))
                     for k, v in n.parameters.items())),
        tuple(sorted(positions[d] for d in n.dependency))) for n in rows)


class ProviderStabilityEvaluator:
    def __init__(self, planner):
        self.planner = planner

    def evaluate(self, case, *, observations=None, evidence_verifier=None):
        validated, error = None, None
        try:
            validated = self.planner.generate_validated(case.request)
        except InferenceError as failure:
            error = failure.code
        events = [e for e in self.planner.runtime.trace_store.events()
                  if e['request_id'] == case.request.request_id and e['status'] != 'RUNNING']
        event = events[-1] if events else {}
        validation = event.get('validation_result', {'schema': 'NOT_RUN', 'plan': 'NOT_RUN'})
        structure = None
        expected = None
        specs = self.planner.validator.specifications()
        if validated:
            structure = plan_signature(validated.plan, specs)
        if case.expected_plan is not None:
            expected = plan_signature(case.expected_plan, specs)
        aligned = execution_alignment(validated, observations, evidence_verifier=evidence_verifier) if validated else {
            'status': 'NOT_RUN', 'aligned': None}
        return {'case_id': case.case_id, 'provider': event.get('provider'), 'model': event.get('model'),
            'prompt_hash': event.get('prompt_hash'), 'schema_valid': validation['schema'] == 'PASS',
            'taskgraph_valid': validation['plan'] == 'PASS',
            'validator_rejected': validation['plan'] == 'FAIL' or validation['schema'] == 'FAIL',
            'planner_success': validated is not None and structure == expected if expected is not None else None,
            'error_code': error, 'execution_alignment': aligned}


def summarize(rows):
    if not rows:
        return {'cases': 0, 'planner_success_rate': None, 'taskgraph_validity': None, 'schema_validity': None,
                'validator_rejection_rate': None, 'execution_alignment': None, 'execution_cases': 0}
    executed = [r for r in rows if r['execution_alignment']['status'] != 'NOT_RUN']
    count = len(rows)
    scored = [r for r in rows if r['planner_success'] is not None]
    return {'cases': count, 'planner_success_rate': sum(r['planner_success'] for r in scored) / len(scored) if scored else None,
        'planner_scored_cases': len(scored),
        'taskgraph_validity': sum(r['taskgraph_valid'] for r in rows) / count,
        'schema_validity': sum(r['schema_valid'] for r in rows) / count,
        'validator_rejection_rate': sum(r['validator_rejected'] for r in rows) / count,
        'execution_alignment': sum(r['execution_alignment']['aligned'] is True for r in executed) / len(executed) if executed else None,
        'execution_cases': len(executed)}
