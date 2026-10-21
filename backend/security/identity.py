import hashlib
import hmac
from backend.domain import Rejected
from backend.agents.registry import AgentRegistry


class AgentIdentity:
    def __init__(self, master_key, keyring=None):
        self.master_key, self.keyring = master_key, keyring

    def key(self, agent, version=None, signing=False):
        AgentRegistry().require_active(agent)
        if version is not None:
            if not self.keyring:
                raise Rejected("MESSAGE_KEY_UNKNOWN")
            return self.keyring.key(agent, version, signing)
        return hmac.new(self.master_key, agent.encode(), hashlib.sha256).digest()
