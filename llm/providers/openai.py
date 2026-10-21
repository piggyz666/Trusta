from backend.llm.providers.openai_compatible import OpenAICompatibleProvider as ChatTransport
from llm.providers.common import TransportProvider


class OpenAITransport(ChatTransport):
    def body(self, request):
        body = super().body(request)
        body['max_completion_tokens'] = body.pop('max_tokens')
        return body


class OpenAIProvider(TransportProvider):
    """OpenAI Chat Completions; model and exact endpoint are operator config."""
    transport_class = OpenAITransport
    kind = 'openai'
