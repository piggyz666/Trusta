"""Additive candidate observation; all validation stays in frozen LLMRuntime."""
from llm.runtime import LLMRuntime


class EvidenceRuntime(LLMRuntime):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.responses = {}

    def generate(self, **kwargs):
        validator = kwargs.get('candidate_validator')
        request_id = kwargs['request_id']

        def observe(candidate):
            self.responses[request_id] = candidate.response
            if validator is not None: validator(candidate)

        return super().generate(**(kwargs | {'candidate_validator':observe}))
