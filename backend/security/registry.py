from backend.agents.registry import AgentRegistry, INFERENCE_GRANTS
from backend.domain import Rejected


BANK_GRANTS = {
    "transaction": {"account.read", "balance.read", "payee.read", "transfer.propose"},
    "policy": {"account.read", "balance.read", "payee.read"},
    "schema": {"account.read", "payee.read"},
    "verifier": {"account.read", "balance.read", "transaction.read"},
    "executor": {"banking.transfer"},
    "finance": {"account.read", "transaction.read"},
    "wealth": {"risk_profile.read", "wealth_product.read"},
}
SYSTEM_PRINCIPALS = {"policy", "schema", "verifier", "executor"}
MEMORY_READERS = {"supervisor": {"PREFERENCE", "CONVERSATION_REFERENCE"},
                  "finance": {"PREFERENCE"}, "wealth": {"PREFERENCE", "RISK_REFERENCE"}}


class CapabilityRegistry:
    """Bank, inference and memory privileges remain separate namespaces."""
    def __init__(self, banking_version=4):
        self.banking_version = banking_version
        self.agents = AgentRegistry(banking_version)
        self.bank_grants = BANK_GRANTS if banking_version == 4 else {actor: set(grants) for actor, grants in BANK_GRANTS.items()}
        if banking_version == 5:
            from backend.banking.actions import ACTION_SPECS
            reads = {"card.read", "subscription.read", "direct_debit.read", "portfolio.read", "operation.read"}
            self.bank_grants["service"] = {"account.read", "card.read", "subscription.read", "direct_debit.read", "service.propose"}
            self.bank_grants["wealth"] |= {"account.read", "balance.read", "portfolio.read", "wealth.propose"}
            for actor in ("schema", "policy", "verifier"):
                self.bank_grants[actor] |= reads | {"risk_profile.read", "wealth_product.read", "balance.read"}
            self.bank_grants["executor"] |= {spec["capability"] for spec in ACTION_SPECS.values()}

    def validate(self):
        active = set(self.agents.active())
        executor = self.bank_grants.get("executor", set())
        if (set(self.bank_grants) - active - SYSTEM_PRINCIPALS
                or set(INFERENCE_GRANTS) - active or set(MEMORY_READERS) - active
                or "banking.transfer" not in executor
                or any(grants & executor for actor, grants in self.bank_grants.items() if actor != "executor")):
            raise Rejected("REGISTRY_INCONSISTENT")

    def check_bank(self, actor, capability):
        if actor not in SYSTEM_PRINCIPALS:
            self.agents.require_active(actor)
        if capability not in self.bank_grants.get(actor, set()):
            raise Rejected("CAPABILITY_VIOLATION")

    def check_inference(self, agent, purpose, task_type):
        self.agents.require_active(agent)
        if task_type not in INFERENCE_GRANTS.get(agent, {}).get(purpose, set()):
            raise Rejected("CAPABILITY_VIOLATION")

    def check_memory(self, agent, kind):
        self.agents.require_active(agent)
        if kind not in MEMORY_READERS.get(agent, set()):
            raise Rejected("MEMORY_CAPABILITY_DENIED")

    def snapshot(self):
        return {"agents": [{"agent": agent, "bank": sorted(self.bank_grants.get(agent, set())),
                            "inference": {purpose: sorted(types) for purpose, types in INFERENCE_GRANTS.get(agent, {}).items()},
                            "memory_read": sorted(MEMORY_READERS.get(agent, set()))}
                           for agent in self.agents.active()],
                "system_principals": {actor: sorted(self.bank_grants[actor]) for actor in sorted(SYSTEM_PRINCIPALS)},
                "dynamic_registration": False}

    def agent_snapshot(self, agent):
        self.validate()
        self.agents.require_active(agent)
        return next(entry for entry in self.snapshot()["agents"] if entry["agent"] == agent)

    def commitment(self, agent):
        from backend.domain import digest
        return digest(self.agent_snapshot(agent))
