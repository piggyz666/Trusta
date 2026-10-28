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
CREATE TABLE IF NOT EXISTS banking_sms_otps(id TEXT PRIMARY KEY, owner TEXT NOT NULL, authorization_id TEXT NOT NULL,
 created_at REAL NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS sms_otp_auth ON banking_sms_otps(authorization_id);
CREATE INDEX IF NOT EXISTS sms_otp_owner ON banking_sms_otps(owner,created_at);
CREATE TABLE IF NOT EXISTS account_protection(owner TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS account_protection_no_delete BEFORE DELETE ON account_protection BEGIN SELECT RAISE(ABORT,'protection cannot be deleted'); END;
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
        from backend.security.webauthn import WebAuthn
        self.webauthn = WebAuthn(db, audit, config.signing_key)  # face / fingerprint / security key
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
                "webauthn_credentials":self.webauthn.credentials(owner),
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

    VELOCITY_WINDOW, VELOCITY_LIMIT = 600, 3
    NEW_PAYEE_LIMIT = 50000

    SMS_TTL = 300
    SMS_MAX_ATTEMPTS = 3
    SMS_PER_AUTHORIZATION = 3
    SMS_PER_OWNER_10MIN = 6

    def _sms_hash(self, otp_id, code):
        return hmac.new(self.key, f"sms-otp:{otp_id}:{code}".encode(), hashlib.sha256).hexdigest()

    def issue_sms(self, owner, auth, phone="13800000000", demo=False):
        """Second factor bound to ONE authorization (action hash). Simulated SMS gateway: the code is only
        returned to the caller in demo mode; otherwise it would go to the customer's registered phone."""
        if not self.enabled:
            raise Rejected("COMPETITION_SAFETY_DISABLED")
        if auth["user_id"] != owner or auth["status"] != "PENDING_STRONG_AUTH" or auth.get("auth_method") != "totp":
            raise Rejected("SMS_NOT_APPLICABLE")
        now = time.time()
        if now >= auth["expires_at"]:
            raise Rejected("AUTHORIZATION_EXPIRED")
        with self.db.transaction() as conn:
            self.assert_unlocked(owner, conn)
            if conn.execute("SELECT COUNT(*) FROM banking_sms_otps WHERE authorization_id=?", (auth["authorization_id"],)).fetchone()[0] >= self.SMS_PER_AUTHORIZATION:
                raise Rejected("SMS_RATE_LIMITED")
            if conn.execute("SELECT COUNT(*) FROM banking_sms_otps WHERE owner=? AND created_at>?", (owner, now - 600)).fetchone()[0] >= self.SMS_PER_OWNER_10MIN:
                raise Rejected("SMS_RATE_LIMITED")
            # A new code supersedes earlier unused ones for the same authorization.
            for row in conn.execute("SELECT id, body FROM banking_sms_otps WHERE authorization_id=?", (auth["authorization_id"],)).fetchall():
                old = self._load(row["body"])
                if old["status"] == "SENT":
                    old["status"] = "SUPERSEDED"
                    conn.execute("UPDATE banking_sms_otps SET body=? WHERE id=?", (self._pack(old), row["id"]))
            otp_id, code = uid(), f"{secrets.randbelow(10**6):06d}"
            body = {"id": otp_id, "owner": owner, "authorization_id": auth["authorization_id"], "action_hash": auth["action_hash"],
                    "code_hash": self._sms_hash(otp_id, code), "created_at": now,
                    "expires_at": min(now + self.SMS_TTL, auth["expires_at"]), "attempts": 0, "status": "SENT"}
            conn.execute("INSERT INTO banking_sms_otps VALUES(?,?,?,?,?)", (otp_id, owner, auth["authorization_id"], now, self._pack(body)))
            self.audit.append_in(conn, None, None, "SMS_OTP_SENT", "security",
                                 {"owner": owner, "authorization_id": auth["authorization_id"], "otp_id": otp_id})
        masked = phone[:3] + "****" + phone[-4:]
        result = {"sent_to": masked, "expires_at": body["expires_at"], "channel": "SIMULATED_SMS"}
        if demo:
            result["dev_code"] = code
        return result

    def _verify_sms(self, conn, auth, action, code):
        owner = auth["user_id"]
        self.assert_unlocked(owner, conn)
        rows = conn.execute("SELECT id, body FROM banking_sms_otps WHERE authorization_id=? ORDER BY created_at DESC", (auth["authorization_id"],)).fetchall()
        live = [(r["id"], self._load(r["body"])) for r in rows]
        live = [(i, b) for i, b in live if b["status"] == "SENT" and time.time() < b["expires_at"]]
        if not live:
            self._failure(conn, owner, "authentication", "SMS_NOT_SENT_OR_EXPIRED")
            return None, "SMS_NOT_SENT_OR_EXPIRED"
        otp_id, body = live[0]
        if body["owner"] != owner or body["action_hash"] != digest(action):
            raise Rejected("SECURITY_STATE_INVALID")
        ok = isinstance(code, str) and len(code) == 6 and code.isdigit() and hmac.compare_digest(body["code_hash"], self._sms_hash(otp_id, code))
        body["attempts"] += 1
        body["status"] = "USED" if ok else ("EXHAUSTED" if body["attempts"] >= self.SMS_MAX_ATTEMPTS else "SENT")
        conn.execute("UPDATE banking_sms_otps SET body=? WHERE id=?", (self._pack(body), otp_id))
        if not ok:
            self._failure(conn, owner, "authentication", "SMS_INVALID")
            return None, "SMS_INVALID"
        state = self._user(conn, owner)
        state["authentication_failures"] = 0
        self._save_user(conn, state)
        return otp_id, None

    def verify_authorization(self, conn, auth, action, code):
        if isinstance(code, str) and code.startswith("sms:"):
            reference, error = self._verify_sms(conn, auth, action, code[4:])
            method, counter = "sms", None
        elif isinstance(code, str) and code.startswith("webauthn:"):
            self.assert_unlocked(auth["user_id"], conn)
            reference, error = self.webauthn.verify_assertion(conn, auth["user_id"], auth, digest(action), code[9:])
            method, counter = "webauthn", None
            if error:
                self._failure(conn, auth["user_id"], "authentication", error)
            else:
                state = self._user(conn, auth["user_id"])
                state["authentication_failures"] = 0
                self._save_user(conn, state)
        else:
            counter, error = self._verify_code(conn, auth["user_id"], code)
            method, reference = "totp", None
        if error:
            return None,error
        proof = {"authorization_id":auth["authorization_id"],"owner":auth["user_id"],"action_hash":digest(action),
                 "verified_at":time.time(),"expires_at":auth["expires_at"],"counter":counter,"method":method}
        if reference:
            proof["credential" if method == "webauthn" else "otp_id"] = reference
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
                and auth["created_at"] <= proof["verified_at"] < auth["expires_at"] and proof["method"] in {"totp","sms","webauthn"})

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
        reasons = []
        if action["currency"]!="CNY":
            reasons.append("FOREIGN_CURRENCY_STRONG_AUTH")
        if total+action["amount"]>self.config.daily_transfer_limit:
            reasons.append("DAILY_TRANSFER_STRONG_AUTH")
        # Velocity: several executed transfers in a short window is a classic account-takeover / scam pattern.
        recent = conn.execute("SELECT COUNT(*) FROM banking_daily_claims WHERE owner=? AND json_extract(body,'$.body.created_at')>?",
                              (action["customer_id"], time.time()-self.VELOCITY_WINDOW)).fetchone()[0]
        if recent >= self.VELOCITY_LIMIT:
            reasons.append("TRANSFER_VELOCITY_STRONG_AUTH")
        # A payee added within 24h receiving a meaningful amount: the typical "new account from a scammer" case.
        # Payees TRUSTA itself added (recorded with the verified receipt, so this works with any bank); the sandbox bank's
        # own registration table is consulted too when present.
        fresh = None
        for table in ("payee_additions", "payee_registrations"):
            if not fresh and conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                fresh = conn.execute(f"SELECT 1 FROM {table} WHERE payee_id=? AND customer_id=? AND created_at>?",
                                     (action.get("recipient_id"), action["customer_id"], time.time()-86400)).fetchone()
        if fresh and action["amount"] >= self.NEW_PAYEE_LIMIT:
            reasons.append("NEW_PAYEE_STRONG_AUTH")
        strong = bool(reasons)
        return {"risk_level":"RED" if strong else "YELLOW", "requires_strong_auth":strong, "reasons":reasons,
                "day":day,"currency":action["currency"],"prior_amount_minor":total,
                "projected_amount_minor":total+action["amount"],"scope":"TRUSTA_INITIATED"}

    # ---- 反诈冷静期 (account protection) -------------------------------------------------------
    # Turned on by the customer in one tap (it only *tightens*), active in every safety profile. During the window every
    # outbound money movement is blocked at Policy time AND again right before execution. It cannot be lifted from the chat
    # or by the customer under pressure -- only by a security operator after verification -- so a scammer coaching the
    # victim gains nothing by asking them to "turn it off".
    PROTECTED_ACTIONS = frozenset({"CARD_CHARGE","CARD_REFUND","CARD_REPAY","CARD_SURPLUS","FUND_SUBSCRIBE","FUND_CONFIRM","FUND_CANCEL","FUND_REDEEM","DEPOSIT_OPEN","DEPOSIT_WITHDRAW","DEPOSIT_RENEW","FX_BUY","FX_SELL","LOAN_DRAW","INSTALLMENT_START","CREDIT_REPAY","CREDIT_SETTLE","GOLD_BUY","GOLD_SELL", "RETAIL_PAY", "TRANSFER", "ADD_PAYEE", "WISH_ORDER", "INVESTMENT_PURCHASE"})
    PROTECTION_MAX_HOURS = 72

    def protection(self, owner, conn=None):
        if conn is None:
            with self.db.transaction() as current:
                return self.protection(owner, current)
        row = conn.execute("SELECT body FROM account_protection WHERE owner=?", (owner,)).fetchone()
        if not row:
            return None
        body = self._load(row["body"])
        return body if body["status"] == "ACTIVE" and body["until"] > time.time() else None

    def enable_protection(self, owner, hours, reason, source):
        if type(hours) is not int or not 1 <= hours <= self.PROTECTION_MAX_HOURS or reason not in {"FRAUD_SUSPECTED", "USER_REQUEST"}:
            raise Rejected("INVALID_PROTECTION")
        now = time.time()
        with self.db.transaction() as conn:
            current = self.protection(owner, conn)
            until = max(now + hours * 3600, current["until"] if current else 0)  # can only be extended, never shortened here
            body = {"owner": owner, "status": "ACTIVE", "since": current["since"] if current else now, "until": until,
                    "reason": reason, "source": source, "updated_at": now}
            conn.execute("INSERT INTO account_protection VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET body=excluded.body",
                         (owner, self._pack(body)))
            self.audit.append_in(conn, None, None, "ACCOUNT_PROTECTION_ENABLED", "security",
                                 {"owner": owner, "until": until, "reason": reason, "source": source})
        return body

    def lift_protection(self, owner, operator_token, reason):
        if (not self.config.security_operator_token or not isinstance(operator_token, str)
                or not hmac.compare_digest(operator_token.encode(), self.config.security_operator_token.encode())):
            raise Rejected("SECURITY_OPERATOR_REQUIRED")
        if not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 500:
            raise Rejected("REVIEW_REASON_REQUIRED")
        with self.db.transaction() as conn:
            current = self.protection(owner, conn)
            if not current:
                raise Rejected("PROTECTION_NOT_ACTIVE")
            body = current | {"status": "LIFTED", "lifted_at": time.time(), "lift_reason_hash": digest(reason)}
            conn.execute("UPDATE account_protection SET body=? WHERE owner=?", (self._pack(body), owner))
            self.audit.append_in(conn, None, None, "ACCOUNT_PROTECTION_LIFTED", "security", {"owner": owner, "reason_hash": digest(reason)})
        return body

    def assert_not_protected(self, action, conn=None):
        if action.get("action_type") in self.PROTECTED_ACTIONS and self.protection(action["customer_id"], conn):
            raise Rejected("ACCOUNT_PROTECTION_ACTIVE")

    def before_execution(self, action, auth):
        self.assert_not_protected(action)
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
