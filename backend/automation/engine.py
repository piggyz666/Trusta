"""联动规则 (automation rules): the generalised cross-scenario engine.

A rule is  trigger  →  ordered actions , drawn only from fixed whitelists:

  triggers  SCHEDULE (once / daily / weekly / monthly) · INCOME (credit ≥ X) · BALANCE_BELOW (balance < X)
            SPEND_OVER (consumption in a month or a date window, optionally one category, ≥ X)
            LARGE_TRANSACTION (single purchase ≥ X)
  actions   NOTIFY · HANDOFF · TRANSFER · WISH_LOCK · INVEST · CARD_FREEZE · CARD_CONTROL

Safety model
* A rule is created as a DRAFT and only becomes ACTIVE after the customer confirms the exact rule (bound by rule_hash).
* When a rule fires, money-moving actions become ordinary proposals: each still goes through Policy → Authorization
  (the customer confirms every single one, RED ones with a one-time code). Firing never moves money by itself.
* Only *tightening* actions (freeze a card, switch a card channel OFF) may run without a further tap, and only when the
  customer granted that standing consent on the rule. The chat layer re-checks the concrete action is tightening and
  YELLOW before using it; anything else falls back to a confirmation card.
* Rule bodies are HMAC-sealed; activation, firing and every standing-consent use are written to the audit chain.
* Caps: ≤ 20 active rules per customer, ≤ 5 actions per rule, bounded runs, at most one firing per rule per 10 minutes
  (INCOME: one per credit), rules expire after at most one year.
"""
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from backend.business import CHINA, month_window
from backend.chat.amounts import format_cents
from backend.domain import Rejected, canonical, digest, uid

