"""Plan adapter into frozen GraphRuntime, with durable binding and native audit.

No direct bank write, authorization forgery, external provider or auto-approval.
"""
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import time
from dataclasses import asdict
from backend.llm.validation import strict_json
from llm.hashing import text_hash
from backend.config import Config
from backend.domain import Rejected, canonical, digest, uid
from backend.llm.contracts import InferenceError
from backend.service import TrustaService
from backend.security.local_files import write_secret_atomic, protect_secret
from runtime.task_graph import TaskPlan
from validator import PlanValidator, PlanningContext
from sandbox.permission import SandboxPermissions

SCOPE = 'LOCAL_VIRTUAL_BANK_ONLY'


class BankSandbox:
    def __init__(self, root):
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        marker = root / 'sandbox-manifest.json'
        if not marker.exists():
            if any(root.iterdir()):
                raise InferenceError('LLM_SANDBOX_DIRECTORY_NOT_EMPTY')
            write_secret_atomic(root / 'sandbox.key', secrets.token_bytes(32), create_only=True)
            marker.write_text(canonical({'scope': SCOPE, 'version': 1}), encoding='utf-8')
        if json.loads(marker.read_text(encoding='utf-8')) != {'scope': SCOPE, 'version': 1}:
            raise InferenceError('LLM_SANDBOX_SCOPE')
        protect_secret(root / 'sandbox.key')
        key = (root / 'sandbox.key').read_bytes()
        if len(key) != 32:
            raise InferenceError('LLM_SANDBOX_KEY')
        self.root = root
        self.service = TrustaService(Config(db_path=str(root / 'bank.sqlite3'), signing_key=key,
            banking_version=5, communication_version=3, safety_profile='competition',
            blockchain_anchor_enabled=False, crypto_key_dir=str(root / 'crypto-keys')))
        if self.service.gateway._adapter.external:
            raise InferenceError('LLM_SANDBOX_EXTERNAL_DENIED')
        self.permissions = SandboxPermissions()
        self.validator = PlanValidator(self.service.registry)
        with self.service.db.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS alignment_runs (id TEXT PRIMARY KEY, '
                       'request_id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL)')
        from sandbox.account_service import AccountService
        from sandbox.transfer_service import TransferService
        from sandbox.payment_service import PaymentService
        self.accounts, self.transfers, self.payments = AccountService(self), TransferService(self), PaymentService(self)

    def _seal(self, body):
        return hmac.new(self.service.config.signing_key, b'trusta-alignment-v1\0' + canonical(body).encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def payload(record):
        return {k: v for k, v in record.items() if k not in {'seal', 'audit_event_id'}}

    def _save(self, record, *, expected_status=None, expected_runtime_revision=None):
        value = self.payload(record)
        value['timestamp'] = int(time.time() * 1000)
        with self.service.db.transaction() as db:
            if expected_runtime_revision is not None:
                task_row = db.execute('SELECT body FROM tasks WHERE id=?', (value['runtime_task_id'],)).fetchone()
                if not task_row or json.loads(task_row[0])['revision'] != expected_runtime_revision:
                    raise InferenceError('LLM_SANDBOX_STALE_RUNTIME')
            event = self.service.audit.append_in(db, value.get('runtime_task_id'), None,
                'EXECUTION_ALIGNMENT_' + value['status'], 'alignment_adapter', value)
            result = value | {'audit_event_id': event}
            result['seal'] = self._seal(result)
            sql = 'UPDATE alignment_runs SET status=?,body=? WHERE id=?'
            args = (value['status'], canonical(result), value['execution_id'])
            if expected_status is not None:
                sql += ' AND status=?'
                args += (expected_status,)
            if db.execute(sql, args).rowcount != 1:
                raise InferenceError('LLM_SANDBOX_STATE_CONFLICT')
        return result

    def get(self, execution_id, user_id):
        row = self.service.db.one('SELECT * FROM alignment_runs WHERE id=?', (execution_id,))
        if not row or row['owner'] != user_id:
            raise InferenceError('LLM_SANDBOX_ACCESS_DENIED')
        body = json.loads(row['body'])
        if (not isinstance(body.get('seal'), str) or body.get('execution_id') != execution_id or body.get('user_id') != user_id
                or body.get('status') != row['status'] or not hmac.compare_digest(
                    body.get('seal', ''), self._seal({k: v for k, v in body.items() if k != 'seal'}))):
            raise InferenceError('LLM_SANDBOX_STORAGE_TAMPERED')
        return body

    def prepare(self, adapter, request, user_id='demo'):
        # This is a trusted controller entry. Providers receive no sandbox handle.
        request.validate()
        if user_id != 'demo':
            raise InferenceError('LLM_SANDBOX_ACCESS_DENIED')
        self.permissions.check_context(request.context)
        execution_id = uid()
        base = {'execution_id': execution_id, 'request_id': request.request_id, 'user_id': user_id,
            'scope': SCOPE, 'status': 'PLANNING', 'result': None, 'agent_id': 'planner',
            'context': {'task_id': request.context.task_id, 'inputs': request.context.inputs,
                        'allowed_actions': sorted(request.context.allowed_actions)}}
        try:
            with self.service.db.transaction() as db:
                db.execute('INSERT INTO alignment_runs VALUES(?,?,?,?,?)',
                           (execution_id, request.request_id, user_id, 'PLANNING', '{}'))
        except sqlite3.IntegrityError:
            raise InferenceError('LLM_DUPLICATE_REQUEST') from None
        self._save(base)
        try:
            validated = adapter.generate(request)
            # Reparse and revalidate even if an adapter supplied a forged typed object.
            validated = self.validator.validate(validated.plan, request.context)
            trace = adapter.trace.events()
            terminal = next(e for e in reversed(trace) if e['request_id'] == request.request_id)
            response = adapter.runtime.responses.get(request.request_id)
            if (terminal['status'] != 'SUCCEEDED' or terminal.get('validation_result') != {'schema': 'PASS', 'plan': 'PASS'}
                    or not adapter.trace.verify(trace[-1]['record_hash']) or response is None
                    or text_hash(response.content) != terminal['response_hash']
                    or (not adapter.verify_response_binding(response, request, validated)
                        if hasattr(adapter, 'verify_response_binding') else
                        strict_json(response.content) != validated.plan.as_dict())):
                raise InferenceError('LLM_ALIGNMENT_TRACE_BINDING')
            native_id = self.service.supervisor.receive(user_id, validated.plan.goal,
                {'task_type': 'GENERAL_GRAPH', 'needs_clarification': False})
            return self._save(base | {'status': 'VALIDATED', 'plan': validated.plan.as_dict(),
                'plan_hash': validated.plan.plan_hash, 'context_hash': validated.context_hash,
                'runtime_task_id': native_id, 'llm_events': trace, 'llm_trace_tail': trace[-1]['record_hash'],
                'request_hash': next(e['request_hash'] for e in trace if e['request_id'] == request.request_id),
                'provider': terminal['provider'], 'model': terminal['model'], 'latency': terminal['latency'],
                'source': adapter.source, 'llm_response':asdict(response), 'validation_result': 'PASS'})
        except (InferenceError, Rejected) as error:
            events = adapter.trace.events()
            terminal = next((e for e in reversed(events) if e['request_id'] == request.request_id), {})
            return self._save(base | {'status': 'REJECTED', 'result': error.code, 'validation_result': 'FAIL',
                'request_hash': next((e.get('request_hash') for e in events if e['request_id'] == request.request_id), None),
                'provider': terminal.get('provider', adapter.config.name), 'model': terminal.get('model', adapter.config.model),
                'latency': terminal.get('latency'), 'source': adapter.source,
                'llm_events': events, 'llm_trace_tail': events[-1]['record_hash'] if events else None})

    def _validated(self, record):
        c = record['context']
        validated = self.validator.validate(TaskPlan.from_dict(record['plan']),
            PlanningContext(c['task_id'], c['inputs'], c['allowed_actions']))
        if validated.plan.plan_hash != record['plan_hash'] or validated.context_hash != record['context_hash']:
            raise InferenceError('LLM_ALIGNMENT_PLAN_BINDING')
        return validated

    def start(self, execution_id, user_id):
        record = self.get(execution_id, user_id)
        if record['status'] != 'VALIDATED':
            raise InferenceError('LLM_SANDBOX_START_STATE')
        validated = self._validated(record)
        record = self._save(record | {'status': 'STARTING'}, expected_status='VALIDATED')
        try:
            task = self.service.graphs.create(user_id, validated.plan.goal, validated.graph,
                record['context']['inputs'], record['runtime_task_id'])
        except Rejected as error:
            return self._save(record | {'status': 'BLOCKED', 'result': error.code})
        return self._capture(record, task)

    def resume(self, execution_id, user_id):
        record = self.get(execution_id, user_id)
        if record['status'] not in {'STARTING', 'WAITING_USER', 'WAITING_TIME', 'RUNNING'}:
            raise InferenceError('LLM_SANDBOX_RESUME_STATE')
        self._validated(record)
        task = self.service._owned_task(record['runtime_task_id'], user_id)
        if not task.get('graph_state'):
            # An interrupted installation must not be interpreted as an executed action.
            return self._save(record | {'status': 'MANUAL_REVIEW', 'result': 'INSTALLATION_UNCERTAIN'})
        return self._capture(record, self.service.graphs.advance(task['task_id'], user_id))

    def authorize(self, execution_id, user_id, node_id, action_hash, approve):
        record = self.get(execution_id, user_id)
        if record['status'] != 'WAITING_USER' or type(approve) is not bool:
            raise InferenceError('LLM_SANDBOX_APPROVAL_STATE')
        self._validated(record)
        task = self.service.graphs.authorize(record['runtime_task_id'], user_id, node_id, action_hash, approve)
        return self._capture(record, task)

    def _capture(self, record, task, remaining=3):
        validated = self._validated(record)
        state = self.service.graphs._state(task)
        if state['graph'] != validated.graph or state['inputs'] != record['context']['inputs']:
            raise InferenceError('LLM_ALIGNMENT_RUNTIME_BINDING')
        values = state['inputs'] | state['outputs']
        nodes = {n['node_id']: n for n in state['graph']['nodes']}
        observed = []
        for step in task['steps']:
            item = {'step_id': step['step_id'], 'owner': step['owner'], 'name': step['name'], 'status': step['status'],
                    'dependencies': step['dependencies']}
            n = nodes.get(step['step_id'])
            if n:
                item['parameters'] = {k: values[v] for k, v in n['parameters'].items() if v in values}
                item['capability'] = n['capability']
                action_state = state['actions'].get(step['step_id'])
                if action_state and action_state.get('action'):
                    item['action_id'] = action_state['action']['action_id']
                    if n['capability'] == 'transfer.propose':
                        item['recipient_evidence'] = self.service.transaction.recipient(task['user_id'], item['parameters']['recipient_name'])
            if step['step_id'] + ':result' in state['outputs']:
                item['output_hash'] = digest(state['outputs'][step['step_id'] + ':result'])
            observed.append(item)
        execution = {'task_id': task['task_id'], 'status': task['status'], 'runtime_revision':task['revision'], 'steps': observed,
                     'actions': task['action_records'], 'receipts': task['receipts']}
        try:
            return self._save(record | {'status': task['status'], 'execution': execution,
                'execution_hash': digest(execution), 'result': task['status']}, expected_runtime_revision=task['revision'])
        except InferenceError as error:
            if error.code != 'LLM_SANDBOX_STALE_RUNTIME' or remaining <= 0:
                raise
            return self._capture(record, self.service._owned_task(task['task_id'], task['user_id']), remaining - 1)

    def export(self, execution_id, user_id):
        record = self.get(execution_id, user_id)
        while self.service.checkpoints.generate_checkpoint(4096):
            pass
        proof = self.service.checkpoints.generate_inclusion_proof(record['audit_event_id'])
        key, _ = self.service.checkpoints.public_key(proof['checkpoint'])
        pinned = self.root / 'audit-public-key.pem'
        if pinned.exists() and pinned.read_text(encoding='utf-8') != key:
            raise InferenceError('LLM_ALIGNMENT_PIN_CHANGED')
        if not pinned.exists():
            pinned.write_text(key, encoding='utf-8')
        receipts = [self.service.records.query(user_id, receipt_id=r['receipt_id'])
                    for r in record.get('execution', {}).get('receipts', [])]
        return {'schema_version': 1, 'scope': SCOPE, 'record': self.payload(record),
                'audit_proof': proof, 'receipt_proofs': receipts}
