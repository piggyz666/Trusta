from pathlib import Path
from backend.config import Config
from backend.service import TrustaService

def service(root):
    return TrustaService(Config(db_path=str(Path(root)/'bank.sqlite3'),banking_version=5,communication_version=3,signing_key=b'phase6-test-root-key-only-32-bytes',strong_auth_secret='test-secret'))

def seed_participants(s):
    # Explicit bank fixture provisioning, not a customer-facing state mutation API.
    with s.db.transaction() as conn:
        for user in ('alice','bob','carol'):
            conn.execute('INSERT OR IGNORE INTO customers VALUES(?,?)',(user,user))
            conn.execute("INSERT OR IGNORE INTO accounts VALUES(?,?,'CNY',100000,0,0)",('acct-'+user,user))
            conn.execute('INSERT OR IGNORE INTO payees VALUES(?,?,?,0)',('payee-collect-'+user,user,'Collector demo'))
            conn.execute("INSERT OR IGNORE INTO split_payee_routes VALUES(?,'acct-demo')",('payee-collect-'+user,))
