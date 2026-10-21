"""Deterministic banking safety. No Agent capability and no bank write API."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken
from backend.domain import Rejected, canonical, digest, uid


SCHEMA = """
CREATE TABLE IF NOT EXISTS banking_safety_profile(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS banking_security_users(owner TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS banking_totp(owner TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS banking_daily_claims(idempotency_key TEXT PRIMARY KEY, owner TEXT NOT NULL,
 currency TEXT NOT NULL, day TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>0), body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS daily_claim_owner ON banking_daily_claims(owner,currency,day);
CREATE TABLE IF NOT EXISTS banking_review_cases(task_id TEXT PRIMARY KEY, owner TEXT NOT NULL, body TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS safety_profile_no_delete BEFORE DELETE ON banking_safety_profile BEGIN SELECT RAISE(ABORT,'security profile cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS security_user_no_delete BEFORE DELETE ON banking_security_users BEGIN SELECT RAISE(ABORT,'security state cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS totp_no_delete BEFORE DELETE ON banking_totp BEGIN SELECT RAISE(ABORT,'MFA cannot be reset by deleting enrollment'); END;
CREATE TRIGGER IF NOT EXISTS daily_claim_no_delete BEFORE DELETE ON banking_daily_claims BEGIN SELECT RAISE(ABORT,'daily quota is immutable'); END;
CREATE TRIGGER IF NOT EXISTS daily_claim_no_update BEFORE UPDATE ON banking_daily_claims BEGIN SELECT RAISE(ABORT,'daily quota is immutable'); END;
"""


def hotp(secret, counter, digits=6):
    """RFC 4226 dynamic truncation; production enrollment uses 160 random bits."""
    value = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = value[-1] & 15
    truncated = struct.unpack(">I", value[offset:offset+4])[0] & 0x7fffffff
    return str(truncated % (10 ** digits)).zfill(digits)


class BankingControls:
    def __init__(self, db, audit, config):
        self.db, self.audit, self.config = db, audit, config
        if (config.safety_profile not in {"legacy", "competition"}
                or any(type(n) is not int or n <= 0 for n in
                       (config.daily_transfer_limit, config.authentication_failure_limit, config.execution_failure_limit))):
            raise Rejected("INVALID_SAFETY_CONFIGURATION")
        self.enabled = config.safety_profile == "competition"
        self.key = hmac.new(config.signing_key, b"banking-safety-v1", hashlib.sha256).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(self.key))
        with db.transaction() as conn:
            conn.executescript(SCHEMA)
        with db.transaction() as conn:
            row = conn.execute("SELECT body FROM banking_safety_profile WHERE id=1").fetchone()
            if row:
                profile = self._load(row["body"])
                if profile != {"profile": "competition"}:
                    raise Rejected("SECURITY_STATE_INVALID")
                if not self.enabled:
                    raise Rejected("SAFETY_DOWNGRADE_DENIED")
            elif self.enabled:
                conn.execute("INSERT INTO banking_safety_profile VALUES(1,?)", (self._pack({"profile":"competition"}),))
                audit.append_in(conn, None, None, "BANKING_SAFETY_ENABLED", "security", {"profile":"competition"})

    def _pack(self, body):
        return canonical({"body":body, "seal":hmac.new(self.key, canonical(body).encode(), hashlib.sha256).hexdigest()})

    def _load(self, value):
        try:
            record = json.loads(value)
            expected = hmac.new(self.key, canonical(record["body"]).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, record["seal"]):
                raise ValueError()
            return record["body"]
        except (ValueError, KeyError, TypeError):
            raise Rejected("SECURITY_STATE_INVALID") from None

    def _user(self, conn, owner):
        row = conn.execute("SELECT body FROM banking_security_users WHERE owner=?", (owner,)).fetchone()
        state = self._load(row["body"]) if row else {"owner":owner, "locked":False, "authentication_failures":0,
                                                   "execution_failures":0, "reason":None}
        if state["owner"] != owner:
            raise Rejected("SECURITY_STATE_INVALID")
        return state

    def _save_user(self, conn, state):
        conn.execute("INSERT INTO banking_security_users VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET body=excluded.body", (state["owner"], self._pack(state)))

    def assert_unlocked(self, owner, conn=None):
        if not self.enabled:
            return
        if conn is None:
            with self.db.transaction() as current:
                return self.assert_unlocked(owner, current)
        if self._user(conn, owner)["locked"]:
            raise Rejected("BANKING_SECURITY_LOCKED")

    def _failure(self, conn, owner, kind, reason):
        state = self._user(conn, owner)
        key = "authentication_failures" if kind == "authentication" else "execution_failures"
        state[key] += 1
        limit = self.config.authentication_failure_limit if kind == "authentication" else self.config.execution_failure_limit
        state["locked"] = state["locked"] or state[key] >= limit
        state["reason"] = reason
        self._save_user(conn, state)
        self.audit.append_in(conn, None, None, "BANKING_SECURITY_LOCKED" if state["locked"] else "BANKING_SECURITY_FAILURE",
                             "security", {"owner":owner, "kind":kind, "reason":reason, "count":state[key]})

    def execution_failed(self, owner, reason):
        if self.enabled:
            with self.db.transaction() as conn:
                self._failure(conn, owner, "execution", reason)

    def verification_result(self, owner, verification):
        # Only a trusted HardVerifier's definite state mismatch triggers this.
        # Timeout, missing evidence and model anomaly suggestions are not fraud facts.
        if (self.enabled and verification.get('status')!='VERIFIED'
                and verification.get('reason')=='AUTHORITATIVE_STATE_MISMATCH'):
            with self.db.transaction() as conn:
                state=self._user(conn,owner)
                state.update(locked=True,reason='AUTHORITATIVE_STATE_MISMATCH')
                self._save_user(conn,state)
                self.audit.append_in(conn,None,None,'BANKING_SECURITY_LOCKED','security',
                                     {'owner':owner,'kind':'authoritative_verification','reason':state['reason']})

    def execution_succeeded(self, owner):
        if self.enabled:
            with self.db.transaction() as conn:
                state=self._user(conn,owner)
                if state['execution_failures']:
                    state['execution_failures']=0
                    self._save_user(conn,state)
                    self.audit.append_in(conn,None,None,'BANKING_EXECUTION_FAILURES_RESET','security',{'owner':owner})

    def status(self, owner):
        with self.db.transaction() as conn:
            state = self._user(conn, owner)
            row = conn.execute("SELECT body FROM banking_totp WHERE owner=?", (owner,)).fetchone()
            enrolled = bool(row and self._load(row["body"])["active"])
        return {**state, "profile":self.config.safety_profile, "totp_enrolled":enrolled,
                "daily_transfer_limit_minor":self.config.daily_transfer_limit, "daily_limit_currency":"CNY",
                "daily_timezone":"Asia/Shanghai", "foreign_currency_policy":"STRONG_AUTH",
                "quota_scope":"TRUSTA initiated transfers; not all activity at external banks"}

    def begin_enrollment(self, owner):
        if not self.enabled:
            raise Rejected("COMPETITION_SAFETY_DISABLED")
        secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
        with self.db.transaction() as conn:
            self.assert_unlocked(owner, conn)
            old = conn.execute("SELECT body FROM banking_totp WHERE owner=?", (owner,)).fetchone()
            if old and self._load(old["body"])["active"]:
                raise Rejected("TOTP_ALREADY_ENROLLED")
            # A pending enrollment cannot be replaced to reset its deadline/failure counter.
            if old and self._load(old["body"])["expires_at"] > time.time():
                raise Rejected("TOTP_ENROLLMENT_PENDING")
            body = {"owner":owner, "active":False, "secret":self.cipher.encrypt(canonical({"owner":owner,"secret":secret}).encode()).decode(),
                    "expires_at":time.time()+300, "last_counter":-1}
            conn.execute("INSERT INTO banking_totp VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET body=excluded.body", (owner,self._pack(body)))
            self.audit.append_in(conn,None,None,"TOTP_ENROLLMENT_STARTED","security",{"owner":owner})
        return {"secret":secret, "expires_at":body["expires_at"],
                "otpauth_uri":"otpauth://totp/"+quote("TRUSTA:"+owner,safe="")+"?"+urlencode(
                    {"secret":secret,"issuer":"TRUSTA","algorithm":"SHA1","digits":6,"period":30})}

    def _verify_code(self, conn, owner, code, enrollment=False):
        self.assert_unlocked(owner, conn)
        row = conn.execute("SELECT body FROM banking_totp WHERE owner=?", (owner,)).fetchone()
        if not row:
            return None, "TOTP_NOT_ENROLLED"
        body = self._load(row["body"])
        if body["owner"] != owner:
            raise Rejected("SECURITY_STATE_INVALID")
        if not body["active"] and (not enrollment or time.time() >= body["expires_at"]):
            return None, "TOTP_NOT_ENROLLED"
        try:
            decrypted = json.loads(self.cipher.decrypt(body["secret"].encode()))
            if decrypted["owner"] != owner:
                raise ValueError()
            secret = base64.b32decode(decrypted["secret"]+"="*((-len(decrypted["secret"]))%8))
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise Rejected("SECURITY_STATE_INVALID") from None
        counter = int(time.time()//30)
        candidates = [n for n in (counter, counter-1, counter+1) if n >= 0]
        match = next((n for n in candidates if isinstance(code,str) and len(code)==6 and code.isascii() and code.isdigit()
                      and hmac.compare_digest(hotp(secret,n),code)),None)
        error = "TOTP_INVALID" if match is None else "TOTP_REPLAY" if match <= body["last_counter"] else None
        if error:
            self._failure(conn, owner, "authentication", error)
            return None,error
        body["active"], body["last_counter"] = True, match
        conn.execute("UPDATE banking_totp SET body=? WHERE owner=?", (self._pack(body),owner))
        state = self._user(conn,owner)
        state["authentication_failures"] = 0
        self._save_user(conn,state)
        return match,None

    def confirm_enrollment(self, owner, code):
        with self.db.transaction() as conn:
            row = conn.execute("SELECT body FROM banking_totp WHERE owner=?",(owner,)).fetchone()
            if row and self._load(row["body"])["active"]:
                raise Rejected("TOTP_ALREADY_ENROLLED")
            counter,error = self._verify_code(conn,owner,code,enrollment=True)
            if not error:
                self.audit.append_in(conn,None,None,"TOTP_ENROLLED","security",{"owner":owner})
        if error:
            raise Rejected(error)
        return {"totp_enrolled":True}

    def verify_authorization(self, conn, auth, action, code):
        counter,error = self._verify_code(conn,auth["user_id"],code)
        if error:
            return None,error
        proof = {"authorization_id":auth["authorization_id"],"owner":auth["user_id"],"action_hash":digest(action),
                 "verified_at":time.time(),"expires_at":auth["expires_at"],"counter":counter,"method":"totp"}
        self.audit.append_in(conn,action["task_id"],action["action_id"],"STRONG_AUTH_VERIFIED","security",proof)
        return self._pack(proof),None

    def strong_valid(self, auth):
        if not self.enabled:
            return auth.get("auth_method")=="sandbox_secret"
        if auth.get("auth_method")!="totp" or not auth.get("strong_auth_proof"):
            return False
        proof = self._load(auth["strong_auth_proof"])
        return (proof["authorization_id"]==auth["authorization_id"] and proof["owner"]==auth["user_id"]
                and proof["action_hash"]==auth["action_hash"] and proof["expires_at"]==auth["expires_at"]
                and auth["created_at"] <= proof["verified_at"] < auth["expires_at"] and proof["method"]=="totp")

    def _day(self):
        now = datetime.fromtimestamp(time.time(),timezone(timedelta(hours=8)))
        start = now.replace(hour=0,minute=0,second=0,microsecond=0)
        return now.date().isoformat(), start.timestamp(),(start+timedelta(days=1)).timestamp()

    def _daily(self, conn, action):
        day,start,end = self._day()
        owner,currency = action["customer_id"],action["currency"]
        total = 0
        for row in conn.execute("SELECT * FROM banking_daily_claims WHERE owner=? AND currency=? AND day=?",(owner,currency,day)):
            claim = self._load(row["body"])
            if any(claim[k]!=row[k] for k in ("idempotency_key","owner","currency","day","amount")):
                raise Rejected("SECURITY_STATE_INVALID")
            if row["idempotency_key"]!=action["idempotency_key"]:
                total += row["amount"]
        # Include pre-upgrade local transfers, without counting registered claims twice.
        total += conn.execute("""SELECT COALESCE(SUM(t.amount),0) FROM transactions t JOIN accounts a ON a.id=t.source_account_id
          JOIN transfers f ON f.id=t.transfer_id WHERE a.customer_id=? AND t.currency=? AND t.executed_at>=? AND t.executed_at<?
          AND f.idempotency_key!=? AND NOT EXISTS(SELECT 1 FROM banking_daily_claims q WHERE q.idempotency_key=f.idempotency_key)""",
          (owner,currency,start,end,action["idempotency_key"])).fetchone()[0]
        return day,total

    def risk(self, action, conn=None):
        if not self.enabled or action["action_type"]!="TRANSFER":
            return None
        if conn is None:
            with self.db.transaction() as current:
                return self.risk(action,current)
        day,total = self._daily(conn,action)
        strong = action["currency"]!="CNY" or total+action["amount"]>self.config.daily_transfer_limit
        return {"risk_level":"RED" if strong else "YELLOW", "requires_strong_auth":strong,
                "day":day,"currency":action["currency"],"prior_amount_minor":total,
                "projected_amount_minor":total+action["amount"],"scope":"TRUSTA_INITIATED"}

    def before_execution(self, action, auth):
        if not self.enabled:
            return
        with self.db.transaction() as conn:
            self.assert_unlocked(action["customer_id"],conn)
            risk = self.risk(action,conn)
            if risk and risk["requires_strong_auth"] and not self.strong_valid(auth):
                raise Rejected("DAILY_STRONG_AUTH_REQUIRED")
            if risk:
                row=conn.execute("SELECT body FROM banking_daily_claims WHERE idempotency_key=?",(action["idempotency_key"],)).fetchone()
                if row:
                    if self._load(row["body"])["action_hash"]!=digest(action):
                        raise Rejected("SECURITY_CLAIM_CONFLICT")
                    return
                claim={"idempotency_key":action["idempotency_key"],"owner":action["customer_id"],"currency":action["currency"],
                       "day":risk["day"],"amount":action["amount"],"action_hash":digest(action),"created_at":time.time()}
                conn.execute("INSERT INTO banking_daily_claims VALUES(?,?,?,?,?,?)",tuple(claim[k] for k in
                             ("idempotency_key","owner","currency","day","amount"))+(self._pack(claim),))
                self.audit.append_in(conn,action["task_id"],action["action_id"],"DAILY_TRANSFER_RESERVED","security",claim)

    def unlock(self, owner, operator_token, reason):
        if (not self.config.security_operator_token or not isinstance(operator_token,str)
                or not hmac.compare_digest(operator_token.encode(),self.config.security_operator_token.encode())):
            raise Rejected("SECURITY_OPERATOR_REQUIRED")
        if not isinstance(reason,str) or not 5<=len(reason.strip())<=500:
            raise Rejected("REVIEW_REASON_REQUIRED")
        with self.db.transaction() as conn:
            state=self._user(conn,owner)
            state.update(locked=False,authentication_failures=0,execution_failures=0,reason=None)
            self._save_user(conn,state)
            self.audit.append_in(conn,None,None,"BANKING_SECURITY_UNLOCKED","local_operator",{"owner":owner,"reason":reason})
        return self.status(owner)

    def review(self, task, owner, note):
        if task["user_id"]!=owner or task["status"]!="MANUAL_REVIEW":
            raise Rejected("REVIEW_TASK_REQUIRED")
        if not isinstance(note,str) or not 5<=len(note.strip())<=500:
            raise Rejected("REVIEW_REASON_REQUIRED")
        with self.db.transaction() as conn:
            row=conn.execute("SELECT body FROM banking_review_cases WHERE task_id=?",(task["task_id"],)).fetchone()
            if row:
                case=self._load(row['body'])
                if case['owner']!=owner or case['task_id']!=task['task_id']:
                    raise Rejected('SECURITY_STATE_INVALID')
                return case
            case={"case_id":uid(),"task_id":task["task_id"],"owner":owner,"status":"OPEN","note":note,
                  "created_at":time.time(),"operator_notes":[]}
            conn.execute("INSERT INTO banking_review_cases VALUES(?,?,?)",(task["task_id"],owner,self._pack(case)))
            self.audit.append_in(conn,task["task_id"],None,"MANUAL_REVIEW_OPENED","security",case)
        return case

    def cases(self, owner):
        cases=[]
        for row in self.db.rows('SELECT * FROM banking_review_cases WHERE owner=?',(owner,)):
            case=self._load(row['body'])
            if case['owner']!=owner or case['task_id']!=row['task_id']: raise Rejected('SECURITY_STATE_INVALID')
            cases.append(case)
        return cases

    def annotate_review(self, task_id, owner, operator_token, note):
        if (not self.config.security_operator_token or not isinstance(operator_token,str)
                or not hmac.compare_digest(operator_token.encode(),self.config.security_operator_token.encode())):
            raise Rejected("SECURITY_OPERATOR_REQUIRED")
        if not isinstance(note,str) or not 5<=len(note.strip())<=500:
            raise Rejected("REVIEW_REASON_REQUIRED")
        with self.db.transaction() as conn:
            row=conn.execute("SELECT body FROM banking_review_cases WHERE task_id=? AND owner=?",(task_id,owner)).fetchone()
            if not row:
                raise Rejected("REVIEW_CASE_REQUIRED")
            case=self._load(row["body"])
            if case['owner']!=owner or case['task_id']!=task_id: raise Rejected('SECURITY_STATE_INVALID')
            case["operator_notes"].append({"note":note,"at":time.time(),"actor":"local_operator"})
            case["status"]="ACKNOWLEDGED"
            conn.execute("UPDATE banking_review_cases SET body=? WHERE task_id=?",(self._pack(case),task_id))
            self.audit.append_in(conn,task_id,None,"MANUAL_REVIEW_ANNOTATED","local_operator",{"case_id":case["case_id"],"note":note})
        return case  # Never changes bank state, authorization or the task's financial status.