TRIGGERS = {"SCHEDULE", "INCOME", "BALANCE_BELOW", "SPEND_OVER", "LARGE_TRANSACTION"}
ACTIONS = {"NOTIFY", "HANDOFF", "TRANSFER", "WISH_LOCK", "INVEST", "CARD_FREEZE", "CARD_CONTROL"}
MONEY_ACTIONS = {"TRANSFER", "WISH_LOCK", "INVEST"}
TIGHTENING = {"CARD_FREEZE", "CARD_CONTROL"}
FREQUENCIES = {"ONCE", "DAILY", "WEEKLY", "MONTHLY"}
CATEGORY_LABELS = {"FOOD": "餐饮", "GROCERIES": "日用", "TRANSPORT": "交通", "SHOPPING": "购物", "OTHER": "其他"}
CONTROL_LABELS = {"online": "线上支付", "international": "境外交易", "contactless": "非接触支付"}
MAX_ACTIVE, MAX_ACTIONS, MIN_GAP, MAX_LIFETIME = 20, 5, 600, 366 * 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS automation_rules(id TEXT PRIMARY KEY, owner TEXT NOT NULL, bundle_id TEXT NOT NULL,
 status TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS automation_owner ON automation_rules(owner, status);
CREATE TABLE IF NOT EXISTS automation_firings(id TEXT PRIMARY KEY, owner TEXT NOT NULL, rule_id TEXT NOT NULL,
 status TEXT NOT NULL, created_at REAL NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS automation_firing_owner ON automation_firings(owner, status);
"""


def _when(stamp):
    return datetime.fromtimestamp(stamp, CHINA).strftime("%m月%d日 %H:%M")


class AutomationEngine:
    def __init__(self, service):
        self.s = service
        self.key = hmac.new(service.config.signing_key, b"automation-rules-v1", hashlib.sha256).digest()
        with service.db.transaction() as conn:
            conn.executescript(SCHEMA)

    # ---- storage -------------------------------------------------------------------------------
    def _pack(self, body):
        return canonical({"body": body, "seal": hmac.new(self.key, canonical(body).encode(), hashlib.sha256).hexdigest()})

    def _load(self, raw):
        record = json.loads(raw)
        expected = hmac.new(self.key, canonical(record["body"]).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, record["seal"]):
            raise Rejected("AUTOMATION_STATE_INVALID")
        return record["body"]

    def _save(self, conn, rule):
        conn.execute("INSERT INTO automation_rules VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, body=excluded.body",
                     (rule["id"], rule["owner"], rule["bundle_id"], rule["status"], self._pack(rule)))

    def rules(self, owner, statuses=("ACTIVE",)):
        marks = ",".join("?" * len(statuses))
        return [self._load(r["body"]) for r in self.s.db.rows(
            f"SELECT body FROM automation_rules WHERE owner=? AND status IN ({marks}) ORDER BY id", (owner, *statuses))]

    def bundle(self, owner, bundle_id):
        rules = [self._load(r["body"]) for r in self.s.db.rows(
            "SELECT body FROM automation_rules WHERE owner=? AND bundle_id=? ORDER BY id", (owner, bundle_id))]
        if not rules:
            raise Rejected("AUTOMATION_NOT_FOUND")
        return sorted(rules, key=lambda r: r.get("index", 0))

    @staticmethod
    def bundle_hash(rules):
        return digest([{k: v for k, v in r.items() if k not in {"status", "state", "runs", "activated_at"}} for r in rules])

    # ---- drafting ------------------------------------------------------------------------------
    def _check(self, owner, rule):
        t, actions = rule["trigger"], rule["actions"]
        if t.get("type") not in TRIGGERS or not 1 <= len(actions) <= MAX_ACTIONS:
            raise Rejected("AUTOMATION_INVALID")
        if t["type"] == "SCHEDULE":
            if t.get("frequency") not in FREQUENCIES or type(t.get("first_at")) not in (int, float) or t["first_at"] <= time.time() - 60:
                raise Rejected("AUTOMATION_INVALID_SCHEDULE")
        else:
            if type(t.get("amount")) is not int or t["amount"] <= 0:
                raise Rejected("AUTOMATION_INVALID_THRESHOLD")
        if t["type"] == "SPEND_OVER":
            if t.get("category") not in (None, *CATEGORY_LABELS):
                raise Rejected("AUTOMATION_INVALID")
            window = t.get("window")
            if window is not None and not (isinstance(window, list) and len(window) == 2 and window[0] < window[1]
                                           and window[1] - window[0] <= 92 * 86400):
                raise Rejected("AUTOMATION_INVALID_WINDOW")
        percent = 0
        for a in actions:
            kind = a.get("type")
            if kind not in ACTIONS:
                raise Rejected("AUTOMATION_INVALID_ACTION")
            if kind in MONEY_ACTIONS:
                sources = [k for k in ("amount", "percent", "from_subscriptions") if k in a]
                if len(sources) != 1:
                    raise Rejected("AUTOMATION_AMOUNT_REQUIRED")
                if "amount" in a and (type(a["amount"]) is not int or a["amount"] <= 0):
                    raise Rejected("AUTOMATION_AMOUNT_REQUIRED")
                if "percent" in a:
                    if t["type"] != "INCOME" or type(a["percent"]) is not int or not 1 <= a["percent"] <= 100:
                        raise Rejected("AUTOMATION_PERCENT_NEEDS_INCOME")
                    percent += a["percent"]
            if kind == "TRANSFER" and not a.get("payee_name"):
                raise Rejected("AUTOMATION_INVALID_ACTION")
            if kind == "INVEST" and not a.get("product_id"):
                raise Rejected("AUTOMATION_INVALID_ACTION")
            if kind in TIGHTENING and not a.get("card_id"):
                raise Rejected("AUTOMATION_INVALID_ACTION")
            if kind == "CARD_CONTROL" and (not a.get("controls") or set(a["controls"]) - set(CONTROL_LABELS)
                                           or any(type(v) is not bool for v in a["controls"].values())):
                raise Rejected("AUTOMATION_INVALID_ACTION")
        if percent > 100:
            raise Rejected("AUTOMATION_PERCENT_OVER_100")

    def draft(self, owner, account_id, specs, auto_tighten=False, source=None):
        """specs: list of {name, trigger, actions, max_runs?, expires_at?}. One confirmation covers the whole bundle."""
        with self.s.db.transaction() as conn:
            active = conn.execute("SELECT COUNT(*) FROM automation_rules WHERE owner=? AND status='ACTIVE'", (owner,)).fetchone()[0]
        if active + len(specs) > MAX_ACTIVE:
            raise Rejected("AUTOMATION_TOO_MANY_RULES")
        now, bundle_id, rules = time.time(), "ab-" + uid().replace("-", "")[:12], []
        for index, spec in enumerate(specs):
            rule = {"id": "ar-" + uid().replace("-", "")[:12], "bundle_id": bundle_id, "index": index, "owner": owner, "account_id": account_id,
                    "name": str(spec.get("name") or "联动规则")[:30], "trigger": spec["trigger"], "actions": spec["actions"],
                    "auto_tighten": bool(auto_tighten) and any(a["type"] in TIGHTENING for a in spec["actions"]),
                    "max_runs": int(spec.get("max_runs") or (1 if spec["trigger"].get("frequency") == "ONCE" else 12)),
                    "expires_at": min(spec.get("expires_at") or now + MAX_LIFETIME, now + MAX_LIFETIME),
                    "created_at": now, "draft_expires_at": now + 900, "source": source or {},
                    "status": "DRAFT", "runs": 0, "state": {}}
            if not 1 <= rule["max_runs"] <= 60:
                raise Rejected("AUTOMATION_INVALID")
            self._check(owner, rule)
            rules.append(rule)
        with self.s.db.transaction() as conn:
            for rule in rules:
                self._save(conn, rule)
            self.s.audit.append_in(conn, None, None, "AUTOMATION_DRAFTED", "automation",
                                   {"owner": owner, "bundle_id": bundle_id, "bundle_hash": self.bundle_hash(rules)})
        return bundle_id, self.bundle_hash(rules), rules

    def activate(self, owner, bundle_id, bundle_hash):
        rules = self.bundle(owner, bundle_id)
        if not hmac.compare_digest(self.bundle_hash(rules), bundle_hash or ""):
            raise Rejected("AUTOMATION_HASH_MISMATCH")
        now = time.time()
        if any(r["status"] != "DRAFT" for r in rules):
            raise Rejected("AUTOMATION_NOT_PENDING")
        if any(r["draft_expires_at"] < now for r in rules):
            raise Rejected("AUTOMATION_DRAFT_EXPIRED")
        with self.s.db.transaction() as conn:
            for rule in rules:
                rule.update(status="ACTIVE", activated_at=now, state=self._initial_state(rule, now))
                self._save(conn, rule)
            self.s.audit.append_in(conn, None, None, "AUTOMATION_ACTIVATED", "automation",
                                   {"owner": owner, "bundle_id": bundle_id, "bundle_hash": bundle_hash,
                                    "standing_consent": [r["id"] for r in rules if r["auto_tighten"]]})
        return rules

    def cancel(self, owner, rule_or_bundle_id, reason="USER"):
        with self.s.db.transaction() as conn:
            rows = conn.execute("SELECT body FROM automation_rules WHERE owner=? AND (id=? OR bundle_id=?) AND status IN ('DRAFT','ACTIVE')",
                                (owner, rule_or_bundle_id, rule_or_bundle_id)).fetchall()
            if not rows:
                raise Rejected("AUTOMATION_NOT_FOUND")
            for row in rows:
                rule = self._load(row["body"])
                rule["status"] = "CANCELLED"
                self._save(conn, rule)
            self.s.audit.append_in(conn, None, None, "AUTOMATION_CANCELLED", "automation",
                                   {"owner": owner, "target": rule_or_bundle_id, "reason": reason, "count": len(rows)})
        return len(rows)

    def _initial_state(self, rule, now):
        t = rule["trigger"]
        if t["type"] == "SCHEDULE":
            return {"next_at": t["first_at"]}
        if t["type"] == "INCOME":
            return {"watermark": now}
        if t["type"] == "LARGE_TRANSACTION":
            return {"watermark": now}
        if t["type"] == "BALANCE_BELOW":
            return {"armed": True}
        return {"fired_windows": []}

    # ---- evaluation ----------------------------------------------------------------------------
    def _balance(self, owner, account_id):
        return self.s.gateway.read("transaction", owner, "account.get_balance", account_id=account_id)["balance"]

    def _consumption(self, owner, account_id, start, end):
        months, cursor = [], datetime.fromtimestamp(start, CHINA).replace(day=1)
        while cursor.timestamp() < end:
            months.append(cursor.strftime("%Y-%m"))
            cursor = (cursor + timedelta(days=32)).replace(day=1)
        rows = []
        for month in months:
            rows += [r for r in self.s.gateway.read("finance", owner, "transaction.list", account_id=account_id, month=month)
                     if start <= r["occurred_at"] < end and r["status"] == "SETTLED"]
        return rows

    @staticmethod
    def _next(stamp, frequency, now):
        from backend.runtime.event_plans import next_occurrence
        while stamp <= now:
            stamp = stamp + 86400 if frequency == "DAILY" else next_occurrence(stamp, frequency)
        return stamp

    def _due(self, rule, now):
        """Returns a list of trigger facts ({'amount':…, 'detail':…}) that fire now; mutates rule['state']."""
        t, st = rule["trigger"], rule["state"]
        owner, account = rule["owner"], rule["account_id"]
        if t["type"] == "SCHEDULE":
            if now < st["next_at"]:
                return []
            fact = {"detail": "到达设定时间 " + _when(st["next_at"])}
            st["next_at"] = None if t["frequency"] == "ONCE" else self._next(st["next_at"], t["frequency"], now)
            return [fact]
        if now - st.get("last_fired", 0) < MIN_GAP and t["type"] != "INCOME":
            return []
        if t["type"] == "INCOME":
            feed = getattr(self.s, "feed", None)
            if not feed:
                return []
            facts = []
            for credit in feed.incoming(owner, st["watermark"]):
                st["watermark"] = max(st["watermark"], credit["occurred_at"])
                if credit["account_id"] == account and credit["amount"] >= t["amount"]:
                    facts.append({"amount": credit["amount"], "credit_id": credit["id"],
                                  "detail": f"收到入账 {format_cents(credit['amount'])}"})
            return facts[:3]
        if t["type"] == "BALANCE_BELOW":
            balance = self._balance(owner, account)
            if balance >= t["amount"]:
                st["armed"] = True  # re-arm once the balance has recovered
                return []
            if not st.get("armed"):
                return []
            st["armed"] = False
            return [{"detail": f"余额 {format_cents(balance)}，低于 {format_cents(t['amount'])}"}]
        if t["type"] == "LARGE_TRANSACTION":
            start = st["watermark"]
            rows = [r for r in self._consumption(owner, account, start, now + 1) if r["kind"] == "PURCHASE"]
            if rows:
                st["watermark"] = max(r["occurred_at"] for r in rows)
            hits = [r for r in rows if r["amount"] >= t["amount"] and r["occurred_at"] > start]
            return [{"amount": r["amount"], "record_id": r["id"], "detail": f"出现一笔 {format_cents(r['amount'])} 的消费"} for r in hits[:1]]
        # SPEND_OVER
        if t.get("window"):
            start, end = t["window"]
            key = "W"
            if now < start:
                return []
        else:
            start, end, _ = month_window(datetime.fromtimestamp(now, CHINA).strftime("%Y-%m"))
            key = datetime.fromtimestamp(now, CHINA).strftime("%Y-%m")
        if key in st["fired_windows"]:
            return []
        rows = self._consumption(owner, account, start, min(end, now + 1))
        if t.get("category"):
            rows = [r for r in rows if r["category"] == t["category"]]
        spent = sum(r["amount"] if r["kind"] == "PURCHASE" else -r["amount"] for r in rows)
        if spent < t["amount"]:
            return []
        st["fired_windows"] = (st["fired_windows"] + [key])[-12:]
        label = CATEGORY_LABELS.get(t.get("category"), "")
        return [{"amount": spent, "detail": f"{label}消费已达 {format_cents(spent)}（阈值 {format_cents(t['amount'])}）"}]

    def evaluate(self, owner, now=None):
        """Check every active rule of this customer; persist firings. Idempotent per trigger fact."""
        now = now or time.time()
        fired = []
        for rule in self.rules(owner):
            if rule["expires_at"] <= now or rule["runs"] >= rule["max_runs"]:
                with self.s.db.transaction() as conn:
                    rule["status"] = "EXPIRED" if rule["expires_at"] <= now else "COMPLETED"
                    self._save(conn, rule)
                continue
            try:
                facts = self._due(rule, now)
            except Rejected:
                continue
            facts = facts[: rule["max_runs"] - rule["runs"]]
            resolved = [self._resolve(rule, fact) for fact in facts]  # bank reads happen outside the write transaction
            with self.s.db.transaction() as conn:
                for fact, actions in zip(facts, resolved):
                    firing = {"id": "af-" + uid().replace("-", "")[:12], "rule_id": rule["id"], "owner": owner,
                              "account_id": rule["account_id"], "rule_name": rule["name"], "auto_tighten": rule["auto_tighten"],
                              "fact": fact, "actions": actions, "created_at": now, "rule_hash": digest(
                                  {k: v for k, v in rule.items() if k not in {"status", "state", "runs", "activated_at"}})}
                    conn.execute("INSERT INTO automation_firings VALUES(?,?,?,?,?,?)",
                                 (firing["id"], owner, rule["id"], "READY", now, canonical(firing)))
                    self.s.audit.append_in(conn, None, None, "AUTOMATION_FIRED", "automation",
                                           {"rule_id": rule["id"], "firing_id": firing["id"], "fact_hash": digest(fact)})
                    rule["runs"] += 1
                    rule["state"]["last_fired"] = now
                    fired.append(firing)
                if rule["trigger"]["type"] == "SCHEDULE" and rule["state"].get("next_at") is None:
                    rule["status"] = "COMPLETED"
                if rule["runs"] >= rule["max_runs"]:
                    rule["status"] = "COMPLETED"
                self._save(conn, rule)
        return fired

    def _resolve(self, rule, fact):
        """Concrete amounts are computed here from bank facts, never by the model at firing time."""
        resolved = []
        for a in rule["actions"]:
            a = dict(a)
            if "percent" in a:
                a["amount"] = fact["amount"] * a.pop("percent") // 100
            elif "from_subscriptions" in a:
                total = 0
                for sub_id in a.pop("from_subscriptions"):
                    try:
                        total += self.s.gateway.read("service", rule["owner"], "subscription.get", resource_id=sub_id)["amount"]
                    except (Rejected, KeyError, TypeError):
                        pass
                if a["type"] == "INVEST":
                    # Small monthly savings accumulate until they reach the product's minimum purchase amount.
                    minimum = next((p["minimum_amount"] for p in self.s.gateway.read("wealth", rule["owner"], "wealth.list_products")
                                    if p["id"] == a["product_id"]), 0)
                    total += rule["state"].get("carry", 0)
                    if total < minimum:
                        rule["state"]["carry"] = total
                        a = {"type": "NOTIFY", "detail": f"省下的钱已累计 {format_cents(total)}，达到起购金额 {format_cents(minimum)} 后再申购"}
                        resolved.append(a)
                        continue
                    rule["state"]["carry"] = 0
                a["amount"] = total
            resolved.append(a)
        return resolved

    def ready(self, owner):
        return [json.loads(r["body"]) for r in self.s.db.rows(
            "SELECT body FROM automation_firings WHERE owner=? AND status='READY' ORDER BY created_at, id", (owner,))]

    def update_firing(self, firing_id, fields):
        with self.s.db.transaction() as conn:
            row = conn.execute("SELECT body FROM automation_firings WHERE id=?", (firing_id,)).fetchone()
            conn.execute("UPDATE automation_firings SET body=? WHERE id=?", (canonical(json.loads(row["body"]) | fields), firing_id))

    def mark(self, firing_id, status, detail=None):
        with self.s.db.transaction() as conn:
            row = conn.execute("SELECT body FROM automation_firings WHERE id=?", (firing_id,)).fetchone()
            body = json.loads(row["body"]) | {"delivered": detail or {}}
            conn.execute("UPDATE automation_firings SET status=?, body=? WHERE id=?", (status, canonical(body), firing_id))

    # ---- presentation (deterministic text, never model output) ------------------------------------
    def describe_trigger(self, t):
        if t["type"] == "SCHEDULE":
            moment = datetime.fromtimestamp(t["first_at"], CHINA)
            clock, day = moment.strftime("%H:%M"), f"{moment.month}月{moment.day}日"
            return {"ONCE": f"在 {day} {clock}", "DAILY": f"每天 {clock}（从 {day} 起）",
                    "WEEKLY": f"每周{'一二三四五六日'[moment.weekday()]} {clock}（从 {day} 起）",
                    "MONTHLY": f"每月 {moment.day} 日 {clock}（从 {day} 起）"}[t["frequency"]]
        amount = format_cents(t["amount"])
        if t["type"] == "INCOME":
            return f"每次收到 ≥ {amount} 的入账时"
        if t["type"] == "BALANCE_BELOW":
            return f"余额低于 {amount} 时"
        if t["type"] == "LARGE_TRANSACTION":
            return f"出现单笔 ≥ {amount} 的消费时"
        label = CATEGORY_LABELS.get(t.get("category"), "")
        if t.get("window"):
            start, end = (datetime.fromtimestamp(x, CHINA).strftime("%m月%d日") for x in t["window"])
            return f"{start}–{end} 期间{label}消费达到 {amount} 时"
        return f"当月{label}消费达到 {amount} 时"

    def describe_action(self, a, auto_tighten, catalog_names=None):
        names = catalog_names or {}
        if "amount" in a:
            money = format_cents(a["amount"])
        elif "percent" in a:
            money = f"入账金额的 {a['percent']}%"
        elif "from_subscriptions" in a:
            money = "已取消订阅的月费合计"
        else:
            money = ""
        tail = "（每次需你确认）"
        if a["type"] == "NOTIFY":
            return "提醒我"
        if a["type"] == "HANDOFF":
            return "转人工客服核实"
        if a["type"] == "TRANSFER":
            return f"向 {a['payee_name']} 转账 {money}{tail}"
        if a["type"] == "WISH_LOCK":
            return f"锁定心愿金“{a.get('fund_name', '储蓄')}” {money}{tail}"
        if a["type"] == "INVEST":
            return f"申购 {names.get(a['product_id'], a['product_id'])} {money}{tail}"
        mode = "（已授权自动执行，只会收紧）" if auto_tighten else "（需你确认）"
        if a["type"] == "CARD_FREEZE":
            return f"冻结银行卡 {names.get(a['card_id'], a['card_id'])}{mode}"
        parts = "、".join(("开启" if v else "关闭") + CONTROL_LABELS[k] for k, v in a["controls"].items())
        loosen = any(a["controls"].values())
        return f"{parts}{'（需你确认）' if loosen else mode}"
