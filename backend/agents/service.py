"""Service Agent: authoritative resource reads and proposals, no execution authority."""
from backend.banking.actions import build_action, snapshot_from_gateway


class ServiceAgent:
    def __init__(self, gateway, config):
        self.gateway, self.config = gateway, config

    def resources(self, user_id, kind):
        return self.gateway.read("service", user_id, kind + ".list")

    def optional_resources(self, user_id, kind):
        """Same read, but an empty list when the connected bank does not offer this kind of resource at all."""
        return self.resources(user_id, kind) if self.gateway.offers(kind + ".list") else []

    def propose(self, task, kind, target_id, values, account_id):
        self.gateway.capabilities.check("service", "service.propose")
        snapshot = snapshot_from_gateway(self.gateway, "service", task["user_id"], account_id, kind, target_id)
        return build_action(task, kind, target_id, values, snapshot, self.config.action_ttl)

    def resolve_subscriptions(self, candidates, authority):
        result = []
        for candidate in candidates:
            matches = [row for row in authority if row["merchant"] == candidate["merchant"] and
                       row["account_id"] == candidate["account_id"] and row["amount"] == candidate["amount"] and
                       row["currency"] == candidate["currency"] and row["status"] == "ACTIVE" and row["cancelable"]]
            result.append({"candidate": candidate, "resolution": "RESOLVED" if len(matches)==1 else "UNRESOLVED",
                           "subscription_id": matches[0]["id"] if len(matches)==1 else None})
        return result
