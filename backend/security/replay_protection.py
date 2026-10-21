import sqlite3
from backend.domain import Rejected


class ReplayProtection:
    def __init__(self, db):
        self.db = db

    def consume(self, sender, nonce, timestamp):
        if not isinstance(nonce, str) or not nonce:
            raise Rejected("INVALID_NONCE")
        try:
            with self.db.transaction() as db:
                db.execute("INSERT INTO replay_nonces VALUES(?,?,?)", (sender, nonce, timestamp))
        except sqlite3.IntegrityError:
            raise Rejected("REPLAY_BLOCKED")

    def consume_in(self, conn, sender, nonce, timestamp):
        try:
            conn.execute("INSERT INTO replay_nonces VALUES(?,?,?)", (sender, nonce, timestamp))
        except sqlite3.IntegrityError:
            raise Rejected("REPLAY_BLOCKED") from None
