"""Additive v3 interface for the ORIGINAL contextual graph proposal path.

Uses the same proposal contract/compiler as llm.Planner. No second inference
loop, Agent, authorization service or financial execution implementation.
"""
from pathlib import Path
from backend.domain import Rejected
from backend.llm.contracts import InferenceError
from backend.runtime.taskgraph import node, TaskGraphValidator
from llm.planner import UserRequest
from llm.planner.semantic import catalog, output_schema, input_descriptions, compile_proposal
from llm.prompts import PromptRegistry
from llm.router import RoutingContext
from llm.hashing import snapshot
from llm.contracts import LLMResponse
from llm.structured import LLMStructuredResponse
from validator import PlanningContext, PlanValidator


def context(registry, task_id, goal, inputs):
    validator=PlanValidator(registry)
    grants=tuple(validator.specifications())
    return validator, UserRequest(task_id,goal,PlanningContext(task_id,inputs,grants),RoutingContext('GENERAL_GRAPH'))


def contract(registry, task_id, goal, inputs):
    validator,request=context(registry,task_id,goal,inputs)
    request.validate()
    items=catalog(validator,request.context)
    prompts=PromptRegistry()
    prompts.load(Path(__file__).resolve().parents[2]/'llm/planner/prompt.v3.2.yaml')
    variables={'catalog':snapshot([{'agent':i['agent'],'action':i['action'],
        'parameter_names':i['parameters']} for i in items]),'request':goal,
        'input_descriptions':snapshot(input_descriptions(request.context))}
    return prompts.get('constrained_planner','3.2.0').render(variables).messages,output_schema(validator,request.context)


def normalize(candidate, registry, inputs, task_id, goal):
    try:
        validator,request=context(registry,task_id,goal,inputs)
        value=LLMStructuredResponse.parse(LLMResponse(snapshot(candidate)),output_schema(validator,request.context)).data
        if value['clarification'] is not None:
            if value['calls']: raise InferenceError('LLM_PLAN_CLARIFICATION_CONFLICT')
            return 'NEEDS_CLARIFICATION',{'needs_clarification':True,'reason':value['clarification'],
                'missing_parameters':[], 'question':'请明确所需的银行业务，并补充未绑定的必要参数。','authority':'NONE'}
        validated=compile_proposal(value,request,validator)
        graph=validated.graph
        if len(graph['nodes'])>=32: raise InferenceError('LLM_PLAN_GRAPH_SCHEMA')
        # Aggregation is deterministic and read-only; native capabilities and
        # safety expansion still authorize every real operation independently.
        graph['nodes'].append(node('Report','REPORT','supervisor','graph.report',
            [n['node_id'] for n in graph['nodes'] if not any(n['node_id'] in s['dependencies'] for s in graph['nodes'])]))
        return 'SUCCEEDED',TaskGraphValidator(registry).validate(graph,inputs)
    except InferenceError as error:
        raise Rejected('REJECT_PLAN',error.code) from None


def matches_intrinsic_graphs(candidate, expected, registry, inputs):
    """Only contract registered WealthAgent intrinsic preparatory reads.

    Original grounded matcher still checks every surviving operation, value
    reference, producer, condition and ordering. No generic relaxed matching.
    """
    from copy import deepcopy
    from backend.llm.grounded_request import matches_request
    def contract_reads(graph):
        result=deepcopy(TaskGraphValidator(registry).validate(graph,inputs))
        nodes=result['nodes']
        if any(n['condition'] is not None for n in nodes): return result
        # Derive capability spellings from the existing registry's ActionSpecs.
        from backend.bank_sdk.contracts import CANONICAL
        intrinsic={CANONICAL['customer.get_risk_profile'],CANONICAL['wealth.list_products']}
        reads={n['node_id'] for n in nodes if n['owner']=='wealth' and n['node_type']=='READ'
            and not n['parameters'] and n['capability'] in intrinsic}
        delegates={n['node_id'] for n in nodes if n['owner']=='wealth' and n['capability']=='wealth.recommend'}
        used={v[:-7] for n in nodes for v in n['parameters'].values() if v.endswith(':result')}
        children={n['node_id']:{c['node_id'] for c in nodes if n['node_id'] in c['dependencies']} for n in nodes}
        drop=reads-used
        while True:
            keep={key for key in drop if children[key] and children[key]<=drop|delegates}
            if keep==drop: break
            drop=keep
        lookup={n['node_id']:n for n in nodes}
        def retained(key):
            if key not in drop: return {key}
            return set().union(*(retained(d) for d in lookup[key]['dependencies']))
        for n in nodes:
            n['dependencies']=sorted(set().union(*(retained(d) for d in n['dependencies'])))
        result['nodes']=[n for n in nodes if n['node_id'] not in drop]
        return TaskGraphValidator(registry).validate(result,inputs)
    try:
        return matches_request(contract_reads(candidate),contract_reads(expected))
    except (Rejected,KeyError,ValueError,TypeError):
        return False
