"""The sandbox bank's desk for deposits, FX, gold, loans and instalments, the credit account and practice funds.

Bank side only. TRUSTA reaches it exclusively through the canonical bank interface, exactly as it would a real bank:

  finance.list     the customer's positions, contracts and product terms           (read)
  finance.quote    a priced, unsigned draft contract — nothing moves               (non-monetary request)
  finance.archive  withdraw a draft that was never executed                        (non-monetary request)
  finance.get      one contract with the state its execution depends on            (read, used for preconditions)
  finance.<op>     execute, e.g. finance.deposit_open — signed write path, see ExtendedBank.apply_action

Prices are the bank's: for gold and FX the desk asks its own price source. Every rule and limit below is a sandbox
product term, not an offer by any real bank.
"""
import json
import time
from datetime import datetime, timedelta, date
from decimal import Decimal
from backend.business import CHINA
from backend.domain import Rejected, canonical, uid
from backend.banking import finance as F, extensions as X, advanced as A

REFERENCE_KEYS = {"savings_plan": str, "savings_due": str, "savings_version": int}


def label(card_id):
    return "银行卡 · 标识 " + card_id[-4:]


def _card(c, owner, card_id):
    if not isinstance(card_id, str):
        raise Rejected("INVALID_INPUT")
    card = c.execute("SELECT * FROM cards WHERE id=? AND customer_id=?", (card_id, owner)).fetchone()
    if not card:
        raise Rejected("RESOURCE_NOT_FOUND_OR_DENIED")
    return dict(card)


def _prices(price_source):
    provider = price_source() if price_source else None
    if provider is None:
        from backend.market import MarketQuotes
        provider = MarketQuotes()
    return provider


# ---- finance.list ---------------------------------------------------------------------------------------
def state(db, owner, now=None):
    now = now or time.time()
    with db.transaction() as c:
        items = F.contracts(c, owner)
        financed = {t["source_id"] for t in items if t["type"] == "installment" and t["status"] in {"ACTIVE", "SETTLED"}}
        eligible = []
        for row in c.execute("SELECT r.* FROM consumption_records r JOIN accounts a ON a.id=r.account_id WHERE a.customer_id=? AND r.kind='PURCHASE' "
                             "AND r.status='SETTLED' AND r.category!='融资费用' ORDER BY occurred_at DESC LIMIT 100", (owner,)).fetchall():
            if row["id"] in financed or not 10000 <= row["amount"] <= F.LIMIT:
                continue
            if c.execute("SELECT id FROM bank_operations WHERE id=?", (row["id"],)).fetchone():
                continue  # legacy local retail orders can still be refunded: do not refinance them
            eligible.append(dict(row))
        gold = [dict(r) for r in c.execute("SELECT * FROM gold_positions WHERE customer_id=?", (owner,)).fetchall()]
        deposits = [json.loads(r["body"]) for r in c.execute("SELECT body FROM term_deposits WHERE customer_id=?", (owner,)).fetchall()]
        wallets = [dict(r) for r in c.execute("SELECT * FROM fx_wallets WHERE customer_id=?", (owner,)).fetchall()]
        credit = []
        for card in c.execute("SELECT id FROM cards WHERE customer_id=? AND card_type='DEBIT' ORDER BY id", (owner,)).fetchall():
            v = A.state(c, owner, card["id"])
            d = A.debt(v)
            credit.append(v | {"debt": max(0, d), "surplus": max(0, -d), "available": A.LIMIT - max(0, d), "statements": A.statements(v, now)})
    return {"contracts": sorted(items, key=lambda t: t["created_at"], reverse=True), "eligible": eligible, "gold_positions": gold,
            "outstanding": sum(t["remaining"] for t in items if t["status"] == "ACTIVE" and t["type"] in {"loan", "installment"}),
            "credit_limit": F.LIMIT, "deposits": deposits, "fx_wallets": wallets, "deposit_rates": X.RATES, "currencies": list(X.PRACTICE),
            "credit_states": credit, "credit_card_limit": A.LIMIT, "fund_products": A.PRODUCTS,
            "rules": "仅虚拟产品：贷款按剩余本金计息，年利率 3.60%；消费分期每期按原本金收取 0.50% 手续费（非年化利率）。"
                     "提前结清收取当前一期费用，免除未来期费用。每月一期，不自动扣款。"}


