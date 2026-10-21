"""Durable LLM-only metadata journal. Not the banking Audit or an authorization store.

Hash chaining detects edits relative to a retained tail; it is not a signed proof
against an attacker who can rewrite the whole database and its trusted tail.
"""
from contextlib import contextmanager
from pathlib import Path
import json
import re
import sqlite3
import time
from backend.llm.configuration import valid_number
from backend.llm.contracts import InferenceError
from llm.hashing import digest, snapshot

ZERO_HASH = '0' * 64


class LLMTraceStore:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS llm_requests (
                    request_id TEXT PRIMARY KEY, status TEXT NOT NULL, prompt_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS llm_trace_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL, previous_hash TEXT NOT NULL, record_hash TEXT NOT NULL);
            ''')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    def _append(self, db, payload):
        tail = db.execute('SELECT record_hash FROM llm_trace_events ORDER BY seq DESC LIMIT 1').fetchone()
        previous = tail[0] if tail else ZERO_HASH
        encoded = snapshot(payload)
        record_hash = digest({'previous_hash': previous, 'payload': payload})
        db.execute('INSERT INTO llm_trace_events(payload, previous_hash, record_hash) VALUES(?,?,?)',
                   (encoded, previous, record_hash))
        return record_hash

    def claim(self, request_id, prompt_hash, request_hash, template_hash):
        if (not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id)
                or any(not isinstance(h, str) or not re.fullmatch(r'[0-9a-f]{64}', h)
                       for h in (prompt_hash, request_hash, template_hash))):
            raise InferenceError('LLM_INVALID_TRACE')
        try:
            with self._connection() as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    db.execute('INSERT INTO llm_requests VALUES(?,?,?)', (request_id, 'RUNNING', prompt_hash))
                    self._append(db, {'request_id': request_id, 'status': 'RUNNING',
                        'prompt_hash': prompt_hash, 'request_hash': request_hash,
                        'template_hash': template_hash, 'timestamp_ns': time.time_ns()})
                    db.commit()
                except sqlite3.IntegrityError:
                    db.rollback()
                    raise InferenceError('LLM_DUPLICATE_REQUEST') from None
        except sqlite3.Error:
            raise InferenceError('LLM_TRACE_UNAVAILABLE') from None

    def finish(self, request_id, *, status, provider, model, latency, prompt_hash,
               response_hash, attempts, error_code=None, validation_result=None):
        if (status not in {'SUCCEEDED', 'REJECTED', 'FAILED'} or
                not valid_number(latency, 0, 86400) or not isinstance(provider, str) or
                not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', provider) or
                not isinstance(model, str) or not 1 <= len(model) <= 128 or
                not isinstance(prompt_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', prompt_hash) or
                response_hash is not None and (not isinstance(response_hash, str) or
                                              not re.fullmatch(r'[0-9a-f]{64}', response_hash)) or
                error_code is not None and (not isinstance(error_code, str) or
                                            not re.fullmatch(r'LLM_[A-Z0-9_]{1,80}', error_code))):
            raise InferenceError('LLM_INVALID_TRACE')
        # Accept bounded metadata only. No prompt, response or credential content.
        if not isinstance(attempts, list) or not 1 <= len(attempts) <= 6:
            raise InferenceError('LLM_INVALID_TRACE')
        for attempt in attempts:
            if not isinstance(attempt, dict) or set(attempt) != {'provider', 'model', 'response_hash', 'error_code'}:
                raise InferenceError('LLM_INVALID_TRACE')
            if (not isinstance(attempt['provider'], str) or
                    not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', attempt['provider']) or
                    not isinstance(attempt['model'], str) or not 1 <= len(attempt['model']) <= 128 or
                    attempt['response_hash'] is not None and (not isinstance(attempt['response_hash'], str) or
                        not re.fullmatch(r'[0-9a-f]{64}', attempt['response_hash'])) or
                    attempt['error_code'] is not None and (not isinstance(attempt['error_code'], str) or
                        not re.fullmatch(r'LLM_[A-Z0-9_]{1,80}', attempt['error_code']))):
                raise InferenceError('LLM_INVALID_TRACE')
        payload = {'request_id': request_id, 'status': status, 'provider': provider, 'model': model,
                   'latency': latency, 'prompt_hash': prompt_hash, 'response_hash': response_hash,
                   'attempts': attempts, 'error_code': error_code, 'timestamp_ns': time.time_ns()}
        if validation_result is not None:
            if (not isinstance(validation_result, dict) or set(validation_result) != {'schema', 'plan'}
                    or any(v not in ('PASS', 'FAIL', 'NOT_RUN') for v in validation_result.values())):
                raise InferenceError('LLM_INVALID_TRACE')
            payload['validation_result'] = dict(validation_result)
        try:
            with self._connection() as db:
                db.execute('BEGIN IMMEDIATE')
                start = db.execute('SELECT prompt_hash FROM llm_requests WHERE request_id=?', (request_id,)).fetchone()
                if not start or start[0] != prompt_hash:
                    db.rollback()
                    raise InferenceError('LLM_TRACE_STATE_CONFLICT')
                if db.execute("UPDATE llm_requests SET status=? WHERE request_id=? AND status='RUNNING'",
                              (status, request_id)).rowcount != 1:
                    db.rollback()
                    raise InferenceError('LLM_TRACE_STATE_CONFLICT')
                tail = self._append(db, payload)
                db.commit()
                return tail
        except sqlite3.Error:
            raise InferenceError('LLM_TRACE_UNAVAILABLE') from None

    def events(self):
        with self._connection() as db:
            return [{'seq': row[0], **json.loads(row[1]), 'previous_hash': row[2], 'record_hash': row[3]}
                    for row in db.execute('SELECT * FROM llm_trace_events ORDER BY seq')]

    def unfinished(self):
        with self._connection() as db:
            return tuple(row[0] for row in db.execute(
                "SELECT request_id FROM llm_requests WHERE status='RUNNING' ORDER BY request_id"))

    def verify(self, expected_tail=None):
        try:
            with self._connection() as db:
                db.execute('BEGIN')  # consistent snapshot under concurrent appends
                previous, states, prompts, expected_seq = ZERO_HASH, {}, {}, 1
                for seq, encoded, prior, hashed in db.execute('SELECT * FROM llm_trace_events ORDER BY seq'):
                    payload = json.loads(encoded)
                    if seq != expected_seq or prior != previous or hashed != digest({'previous_hash': prior, 'payload': payload}):
                        return False
                    expected_seq += 1
                    rid, status = payload['request_id'], payload['status']
                    if rid not in states:
                        if status != 'RUNNING':
                            return False
                        prompts[rid] = payload['prompt_hash']
                    elif states[rid] != 'RUNNING' or status not in {'SUCCEEDED', 'REJECTED', 'FAILED'}:
                        return False
                    if payload['prompt_hash'] != prompts[rid]:
                        return False
                    states[rid], previous = status, hashed
                projection = dict(db.execute('SELECT request_id, status FROM llm_requests'))
                bindings = dict(db.execute('SELECT request_id, prompt_hash FROM llm_requests'))
                return states == projection and bindings == prompts and (expected_tail is None or expected_tail == previous)
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            return False
