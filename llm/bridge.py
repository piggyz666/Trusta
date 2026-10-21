"""Backward-compatible provider boundary for the contextual TRUSTA Gateway."""
from backend.llm.contracts import ProviderResponse
from llm.contracts import LLMModelConfig
from llm.providers import create_provider


class BackendProviderBridge:
    def __init__(self, config, *, provider=None):
        self.config = config
        self.provider = provider if provider is not None else create_provider(config.kind)

    def infer(self, request):
        response = self.provider.generate(request.messages, request.output_schema, (),
            LLMModelConfig(self.config, request.timeout_seconds, request.max_output_tokens))
        # The contextual Gateway retains its existing grounded schema validator.
        reported_model = None if response.diagnostics.get('reported_model_redacted') else response.model
        return ProviderResponse(response.content, reported_model, response.usage)

    def health(self):
        return {'status': 'configured', 'network_checked': False}

    def describe_capabilities(self):
        return self.provider.transport_class(self.config).describe_capabilities()


def backend_provider(config):
    if config.kind in {'openai', 'openai_compatible', 'gemini'}:
        return BackendProviderBridge(config)
    from backend.llm.providers.http_adapter import HTTPAdapter
    from backend.llm.providers.mock import MockProvider
    return {'mock': MockProvider, 'http': HTTPAdapter}[config.kind](config)