# ---- finance.quote --------------------------------------------------------------------------------------
def quote(db, owner, request, price_source=None):
    if not isinstance(request, dict):
        raise Rejected("INVALID_INPUT")
    body = dict(request)
    reference = body.pop("reference", None)
    if reference is not None:
        # An opaque client reference (TRUSTA's gold savings plan) stored with the draft; the bank never interprets it.
        if (not isinstance(reference, dict) or set(reference) != set(REFERENCE_KEYS) or body.get("type") != "gold_buy"
                or any(type(reference[k]) is not kind for k, kind in REFERENCE_KEYS.items())):
            raise Rejected("INVALID_INPUT")
    typ = body.get("type")
    if not isinstance(typ, str):
        raise Rejected("INVALID_INPUT")
    if typ.upper() in A.LABELS:
        t = _advanced(db, owner, body)
    elif typ.upper() in X.LABELS:
        t = _extension(db, owner, body, price_source)
    else:
        t = _finance(db, owner, body, price_source)
    if reference:
        t.update(reference)
    with db.transaction() as c:
        c.execute("INSERT INTO finance_contracts VALUES(?,?,?)", (t["id"], owner, canonical(t)))
    return t


def _finance(db, owner, b, price_source):
    typ = b.get("type")
    required = {"type", "card_id"} | ({"amount", "months"} if typ == "loan" else {"source_id", "months"} if typ == "installment"
                                      else {"units"} if typ in {"gold_buy", "gold_sell"} else set())
    if typ not in {"loan", "installment", "gold_buy", "gold_sell"} or set(b) != required:
        raise Rejected("INVALID_INPUT")
    if typ in {"loan", "installment"} and (type(b["months"]) is not int or b["months"] not in (3, 6, 12)):
        raise Rejected("INVALID_INPUT")
    if typ == "loan" and (type(b["amount"]) is not int or not 10000 <= b["amount"] <= F.LIMIT):
        raise Rejected("INVALID_INPUT")
    quote_row = None
    if typ.startswith("gold_"):
        if type(b["units"]) is not int or not 1 <= b["units"] <= 100000:
            raise Rejected("INVALID_INPUT")
        provider = _prices(price_source)
        if hasattr(provider, "refresh"):
            provider.refresh()
        quote_row = next((q for q in provider.view()["quotes"] if q["symbol"] == "XAU"), None)
        if not quote_row or quote_row["snapshot"] or quote_row["stale"] or not 0 < quote_row["price"] < 100000:
            raise Rejected("GOLD_QUOTE_UNAVAILABLE")
        price = F.cents(Decimal(str(quote_row["price"])) * 100)
    with db.transaction() as c:
        card = _card(c, owner, b["card_id"])
        if typ == "installment":
            source = c.execute("SELECT * FROM consumption_records WHERE id=? AND account_id=? AND kind='PURCHASE' AND status='SETTLED' AND category!='融资费用'",
                               (b["source_id"], card["account_id"])).fetchone()
            if not source or c.execute("SELECT id FROM bank_operations WHERE id=?", (b["source_id"],)).fetchone():
                raise Rejected("INSTALLMENT_SOURCE_INVALID")
            amount = source["amount"]
            if not 10000 <= amount <= F.LIMIT:
                raise Rejected("INSTALLMENT_SOURCE_INVALID")
        else:
            amount = b["amount"] if typ == "loan" else F.cents(Decimal(price) * b["units"] / 100)
    if amount <= 0:
        raise Rejected("INVALID_INPUT")
    now = time.time()
    return {"id": "fin-" + uid(), "customer_id": owner, "account_id": card["account_id"], "card_id": card["id"], "card_label": label(card["id"]),
            "type": typ, "status": "DRAFT", "version": 0, "amount": amount, "remaining": amount if typ in {"loan", "installment"} else 0,
            "paid_fees": 0, "source_id": b.get("source_id", ""),
            "schedule": F.schedule(amount, b["months"], typ == "installment") if typ in {"loan", "installment"} else [],
            "created_at": now, "expires_at": now + (120 if quote_row else 1800), "units": b.get("units", 0),
            "price": price if quote_row else 0, "quote": quote_row, "realized_profit": 0}


