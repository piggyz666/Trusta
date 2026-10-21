"""Version 3 proposal adapter into the existing TaskPlan/Validator/compiler.

No authority, financial literals, bank facts or execution are manufactured here.
The model chooses business calls; local code owns identifiers and causal edges.
"""
from backend.llm.contracts import InferenceError
from llm.hashing import snapshot
from llm.contracts import LLMResponse
from llm.structured import LLMStructuredResponse
from runtime.task_graph import TaskPlan
import re


def catalog(validator, context):
    specs = validator.specifications()
    entries = {}
    for item in validator.catalog(context):
        spec = specs[(item['agent'], item['action'])]
        key = (spec.agent, spec.permission_action)
        entries[key] = {'agent': spec.agent, 'action': spec.permission_action,
                        'parameters': list(spec.parameters)}
    return [entries[k] for k in sorted(entries)]


def input_descriptions(context):
    """Trusted shape metadata only; values and financial facts stay local."""
    aliases = {'account': 'account_id', 'recipient': 'recipient_name'}
    descriptions={}
    for key,value in context.inputs.items():
        field=key.removeprefix('input:')
        scoped=re.fullmatch(r's([0-9]+)_([a-z_]+)',field)
        if scoped: field=scoped[2]
        description={'field_name':aliases.get(field,field),'value_type':type(value).__name__,'bound_locally':True}
        if scoped: description['clause_index']=int(scoped[1])
        descriptions[key]=description
    return descriptions


def output_schema(validator, context):
    ref = {'type': 'string', 'pattern': r'^(input:[A-Za-z][A-Za-z0-9_]{0,63}|call:[0-9]{1,2})$'}
    choices = []
    for item in catalog(validator, context):
        choices.append({'type': 'object', 'additionalProperties': False,
            'required': ['agent', 'action', 'parameters'], 'properties': {
                'agent': {'const': item['agent']}, 'action': {'const': item['action']},
                'parameters': {'type': 'object', 'additionalProperties': False,
                    'required': item['parameters'], 'properties': {k: ref for k in item['parameters']}}}})
    return {'type': 'object', 'additionalProperties': False,
        'required': ['version', 'calls', 'clarification'], 'properties': {
            'version': {'const': 3},
            'calls': {'type': 'array', 'maxItems': 32, 'items': {'anyOf': choices}},
            'clarification': {'enum': [None, 'MISSING_INPUT', 'UNSUPPORTED_REQUEST']}}}


def compile_proposal(candidate, request, validator):
    parsed = LLMStructuredResponse.parse(LLMResponse(snapshot(candidate)),
        output_schema(validator, request.context)).data
    if parsed['clarification'] is not None:
        if parsed['calls']:
            raise InferenceError('LLM_PLAN_CLARIFICATION_CONFLICT')
        raise InferenceError('LLM_PLAN_CLARIFICATION_REQUIRED')
    if not parsed['calls']:
        raise InferenceError('LLM_PLAN_EMPTY')
    specs, nodes, seen, previous = validator.specifications(), [], {}, None
    for index, call in enumerate(parsed['calls']):
        spec = specs[(call['agent'], call['action'])]
        parameters, dependencies = {}, set()
        for key, ref in call['parameters'].items():
            if ref.startswith('call:'):
                producer = int(ref.split(':')[1])
                if producer >= index:
                    raise InferenceError('LLM_PLAN_FORWARD_REFERENCE')
                ref = f'C{producer}:result'
                dependencies.add(f'C{producer}')
            elif ref not in request.context.inputs:
                raise InferenceError('LLM_PLAN_MISSING_INPUT')
            parameters[key] = ref
        # Compare normalized provenance, not wire spelling (call:0/call:00).
        identity=snapshot({'agent':spec.agent,'action':spec.permission_action,'parameters':parameters})
        if identity in seen:
            intervening_write=any(specs[(n['agent'],n['action'])].node_type=='PROPOSE_ACTION'
                for n in nodes[seen[identity]+1:])
            if spec.node_type!='READ' or not intervening_write:
                raise InferenceError('LLM_PLAN_REDUNDANT_CALL')
        seen[identity]=index
        # The ordered business contract explicitly promises source order. This
        # also preserves the existing prohibition on parallel financial writes.
        if previous is not None:
            dependencies.add(previous)
        identifier = f'C{index}'
        nodes.append({'node_id': identifier, 'agent': spec.agent, 'action': spec.permission_action,
            'parameters': parameters, 'dependency': sorted(dependencies)})
        previous = identifier
    plan = TaskPlan.from_dict({'task_id': request.context.task_id,
                              'goal': request.text, 'steps': nodes})
    return validator.validate(plan, request.context)
