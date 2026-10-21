from contextlib import closing
from pathlib import Path
import json
import sqlite3
import time
import uuid
from backend.llm.contracts import InferenceError
from llm.hashing import digest, snapshot
from validator.plan_validator import ValidatedPlan, PlanValidator
from runtime.task_graph import TaskGraph

PREFERENCES = {'language': {'zh-CN', 'en'}, 'verbosity': {'concise', 'detailed'}}
ERRORS = {'LLM_TIMEOUT', 'LLM_PROVIDER_UNAVAILABLE', 'LLM_PROVIDER_PROTOCOL', 'LLM_REFUSED',
          'LLM_INCOMPLETE_OUTPUT', 'LLM_TOOL_CALL_FORBIDDEN', 'LLM_INVALID_OUTPUT', 'LLM_OUTPUT_SCHEMA',
          'LLM_PLAN_SCHEMA', 'LLM_PLAN_PERMISSION', 'LLM_PLAN_CONTEXT', 'LLM_PLAN_UNKNOWN_ACTION',
          'LLM_PLAN_ACTION_PERMISSION', 'LLM_PLAN_TASK_BINDING', 'LLM_PLAN_PARAMETERS',
          'LLM_PLAN_GROUNDED_VALIDATION', 'LLM_PLAN_DUPLICATE_NODE', 'LLM_PLAN_UNKNOWN_DEPENDENCY',
          'LLM_PLAN_CYCLE', 'LLM_PLAN_GRAPH_DEPTH', 'LLM_PLAN_UNKNOWN_AGENT'}


class MemoryStore:
    """No goals, input values, account IDs, plan IDs, tokens or free-text failures.

    SUCCESSFUL_PLAN means successful *validated planning*, not bank execution.
    Structural memory is never automatically injected into a prompt or permissions.
    """
    def __init__(self, path, *, max_records_per_scope=100):
        if type(max_records_per_scope) is not int or not 1 <= max_records_per_scope <= 1000:
            raise InferenceError('LLM_MEMORY_CONFIG')
        self.path = str(Path(path).resolve())
        self.limit = max_records_per_scope
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS planning_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                       'scope TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, created_ns INTEGER NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS planning_memory_scope ON planning_memory(scope,id)')

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute('PRAGMA synchronous=FULL')
        return db

    def _scope(self, scope):
        try:
            if not isinstance(scope, str) or str(uuid.UUID(scope)) != scope:
                raise ValueError()
        except (ValueError, AttributeError):
            raise InferenceError('LLM_MEMORY_SCOPE') from None
        return scope

    def _save(self, scope, kind, value):
        self._scope(scope)
        encoded = snapshot(value)
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            if kind == 'PREFERENCE':
                db.execute("DELETE FROM planning_memory WHERE scope=? AND kind='PREFERENCE'", (scope,))
            db.execute('INSERT INTO planning_memory(scope,kind,body,created_ns) VALUES(?,?,?,?)',
                       (scope, kind, encoded, time.time_ns()))
            db.execute("DELETE FROM planning_memory WHERE scope=? AND kind!='PREFERENCE' AND id NOT IN "
                       "(SELECT id FROM planning_memory WHERE scope=? AND kind!='PREFERENCE' ORDER BY id DESC LIMIT ?)",
                       (scope, scope, self.limit))
            db.commit()

    def save_preference(self, scope, preferences):
        if (not isinstance(preferences, dict) or not preferences or set(preferences) - set(PREFERENCES)
                or any(not isinstance(v, str) or v not in PREFERENCES[k] for k, v in preferences.items())):
            raise InferenceError('LLM_MEMORY_SENSITIVE_OR_UNSUPPORTED')
        self._save(scope, 'PREFERENCE', dict(preferences))

    def save_successful_plan(self, scope, validated):
        if not isinstance(validated, ValidatedPlan):
            raise InferenceError('LLM_MEMORY_UNVALIDATED_PLAN')
        # Positive projection from registered agent/actions; discard every other field.
        specs = PlanValidator().specifications()
        rows = validated.plan.steps
        TaskGraph(rows)
        positions = {n.node_id: f'N{i+1}' for i, n in enumerate(rows)}
        sanitized = []
        for item in rows:
            spec = specs.get((item.agent, item.action))
            if spec is None or any(d not in positions for d in item.dependency):
                raise InferenceError('LLM_MEMORY_UNVALIDATED_PLAN')
            sanitized.append({'node_id': positions[item.node_id], 'agent': spec.agent,
                'action': spec.permission_action, 'dependency': [positions[d] for d in item.dependency]})
        body = {'steps': sanitized, 'structure_hash': digest(sanitized),
                'validation_result': 'PASS', 'execution_status': 'NOT_RUN'}
        self._save(scope, 'SUCCESSFUL_PLAN', body)

    def save_failure(self, scope, error_code):
        if not isinstance(error_code, str) or error_code not in ERRORS:
            raise InferenceError('LLM_MEMORY_SENSITIVE_OR_UNSUPPORTED')
        self._save(scope, 'FAILURE', {'error_code': error_code})

    def read(self, scope):
        self._scope(scope)
        with closing(self._connect()) as db:
            return [{'kind': kind, 'value': json.loads(body)} for kind, body in db.execute(
                'SELECT kind,body FROM planning_memory WHERE scope=? ORDER BY id', (scope,))]

    def forget(self, scope):
        self._scope(scope)
        with closing(self._connect()) as db:
            db.execute('DELETE FROM planning_memory WHERE scope=?', (scope,))