def _extension(db, owner, b, price_source):
    typ = b.get("type")
    allowed = {"deposit_open": {"type", "card_id", "amount", "term"}, "deposit_withdraw": {"type", "deposit_id"}, "deposit_renew": {"type", "deposit_id"},
               "fx_buy": {"type", "card_id", "currency", "units", "quote_mode"}, "fx_sell": {"type", "card_id", "currency", "units", "quote_mode"}}
    if typ not in allowed or set(b) != allowed[typ]:
        raise Rejected("INVALID_INPUT")
    now = time.time()
    today = datetime.fromtimestamp(now, CHINA).date()
    extras, quote_row = {}, None
    if typ == "deposit_open":
        amount = X.integer(b["amount"], 10000, 100000000)
        if type(b["term"]) is not int or b["term"] not in X.RATES:
            raise Rejected("INVALID_INPUT")
        extras = dict(principal=amount, term=b["term"], rate_bps=X.RATES[b["term"]], opened=today.isoformat(),
                      matures=(today + timedelta(days=b["term"])).isoformat())
    elif typ.startswith("fx_"):
        if b["currency"] not in X.PRACTICE or b["quote_mode"] not in {"reference", "practice"}:
            raise Rejected("INVALID_INPUT")
        units = X.integer(b["units"], 1, 100000000)
        if b["quote_mode"] == "practice":
            rate = X.PRACTICE[b["currency"]]
            quote_row = {"source": "内置练习汇率（非市场报价）", "as_of": today.isoformat(), "mode": "practice"}
        else:
            provider = _prices(price_source)
            if hasattr(provider, "refresh"):
                provider.refresh()
            q = next((q for q in provider.view()["quotes"] if q["symbol"] == b["currency"] + "CNY"), None)
            if not q or q["snapshot"] or q["stale"]:
                raise Rejected("FX_QUOTE_UNAVAILABLE")
            rate = str(Decimal(str(q["price"])) / (100 if b["currency"] == "JPY" else 1))
            quote_row = q | {"mode": "reference"}
        amount = X.rounded(Decimal(rate) * units)
        if amount < 1 or amount > 100000000:
            raise Rejected("INVALID_INPUT")
        extras = dict(units=units, foreign_currency=b["currency"], rate=rate)
    with db.transaction() as c:
        if typ in {"deposit_withdraw", "deposit_renew"}:
            d = X.owned(c, "term_deposits", owner, b["deposit_id"])
            if d["status"] != "ACTIVE":
                raise Rejected("DEPOSIT_NOT_ACTIVE")
            if typ == "deposit_renew" and today < date.fromisoformat(d["matures"]):
                raise Rejected("DEPOSIT_NOT_MATURE")
            earned, days, rate = X.interest(d, now)
            amount = earned + (d["principal"] if typ == "deposit_withdraw" else 0)
            extras = {k: d[k] for k in ("principal", "term", "rate_bps")}
            extras.update(deposit_id=d["id"], interest=earned, days=days, applied_bps=rate, renew_opened=today.isoformat(),
                          renew_matures=(today + timedelta(days=d["term"])).isoformat())
            card_id = d["card_id"]
        else:
            card_id = b["card_id"]
        card = _card(c, owner, card_id)
        if card["card_type"] != "DEBIT":
            raise Rejected("DEBIT_CARD_REQUIRED")
    return dict(id="fin-" + uid(), customer_id=owner, account_id=card["account_id"], card_id=card_id, card_label=label(card_id), type=typ,
                status="DRAFT", version=0, amount=amount, remaining=0, paid_fees=0, source_id="", schedule=[], created_at=now,
                expires_at=now + 120, units=0, price=0, quote=quote_row, realized_profit=0) | extras


