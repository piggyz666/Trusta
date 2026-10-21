"""LLM proposes; the local Validator binds capabilities and deterministic compilation."""
from dataclasses import dataclass
from pathlib import Path
from backend.llm.contracts import InferenceError
from llm.hashing import snapshot
from llm.router import RoutingContext
from runtime.task_graph import TaskPlan
from schemas import task_plan_schema
from validator.plan_validator import PlanValidator, PlanningContext


@dataclass(frozen=True)
class UserRequest:
    request_id: str
    text: str
    context: PlanningContext
    routing: RoutingContext

    def validate(self):
        import re
        if (not isinstance(self.request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', self.request_id)
                or not isinstance(self.text, str) or not self.text.strip() or len(self.text) > 4000
                or not isinstance(self.context, PlanningContext) or not isinstance(self.routing, RoutingContext)
                or self.routing.purpose != 'plan_proposal'):
            raise InferenceError('LLM_PLAN_REQUEST')
        self.routing.validate()
        snapshot(self.text)
        return self


class Planner:
    def __init__(self, runtime, validator=None, *, contract_version='2.0.0'):
        self.runtime = runtime
        self.validator = validator or PlanValidator()
        self.runtime.prompts.load(Path(__file__).parent / 'prompt.v2.yaml')
        if contract_version not in {'2.0.0', '3.0.0', '3.1.0', '3.2.0'}:
            raise InferenceError('LLM_PLAN_CONTRACT_VERSION')
        self.contract_version = contract_version
        if contract_version == '3.0.0':
            self.runtime.prompts.load(Path(__file__).parent / 'prompt.v3.yaml')
        if contract_version == '3.1.0':
            self.runtime.prompts.load(Path(__file__).parent / 'prompt.v3.1.yaml')
        if contract_version == '3.2.0':
            self.runtime.prompts.load(Path(__file__).parent / 'prompt.v3.2.yaml')

    def request_contract(self, request):
        request.validate()
        if self.contract_version in {'3.0.0', '3.1.0', '3.2.0'}:
            from .semantic import catalog, output_schema
            items = catalog(self.validator, request.context)
            schema = output_schema(self.validator, request.context)
        else:
            items, schema = self.validator.catalog(request.context), task_plan_schema()
        variables = {'schema': snapshot(schema), 'task_id': request.context.task_id,
                     'catalog': snapshot(items), 'input_keys': snapshot(sorted(request.context.inputs)),
                     'request': request.text}
        if self.contract_version == '3.0.0':
            variables = {k: variables[k] for k in ('catalog', 'input_keys', 'request')}
        if self.contract_version in {'3.1.0', '3.2.0'}:
            from .semantic import input_descriptions
            variables = {'catalog': variables['catalog'], 'request': request.text,
                         'input_descriptions': snapshot(input_descriptions(request.context))}
            if self.contract_version == '3.2.0':
                variables['catalog'] = snapshot([{'agent': item['agent'], 'action': item['action'],
                    'parameter_names': item['parameters']} for item in items])
        return schema, variables

    def generate(self, request):
        """Return a TaskPlan only after schema AND semantic validation.

        Input values remain local. Request text must already satisfy the caller's
        projection/data policy; the Planner never downgrades sensitivity.
        """
        return self.generate_validated(request).plan

    def generate_validated(self, request):
        if not isinstance(request, UserRequest):
            raise InferenceError('LLM_PLAN_REQUEST')
        request.validate()
        schema, variables = self.request_contract(request)
        validated = []

        def validate(candidate):
            if self.contract_version in {'3.0.0', '3.1.0', '3.2.0'}:
                from .semantic import compile_proposal
                validated.append(compile_proposal(candidate.data, request, self.validator))
                return
            plan = TaskPlan.from_dict(candidate.data)
            validated.append(self.validator.validate(plan, request.context))

        self.runtime.generate(request_id=request.request_id, prompt_name='constrained_planner',
            prompt_version=self.contract_version, variables=variables,
            response_schema=schema, routing=request.routing, candidate_validator=validate)
        if len(validated) != 1:
            raise InferenceError('LLM_PLAN_VALIDATION_REQUIRED')
        return validated[0]
