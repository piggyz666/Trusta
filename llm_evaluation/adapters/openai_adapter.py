from llm_evaluation.adapters.compatible_adapter import CompatibleAdapter
from backend.llm.contracts import InferenceError


class OpenAIAdapter(CompatibleAdapter):
    def __init__(self, config, *args, **kwargs):
        if config.kind != 'openai':
            raise InferenceError('LLM_INVALID_CONFIG')
        super().__init__(config, *args, **kwargs)
