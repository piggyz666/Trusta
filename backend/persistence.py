from contextlib import contextmanager
import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers(id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS split_payee_routes(payee_id TEXT PRIMARY KEY REFERENCES payees(id), account_id TEXT NOT NULL REFERENCES accounts(id));
CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, customer_id TEXT, currency TEXT NOT NULL,
 balance INTEGER NOT NULL CHECK(balance >= 0), frozen INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS payees(id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, name TEXT NOT NULL, is_new INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS transfers(id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
 action_hash TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transactions(id TEXT PRIMARY KEY, transfer_id TEXT NOT NULL UNIQUE,
 source_account_id TEXT NOT NULL, recipient_id TEXT NOT NULL, amount INTEGER NOT NULL,
 currency TEXT NOT NULL, status TEXT NOT NULL, before_balance INTEGER NOT NULL,
 after_balance INTEGER NOT NULL, account_version INTEGER NOT NULL, executed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ledger_entries(id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL,
 account_id TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN ('DEBIT','CREDIT')), amount INTEGER NOT NULL CHECK(amount > 0));
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, owner TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_memory(id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
 memory_key TEXT NOT NULL, expires_at REAL NOT NULL, body TEXT NOT NULL, UNIQUE(owner,kind,memory_key));
CREATE INDEX IF NOT EXISTS memory_owner_expiry ON agent_memory(owner,expires_at);
CREATE TABLE IF NOT EXISTS message_security_state(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS message_key_versions(version INTEGER PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crypto_trust_state(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crypto_identities(agent_id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS crypto_keys(key_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
 key_version INTEGER NOT NULL, body TEXT NOT NULL, UNIQUE(agent_id,key_version));
CREATE TABLE IF NOT EXISTS audit_checkpoints(id TEXT PRIMARY KEY, first_seq INTEGER NOT NULL,
 last_seq INTEGER NOT NULL UNIQUE, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_checkpoint_leaves(event_id TEXT PRIMARY KEY, checkpoint_id TEXT NOT NULL
 REFERENCES audit_checkpoints(id), sequence INTEGER NOT NULL UNIQUE, leaf_index INTEGER NOT NULL,
 event_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_anchor_jobs(checkpoint_id TEXT PRIMARY KEY REFERENCES audit_checkpoints(id),
 status TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS local_audit_anchors(anchor_ref TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE,
 checkpoint_hash TEXT NOT NULL, body TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS checkpoint_no_update BEFORE UPDATE ON audit_checkpoints BEGIN SELECT RAISE(ABORT,'immutable checkpoint'); END;
CREATE TRIGGER IF NOT EXISTS checkpoint_no_delete BEFORE DELETE ON audit_checkpoints BEGIN SELECT RAISE(ABORT,'immutable checkpoint'); END;
CREATE TRIGGER IF NOT EXISTS checkpoint_leaf_no_update BEFORE UPDATE ON audit_checkpoint_leaves BEGIN SELECT RAISE(ABORT,'immutable leaf'); END;
CREATE TRIGGER IF NOT EXISTS checkpoint_leaf_no_delete BEFORE DELETE ON audit_checkpoint_leaves BEGIN SELECT RAISE(ABORT,'immutable leaf'); END;
CREATE TABLE IF NOT EXISTS conversation_turns(conversation_id TEXT NOT NULL REFERENCES conversations(id),
 id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(conversation_id,id));
CREATE TABLE IF NOT EXISTS conversation_llm_calls(id TEXT PRIMARY KEY,
 conversation_id TEXT NOT NULL REFERENCES conversations(id), owner TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_calls(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 owner TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS llm_calls_task ON llm_calls(task_id);
CREATE TABLE IF NOT EXISTS task_steps(task_id TEXT NOT NULL, step_id TEXT NOT NULL, status TEXT NOT NULL,
 PRIMARY KEY(task_id,step_id));
CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS policy_decisions(id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS authorizations(id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS execution_contracts(id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS processed_idempotency_keys(key TEXT PRIMARY KEY, action_hash TEXT NOT NULL, transaction_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS replay_nonces(sender TEXT NOT NULL, nonce TEXT NOT NULL, timestamp REAL NOT NULL,
 PRIMARY KEY(sender,nonce));
CREATE TABLE IF NOT EXISTS consumption_records(id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
 merchant TEXT NOT NULL, category TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount > 0), currency TEXT NOT NULL,
 occurred_at REAL NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('PURCHASE','REFUND')), status TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS consumption_account_month ON consumption_records(account_id,occurred_at);
CREATE TABLE IF NOT EXISTS risk_profiles(customer_id TEXT PRIMARY KEY REFERENCES customers(id),
 risk_level INTEGER NOT NULL CHECK(risk_level BETWEEN 1 AND 5), assessed_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS wealth_products(id TEXT PRIMARY KEY, name TEXT NOT NULL, risk_level INTEGER NOT NULL CHECK(risk_level BETWEEN 1 AND 5),
 minimum_amount INTEGER NOT NULL CHECK(minimum_amount > 0), term_days INTEGER NOT NULL CHECK(term_days >= 0), currency TEXT NOT NULL,
 active INTEGER NOT NULL CHECK(active IN (0,1)), risk_note TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'append only'); END;
"""


class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # The dedicated database directory also contains SQLite WAL/SHM files.
        # Protect it before opening SQLite so newly created sidecars inherit a
        # private ACL; securing only the main DB would leave bank data exposed.
        from backend.security.local_files import protect_secret
        protect_secret(Path(path).parent)
        if Path(path).exists():protect_secret(path)
        db = self.connect()
        try:
            db.executescript(SCHEMA)
            from backend.banking.extended_schema import SCHEMA as BANKING_SCHEMA
            db.executescript(BANKING_SCHEMA)
        finally:
            db.close()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, args=()):
        db = self.connect()
        try:
            return [dict(row) for row in db.execute(sql, args).fetchall()]
        finally:
            db.close()

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None
