"""Candidate permission checks followed by the FROZEN TaskGraph validator.

No authorization generation, dynamic agent registration or execution capability.
"""
from dataclasses import dataclass
import json
import re
from backend.domain import Rejected
from backend.llm.contracts import InferenceError
from backend.runtime.taskgraph import (TaskGraphValidator, SecurityExpansionCompiler, SPECIAL,
                                      PARAMS, READ_PARAMS, node)
from backend.bank_sdk.contracts import CANONICAL
from backend.security.capability import CapabilityManager
from backend.security.registry import CapabilityRegistry
from llm.hashing import digest, snapshot
from runtime.task_graph import TaskGraph, TaskPlan

ALIASES = {('finance', 'analysis'): 'finance.summary',
           ('finance', 'detect_anomalies'): 'finance.anomalies',
           ('transaction', 'create_transfer_plan'): 'transfer.propose'}


@dataclass(frozen=True)
class ActionSpec:
    agent: str
    action: str
    node_type: str
    capability: str
    parameters: tuple[str, ...]
    permission_action: str


@dataclass(frozen=True, init=False)
class PlanningContext:
    task_id: str
    _inputs: str
    allowed_actions: frozenset[tuple[str, str]]

    def __init__(self, task_id, inputs, allowed_actions):
        if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', task_id):
            raise InferenceError('LLM_PLAN_CONTEXT')
        if (not isinstance(inputs, dict) or len(inputs) > 64 or
                any(not isinstance(k, str) or not re.fullmatch(r'input:[A-Za-z][A-Za-z0-9_]{0,63}', k) for k in inputs)):
            raise InferenceError('LLM_PLAN_CONTEXT')
        encoded = snapshot(inputs)
        if len(encoded.encode()) > 65536:
            raise InferenceError('LLM_PLAN_CONTEXT')
        if not isinstance(allowed_actions, (tuple, list, set, frozenset)) or not allowed_actions:
            raise InferenceError('LLM_PLAN_PERMISSION')
        pairs = []
        for pair in allowed_actions:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2 or any(not isinstance(x, str) for x in pair):
                raise InferenceError('LLM_PLAN_PERMISSION')
            pairs.append(tuple(pair))
        object.__setattr__(self, 'task_id', task_id)
        object.__setattr__(self, '_inputs', encoded)
        object.__setattr__(self, 'allowed_actions', frozenset(pairs))

    @property
    def inputs(self):
        return json.loads(self._inputs)


@dataclass(frozen=True)
class ValidatedPlan:
    plan: TaskPlan
    _graph: str
    _compiled: str
    context_hash: str

    @property
    def graph(self):
        return json.loads(self._graph)

    @property
    def compiled(self):
        return json.loads(self._compiled)


class PlanValidator:
    def __init__(self, registry=None):
        self.registry = registry or CapabilityRegistry(banking_version=5)
        self.registry.validate()

    def specifications(self):
        specs = {}
        counts = {}
        for tool in READ_PARAMS:
            counts[CANONICAL[tool]] = counts.get(CANONICAL[tool], 0) + 1
        for agent in self.registry.agents.active():
            for tool, keys in READ_PARAMS.items():
                if CapabilityManager.READ_MAP[tool] in self.registry.bank_grants.get(agent, set()):
                    cap = CANONICAL[tool]
                    specs[(agent, tool)] = ActionSpec(agent, tool, 'READ', cap, tuple(sorted(keys)), tool)
                    if counts[cap] == 1:
                        specs[(agent, cap)] = ActionSpec(agent, cap, 'READ', cap, tuple(sorted(keys)), tool)
            for kind, actors in SPECIAL.items():
                for cap in actors.get(agent, set()):
                    # decision.boolean uses DECIDE; conditional execution stays in the native runtime.
                    if kind == 'BRANCH':
                        continue
                    specs[(agent, cap)] = ActionSpec(agent, cap, kind, cap, tuple(sorted(PARAMS[cap])), cap)
            for cap in ('transfer.propose', 'service.propose', 'wealth.propose'):
                if cap in self.registry.bank_grants.get(agent, set()):
                    specs[(agent, cap)] = ActionSpec(agent, cap, 'PROPOSE_ACTION', cap, tuple(sorted(PARAMS[cap])), cap)
        for (agent, alias), cap in ALIASES.items():
            if (agent, cap) in specs:
                spec = specs[(agent, cap)]
                specs[(agent, alias)] = ActionSpec(agent, alias, spec.node_type, cap, spec.parameters, spec.permission_action)
        return specs

    def catalog(self, context):
        permitted = self._permissions(context)
        return [{'agent': s.agent, 'action': s.action, 'parameters': list(s.parameters)}
                for _, s in sorted(self.specifications().items()) if (s.agent, s.permission_action) in permitted]

    def _permissions(self, context):
        if not isinstance(context, PlanningContext):
            raise InferenceError('LLM_PLAN_CONTEXT')
        specs, permissions = self.specifications(), set()
        for pair in context.allowed_actions:
            if pair not in specs:
                raise InferenceError('LLM_PLAN_PERMISSION')
            permissions.add((pair[0], specs[pair].permission_action))
        return permissions

    def validate(self, plan, context):
        if not isinstance(context, PlanningContext):
            raise InferenceError('LLM_PLAN_CONTEXT')
        if not isinstance(plan, TaskPlan):
            raise InferenceError('LLM_PLAN_SCHEMA')
        try:
            plan = TaskPlan.from_dict(plan.as_dict())
        except (InferenceError, AttributeError, TypeError, ValueError):
            raise InferenceError('LLM_PLAN_SCHEMA') from None
        if plan.task_id != context.task_id:
            raise InferenceError('LLM_PLAN_TASK_BINDING')
        permitted, specs = self._permissions(context), self.specifications()
        graph = TaskGraph.from_plan(plan)
        semantic = []
        for item in graph.topological_sort():
            try:
                self.registry.agents.require_active(item.agent)
            except Rejected:
                raise InferenceError('LLM_PLAN_UNKNOWN_AGENT') from None
            spec = specs.get((item.agent, item.action))
            if spec is None:
                raise InferenceError('LLM_PLAN_UNKNOWN_ACTION')
            if (item.agent, spec.permission_action) not in permitted:
                raise InferenceError('LLM_PLAN_ACTION_PERMISSION')
            if set(item.parameters) != set(spec.parameters):
                raise InferenceError('LLM_PLAN_PARAMETERS')
            semantic.append(node(item.node_id, spec.node_type, spec.agent, spec.capability,
                                 item.dependency, item.parameters))
        candidate = {'schema_version': 1, 'nodes': semantic}
        try:
            TaskGraphValidator(self.registry).validate(candidate, context.inputs)
            compiled = SecurityExpansionCompiler(self.registry).compile(candidate, context.inputs)
        except Rejected:
            raise InferenceError('LLM_PLAN_GROUNDED_VALIDATION') from None
        return ValidatedPlan(plan, snapshot(candidate), snapshot(compiled), digest({
            'task_id': context.task_id, 'inputs': context.inputs, 'permissions': sorted(permitted)}))
