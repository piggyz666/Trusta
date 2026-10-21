from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):
    @abstractmethod
    def generate(self, messages, response_schema, tools, config):
        """Return an LLMResponse candidate; never execute model-supplied tools."""
        raise NotImplementedError
