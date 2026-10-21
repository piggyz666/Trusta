from types import MappingProxyType
from backend.llm.contracts import InferenceError
from validator import PlanValidator

GRANTS = MappingProxyType({
    'finance': frozenset({'account.get', 'account.get_balance', 'transaction.list', 'finance.summary', 'finance.anomalies'}),
    'transaction': frozenset({'account.get', 'account.get_balance', 'payee.resolve', 'transfer.propose'}),
    'wealth': frozenset({'customer.get_risk_profile', 'wealth.list_products', 'wealth.recommend', 'wealth.compare'}),
    'supervisor': frozenset({'graph.report', 'graph.join'}),
})


class SandboxPermissions:
    """Fixed server grants. No setter, model-authored permissions or payment grant."""
    def check_context(self, context):
        specs = PlanValidator().specifications()
        for pair in context.allowed_actions:
            spec = specs.get(pair)
            if spec is None or spec.permission_action not in GRANTS.get(pair[0], frozenset()):
                raise InferenceError('LLM_SANDBOX_PERMISSION')

    def describe(self, agent_id):
        if agent_id not in GRANTS:
            raise InferenceError('LLM_SANDBOX_PERMISSION')
        return {'agent_id': agent_id, 'permissions': sorted(GRANTS[agent_id])}
