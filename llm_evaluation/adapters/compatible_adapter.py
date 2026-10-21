from llm.contracts import LLMModelConfig
from llm.prompts import PromptRegistry
from llm.planner import Planner
from llm.router import ModelRoute, ModelRouter
from llm.trace import LLMTraceStore
from urllib.parse import urlsplit
from llm_evaluation.adapters.evidence_runtime import EvidenceRuntime


class CompatibleAdapter:
    """Use the existing gateway/planner, with no tool execution or SDK calls."""
    def __init__(self, config, trace_path, *, budget=None, timeout=45, max_output_tokens=2048, observation_dir=None, contract_version='2.0.0'):
        self.trace = LLMTraceStore(trace_path)
        self.config, self.budget = config, budget
        self.observation_dir = observation_dir
        self.model_config = LLMModelConfig(config, timeout, max_output_tokens)
        self.runtime = EvidenceRuntime(PromptRegistry(), ModelRouter([ModelRoute(self.model_config)], max_fallbacks=0), self.trace)
        self.planner = Planner(self.runtime, contract_version=contract_version)
        self.source = 'LOCAL_HTTP_FIXTURE' if urlsplit(config.endpoint).hostname in {'localhost', '127.0.0.1', '::1'} else 'REAL_PROVIDER_HTTP'

    def request_material(self, request):
        _, variables = self.planner.request_contract(request)
        return self.planner.runtime.prompts.get('constrained_planner', self.planner.contract_version).render(variables).messages

    def generate(self, request):
        if self.source == 'REAL_PROVIDER_HTTP':
            if self.budget is None:
                from backend.llm.contracts import InferenceError
                raise InferenceError('LLM_BUDGET_APPROVAL_REQUIRED')
            from llm.providers.common import validate_input
            schema, _ = self.planner.request_contract(request)
            material = self.request_material(request)
            validate_input(material, schema, (), self.model_config)
            self.budget.reserve(request.request_id, self.config.name, material, self.model_config.max_output_tokens,
                                schema=schema if self.config.structured_outputs else None)
            return self._isolated_generate(request)
        return self.planner.generate_validated(request)

    def verify_response_binding(self, response, request, validated):
        from backend.llm.validation import strict_json
        candidate = strict_json(response.content)
        if self.planner.contract_version in {'3.0.0', '3.1.0', '3.2.0'}:
            from llm.planner.semantic import compile_proposal
            rebound = compile_proposal(candidate, request, self.planner.validator)
            return rebound.plan.as_dict() == validated.plan.as_dict() and rebound.context_hash == validated.context_hash
        return candidate == validated.plan.as_dict()

    def _isolated_generate(self, request):
        import multiprocessing
        import time
        from pathlib import Path
        from backend.llm.contracts import InferenceError
        from runtime.task_graph import TaskPlan
        from llm_evaluation.adapters.worker import infer
        from llm.hashing import digest
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        observation = str(Path(self.observation_dir) / (digest(request.request_id) + '.json')) if self.observation_dir else None
        process = context.Process(target=infer, args=(self.config, self.trace.path, request,
            self.model_config.timeout, self.model_config.max_output_tokens, sender, observation, self.planner.contract_version))
        started = time.monotonic()
        try:
            process.start(); sender.close()
            if not receiver.poll(self.model_config.timeout + 5):
                process.terminate(); process.join(5)
                if process.is_alive(): process.kill(); process.join(5)
                self.close_interrupted(request.request_id, 'LLM_TIMEOUT', time.monotonic() - started)
                raise InferenceError('LLM_TIMEOUT')
            try: result = receiver.recv()
            except EOFError:
                self.close_interrupted(request.request_id, 'LLM_EVALUATION_WORKER_FAILURE', time.monotonic() - started)
                raise InferenceError('LLM_EVALUATION_WORKER_FAILURE') from None
            if 'error' in result: raise InferenceError(result['error'])
            from llm.contracts import LLMResponse
            self.runtime.responses[request.request_id] = LLMResponse(**result['response'])
            return self.planner.validator.validate(TaskPlan.from_dict(result['plan']), request.context)
        finally:
            if process.pid:
                process.join(3)
                if process.is_alive(): process.terminate(); process.join(5)
            receiver.close(); sender.close()

    def close_interrupted(self, request_id, code='LLM_EVALUATION_INTERRUPTED', latency=0):
        events = [e for e in self.trace.events() if e['request_id'] == request_id]
        if events and events[-1]['status'] == 'RUNNING':
            self.trace.finish(request_id, status='FAILED', provider=self.config.name, model=self.config.model,
                latency=latency, prompt_hash=events[0]['prompt_hash'], response_hash=None,
                attempts=[{'provider':self.config.name,'model':self.config.model,'response_hash':None,'error_code':code}],
                error_code=code, validation_result={'schema':'NOT_RUN','plan':'NOT_RUN'})
