"""Existing read-only, assisted Conversation and Memory assertions also run under V3."""
from dataclasses import replace
import tests.test_scenarios as readonly_baseline
import tests.test_memory as memory_baseline
import tests.test_conversation as conversation_baseline
from backend.service import TrustaService
from backend.llm.gateway import LLMGateway


class Phase4ReadOnlyScenarios(readonly_baseline.ScenarioTests):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, communication_version=3)
        self.service = TrustaService(self.config)


class Phase4Memory(memory_baseline.MemoryTests):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, communication_version=3)
        self.s = TrustaService(self.config)


class Phase4AssistedConversation(conversation_baseline.ConversationTests):
    def setUp(self):
        super().setUp()
        self.config = replace(self.config, communication_version=3)
        self.s = TrustaService(self.config)
        self.s.llm = LLMGateway(self.settings, self.s.db, self.s.audit)
