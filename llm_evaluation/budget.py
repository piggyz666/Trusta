"""Durable, pessimistic pre-dispatch reservations. No refunds or implicit retry."""
from pathlib import Path
import sqlite3
import math
from contextlib import closing
from backend.llm.contracts import InferenceError
from llm.hashing import snapshot, digest


class LiveBudget:
    def __init__(self, path, *, approved=False, max_requests=60, max_usd=1):
        if (approved is not True or type(max_requests) is not int or not 1 <= max_requests <= 60
                or type(max_usd) not in (int, float) or not math.isfinite(max_usd) or not 0 < max_usd <= 1):
            raise InferenceError('LLM_BUDGET_APPROVAL_REQUIRED')
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db:
            db.execute('CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY CHECK(id=1), max_requests INTEGER, max_microusd INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS reservations (request_id TEXT PRIMARY KEY, provider TEXT, microusd INTEGER, request_hash TEXT)')
            db.execute('INSERT OR IGNORE INTO budget VALUES(1,?,?)', (max_requests, int(max_usd * 1_000_000)))
            bounds = db.execute('SELECT max_requests,max_microusd FROM budget').fetchone()
            if bounds != (max_requests, int(max_usd * 1_000_000)):
                raise InferenceError('LLM_BUDGET_IMMUTABLE')

    def _db(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        return db

    def reserve(self, request_id, provider, messages, output_tokens, *, schema=None):
        # $2 / M input, $4 / M output: conservative caps above verified configured text-model rates.
        # Byte bound + protocol overhead exceeds the normal input token count.
        if type(output_tokens) is not int or not 1 <= output_tokens <= 4096:
            raise InferenceError('LLM_BUDGET_BOUND')
        # Native schema is a second input on the wire even when also in the prompt.
        schema_bytes = len(snapshot(schema).encode()) if schema is not None else 0
        cost = (len(snapshot(messages).encode()) + schema_bytes + 4096) * 2 + output_tokens * 4
        with closing(self._db()) as db:
            db.execute('BEGIN IMMEDIATE')
            limits = db.execute('SELECT max_requests,max_microusd FROM budget').fetchone()
            count, used = db.execute('SELECT count(*),coalesce(sum(microusd),0) FROM reservations').fetchone()
            if count >= limits[0] or used + cost > limits[1]:
                raise InferenceError('LLM_BUDGET_EXHAUSTED')
            try:
                db.execute('INSERT INTO reservations VALUES(?,?,?,?)', (request_id, provider, cost, digest(messages)))
            except sqlite3.IntegrityError:
                raise InferenceError('LLM_BUDGET_DUPLICATE') from None
            db.commit()
        return cost / 1_000_000

    def status(self):
        with closing(self._db()) as db:
            count, used = db.execute('SELECT count(*),coalesce(sum(microusd),0) FROM reservations').fetchone()
        return {'requests': count, 'reserved_usd': used / 1_000_000, 'actual_billed_usd': None,
                'input_cap_usd_per_million': 2, 'output_cap_usd_per_million': 4, 'refunds': 0}