def _advanced(db, owner, b):
    typ = b.get("type")
    required = {"card_charge": {"amount", "merchant"}, "card_refund": {"entry_id", "amount"}, "card_repay": {"repay_mode"}, "card_surplus": set(),
                "fund_subscribe": {"product_id", "amount"}, "fund_confirm": {"order_id"}, "fund_cancel": {"order_id"}, "fund_redeem": {"product_id", "units"}}
    if typ not in required or set(b) != required[typ] | {"type", "card_id"}:
        raise Rejected("INVALID_INPUT")
    now, extra = time.time(), {}
    with db.transaction() as c:
        card = c.execute("SELECT * FROM cards WHERE id=? AND customer_id=?", (b["card_id"], owner)).fetchone() if isinstance(b["card_id"], str) else None
        if not card or card["card_type"] != "DEBIT":
            raise Rejected("DEBIT_CARD_REQUIRED")
        v = A.state(c, owner, card["id"])
    amount = 0
    if typ == "card_charge":
        amount = X.integer(b["amount"], 1, A.LIMIT)
        if not isinstance(b["merchant"], str) or not 1 <= len(b["merchant"].strip()) <= 40:
            raise Rejected("INVALID_INPUT")
        posted, closing, due = A.dates(now)
        extra = dict(merchant=b["merchant"].strip(), posted=posted, statement=closing, due=due)
    elif typ == "card_refund":
        e = next((e for e in v["entries"] if e["id"] == b["entry_id"]), None)
        if not e:
            raise Rejected("RESOURCE_NOT_FOUND_OR_DENIED")
        amount = X.integer(b["amount"], 1, e["amount"] - e["refunded"])
        extra = dict(entry_id=e["id"], merchant=e["merchant"])
    elif typ == "card_repay":
        if b["repay_mode"] not in {"full", "minimum"}:
            raise Rejected("INVALID_INPUT")
        debt = max(0, A.debt(v))
        amount = debt if b["repay_mode"] == "full" else min(debt, sum(g["minimum"] for g in A.statements(v, now) if g["status"] in {"BILLED", "OVERDUE"}))
        extra["repay_mode"] = b["repay_mode"]
    elif typ == "card_surplus":
        amount = max(0, -A.debt(v))
    elif typ in {"fund_confirm", "fund_cancel"}:
        o = next((o for o in v["orders"] if o["id"] == b["order_id"] and o["status"] == "PENDING"), None)
        if not o:
            raise Rejected("FUND_ORDER_NOT_PENDING")
        amount = o["amount"]
        extra = {k: o[k] for k in ("product_id", "units", "fee", "nav")}
        extra["order_id"] = o["id"]
    else:
        pr = A.product(b["product_id"])
        extra = dict(product_id=pr["id"], nav=pr["nav"])
        if typ == "fund_subscribe":
            amount = X.integer(b["amount"], 100, 100000000)
            fee = amount * pr["buy_bps"] // 10000
            extra.update(fee=fee, units=(amount - fee) * 1000000 // pr["nav"])
        else:
            units = X.integer(b["units"], 1, 10 ** 12)
            gross = units * pr["nav"] // 1000000
            fee = gross * pr["sell_bps"] // 10000
            amount = gross - fee
            extra.update(units=units, fee=fee)
    if not 1 <= amount <= 100000000:
        raise Rejected("NO_PAYABLE_AMOUNT")
    return dict(id="fin-" + uid(), customer_id=owner, card_id=card["id"], account_id=card["account_id"], card_label=label(card["id"]), type=typ,
                status="DRAFT", version=0, amount=amount, remaining=0, paid_fees=0, source_id="", schedule=[], created_at=now, expires_at=now + 120,
                units=0, price=0, quote=None, realized_profit=0, state_version=v["version"],
                trade_date=str(datetime.fromtimestamp(now, CHINA).date())) | extra


# ---- finance.archive ------------------------------------------------------------------------------------
def archive(db, owner, contract_id):
    if not isinstance(contract_id, str):
        raise Rejected("INVALID_INPUT")
    with db.transaction() as c:
        t = X.owned(c, "finance_contracts", owner, contract_id)
        if t["status"] != "DRAFT":
            raise Rejected("FINANCE_STATE_INVALID")
        # Changing status invalidates every old signed precondition; no ledger or history is removed.
        t["status"] = "ARCHIVED"
        t["version"] += 1
        t["remaining"] = 0
        c.execute("UPDATE finance_contracts SET body=? WHERE id=?", (canonical(t), t["id"]))
    return t
