REGISTRY = {
    "supervisor": {"active": True}, "planner": {"active": True}, "transaction": {"active": True},
    "finance": {"active": True}, "service": {"active": False}, "wealth": {"active": True},
}

# Inference privileges are separate from bank capabilities; no new Agent or execute grant.
INFERENCE_GRANTS = {
    "supervisor": {"intent_normalization": {"TRANSFER", "BILL_ANALYSIS", "WEALTH_RECOMMENDATION"},
                   "conversation_intent": {"CONVERSATION"}},
    "planner": {"plan_proposal": {"TRANSFER", "BILL_ANALYSIS", "WEALTH_RECOMMENDATION"}},
    "finance": {"finance_explanation": {"BILL_ANALYSIS"}, "finance_presentation": {"BILL_ANALYSIS"}},
    "wealth": {"wealth_explanation": {"WEALTH_RECOMMENDATION"}, "wealth_presentation": {"WEALTH_RECOMMENDATION"}},
}


class AgentRegistry:
    """Trusted-process identities; there is no client or model registration path."""
    def __init__(self, banking_version=4):
        self.banking_version = banking_version

    def require_active(self, agent):
        from backend.domain import Rejected
        if agent not in REGISTRY or not (REGISTRY[agent]["active"] or agent == "service" and self.banking_version == 5):
            raise Rejected("UNKNOWN_AGENT")

    def active(self):
        return sorted(agent for agent, entry in REGISTRY.items() if entry["active"] or agent == "service" and self.banking_version == 5)


def check_inference(agent, purpose, task_type):
    from backend.domain import Rejected
    from backend.llm.contracts import InferenceError
    from backend.security.registry import CapabilityRegistry
    try:
        CapabilityRegistry().check_inference(agent, purpose, task_type)
    except Rejected:
        raise InferenceError("LLM_CAPABILITY_DENIED") from None
