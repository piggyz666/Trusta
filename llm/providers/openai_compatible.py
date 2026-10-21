from backend.llm.providers.openai_compatible import OpenAICompatibleProvider as ChatTransport
from llm.providers.common import TransportProvider


class OpenAICompatibleProvider(TransportProvider):
    """DeepSeek, GLM and private/local Chat Completions endpoints."""
    transport_class = ChatTransport
    kind = 'openai_compatible'
