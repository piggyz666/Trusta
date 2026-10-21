from dataclasses import dataclass
import json
from backend.llm.contracts import InferenceError
from llm import LLMResponse, LLMStructuredResponse
from llm.hashing import digest, snapshot
from schemas import task_node_schema, task_plan_schema


@dataclass(frozen=True, init=False)
class TaskNode:
    node_id: str
    agent: str
    action: str
    _parameters: str
    dependency: tuple[str, ...]

    def __init__(self, node_id, agent, action, parameters, dependency):
        raw = {'node_id': node_id, 'agent': agent, 'action': action,
               'parameters': parameters, 'dependency': list(dependency) if isinstance(dependency, (tuple, list)) else dependency}
        parsed = LLMStructuredResponse.parse(LLMResponse(snapshot(raw)), task_node_schema()).data
        for field in ('node_id', 'agent', 'action'):
            object.__setattr__(self, field, parsed[field])
        object.__setattr__(self, '_parameters', snapshot(parsed['parameters']))
        object.__setattr__(self, 'dependency', tuple(parsed['dependency']))

    @property
    def parameters(self):
        return json.loads(self._parameters)

    def as_dict(self):
        return {'node_id': self.node_id, 'agent': self.agent, 'action': self.action,
                'parameters': self.parameters, 'dependency': list(self.dependency)}


@dataclass(frozen=True)
class TaskPlan:
    task_id: str
    goal: str
    steps: tuple[TaskNode, ...]

    @classmethod
    def from_dict(cls, value):
        parsed = LLMStructuredResponse.parse(LLMResponse(snapshot(value)), task_plan_schema()).data
        return cls(parsed['task_id'], parsed['goal'], tuple(TaskNode(**n) for n in parsed['steps']))

    def as_dict(self):
        return {'task_id': self.task_id, 'goal': self.goal, 'steps': [n.as_dict() for n in self.steps]}

    @property
    def plan_hash(self):
        return digest(self.as_dict())


class TaskGraph:
    """Bounded deterministic DAG; parallel layers are readiness, not authorization."""
    def __init__(self, nodes):
        if not isinstance(nodes, (tuple, list)) or not 1 <= len(nodes) <= 32 or any(not isinstance(n, TaskNode) for n in nodes):
            raise InferenceError('LLM_PLAN_GRAPH_SCHEMA')
        self.nodes = tuple(nodes)
        ids = [n.node_id for n in nodes]
        if len(set(ids)) != len(ids):
            raise InferenceError('LLM_PLAN_DUPLICATE_NODE')
        known = set(ids)
        if any(set(n.dependency) - known for n in nodes):
            raise InferenceError('LLM_PLAN_UNKNOWN_DEPENDENCY')
        self._layers = self._sort()

    @classmethod
    def from_plan(cls, plan):
        return cls(plan.steps)

    def _sort(self):
        completed, remaining, layers, depth = set(), list(self.nodes), [], {}
        while remaining:
            ready = [n for n in remaining if set(n.dependency) <= completed]
            if not ready:
                raise InferenceError('LLM_PLAN_CYCLE')
            for n in ready:
                depth[n.node_id] = 1 + max((depth[d] for d in n.dependency), default=0)
                if depth[n.node_id] > 20:
                    raise InferenceError('LLM_PLAN_GRAPH_DEPTH')
            layers.append(tuple(ready))
            completed.update(n.node_id for n in ready)
            remaining = [n for n in remaining if n.node_id not in completed]
        return tuple(layers)

    def topological_sort(self):
        return tuple(n for layer in self._layers for n in layer)

    def parallel_groups(self):
        return self._layers
