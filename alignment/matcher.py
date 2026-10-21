from backend.domain import digest
from runtime.task_graph import TaskPlan
from validator import PlanningContext, PlanValidator


def match_execution(record):
    context = record['context']
    validated = PlanValidator().validate(TaskPlan.from_dict(record['plan']),
        PlanningContext(context['task_id'], context['inputs'], context['allowed_actions']))
    if validated.plan.plan_hash != record['plan_hash'] or validated.context_hash != record['context_hash']:
        return {'matched_actions': 0, 'total_actions': len(validated.plan.steps), 'accuracy': 0.0, 'complete': False}
    execution = record['execution']
    actual = {s['step_id']: s for s in execution['steps']}
    expected = validated.compiled['steps']
    if len(actual) != len(execution['steps']) or set(actual) != {s['step_id'] for s in expected}:
        raise ValueError('step set mismatch')
    for step in expected:
        observed = actual[step['step_id']]
        if (observed['owner'] != step['owner'] or observed['name'] != step['name'] or observed['status'] != 'COMPLETED'
                or observed['dependencies'] != step['dependencies']):
            return {'matched_actions': 0, 'total_actions': len(validated.plan.steps), 'accuracy': 0.0, 'complete': False}
    matched = 0
    for n in validated.graph['nodes']:
        observed = actual[n['node_id']]
        # Literal trusted inputs must match exactly. Ancestor-derived data is committed by the audit snapshot.
        exact = set(observed['parameters']) == set(n['parameters']) and all(
            observed['parameters'][k] == context['inputs'][ref] if ref.startswith('input:') else
            digest(observed['parameters'][k]) == actual[ref[:-7]]['output_hash']
            for k, ref in n['parameters'].items())
        if n['node_type'] == 'PROPOSE_ACTION':
            action = next((a for a in execution['actions'] if a['action']['action_id'] == observed.get('action_id')
                           and a['action']['initiating_agent'] == n['owner']
                           and a['action']['action_id'] in {r['action_id'] for r in execution['receipts']}), None)
            params = observed['parameters']
            exact = exact and bool(action) and action['verification']['status'] == 'VERIFIED'
            if action and n['capability'] == 'transfer.propose':
                a = action['action']
                receipt = next(r for r in execution['receipts'] if r['action_id'] == a['action_id'])
                auth, policy, contract, tx, verification = (action[k] for k in ('authorization','policy','contract','transaction','verification'))
                action_hash = digest(a)
                exact = exact and (a['task_id'] == record['runtime_task_id'] == receipt['task_id']
                    and receipt == action['receipt'] and receipt['verification_status'] == 'VERIFIED'
                    and receipt['approved_action_hash'] == action_hash == auth['action_hash'] == policy['action_hash'] == contract['approved_action_hash']
                    and auth['status'] == 'AUTHORIZED' and auth['user_id'] == record['user_id']
                    and receipt['authorization_id'] == auth['authorization_id'] == contract['authorization_id']
                    and receipt['policy_decision_id'] == policy['policy_decision_id'] == contract['policy_decision_id']
                    and contract['action_id'] == a['action_id'] and policy['decision'] != 'DENY'
                    and receipt['bank_transaction_id'] == tx['id'] and tx['status'] == 'SETTLED'
                    and all(tx[k] == a[k] for k in ('amount','currency','recipient_id','source_account_id'))
                    and tx['after_balance'] == tx['before_balance'] - tx['amount']
                    and all(receipt[k] == verification[k] for k in ('pre_state_hash','post_state_hash')))
                exact = exact and a['amount'] == params['amount'] and a['currency'] == params['currency'] and a['source_account_id'] == params['account_id']
                exact = exact and a['recipient_id'] == observed['recipient_evidence']['id']
        matched += bool(exact and observed['capability'] == n['capability'])
    total = len(validated.plan.steps)
    proposals = [n for n in validated.graph['nodes'] if n['node_type'] == 'PROPOSE_ACTION']
    return {'matched_actions': matched, 'total_actions': total, 'accuracy': matched / total,
            'complete': matched == total and execution['status'] == 'COMPLETED'
                        and execution['task_id'] == record['runtime_task_id'] and digest(execution) == record['execution_hash']
                        and len(execution['actions']) == len(execution['receipts']) == len(proposals)}
