from backend.llm.providers.gemini import GeminiProvider as GeminiTransport
from llm.providers.common import TransportProvider


class GeminiProvider(TransportProvider):
    transport_class = GeminiTransport
    kind = 'gemini'
