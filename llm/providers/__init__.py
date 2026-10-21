from llm.providers.openai import OpenAIProvider
from llm.providers.openai_compatible import OpenAICompatibleProvider
from llm.providers.gemini import GeminiProvider

PROVIDERS = {'openai': OpenAIProvider, 'openai_compatible': OpenAICompatibleProvider,
             'gemini': GeminiProvider}


def create_provider(kind):
    from backend.llm.contracts import InferenceError
    if kind not in PROVIDERS:
        raise InferenceError('LLM_INVALID_CONFIG')
    return PROVIDERS[kind]()
