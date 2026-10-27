"""The data contract between TRUSTA and a bank adapter, as machine-checkable schemas.

Every read a provider answers, every write result and every verification result has a schema here. They are used by
the conformance checker (python -m scripts.bank_check) and exported to docs/schemas/ for bank engineers
(python -m scripts.bank_check --export-schemas). docs/bank-data-contract.md explains them in prose.

Conventions: money is an integer in the currency's minor unit (fen / cents); time is Unix seconds (number); currency is
an ISO 4217 code; booleans may also be 0 / 1. Extra fields are allowed and ignored. Vendor-specific fields must be
mapped inside the adapter, never passed through as the canonical ones.
"""

MONEY = {"type": "integer", "minimum": 0}
POSITIVE = {"type": "integer", "minimum": 1}
TIME = {"type": "number", "minimum": 0}
CURRENCY = {"type": "string", "pattern": "^[A-Z]{3}$"}
FLAG = {"type": ["boolean", "integer"]}
ID = {"type": "string", "minLength": 1}
VERSION = {"type": "integer", "minimum": 0, "description": "增加即表示状态变化；用于签名动作的前置条件比对"}
STATUS = ["CREATED", "PENDING", "AUTHORIZED", "PROCESSING", "SETTLED", "FAILED", "REVERSED", "UNKNOWN"]


def obj(required, optional=None, description=""):
    props = dict(required)
    props.update(optional or {})
    return {"type": "object", "required": sorted(required), "properties": props, "description": description}


SCHEMAS = {
    # ---- accounts.read / balance.read ------------------------------------------------------------------------
    "account_list_item": obj({"id": ID, "currency": CURRENCY},
                             {"balance": {"type": "integer"}, "frozen": FLAG, "name": {"type": "string"}},
                             "accounts.read 不带 _tool 时：该客户的全部账户（列表中的每一项）"),
    "account": obj({"id": ID, "customer_id": ID, "currency": CURRENCY, "balance": {"type": "integer"}, "frozen": FLAG, "version": VERSION},
                   {"name": {"type": "string"}}, "account.get：单个账户；customer_id 必须是授权客户"),
    "balance": obj({"id": ID, "balance": {"type": "integer"}, "currency": CURRENCY, "version": VERSION}, {},
                   "account.get_balance"),
    # ---- transactions.read -----------------------------------------------------------------------------------
    "transaction": obj({"id": ID, "account_id": ID, "amount": POSITIVE, "currency": CURRENCY, "occurred_at": TIME,
                        "status": {"enum": STATUS}, "kind": {"type": "string"}},
                       {"merchant": {"type": "string"}, "category": {"type": "string"},
                        "direction": {"enum": ["IN", "OUT", "ZERO"]}, "payer": {"type": "string"}, "memo": {"type": "string"}},
                       "transaction.list（按月）的每一项。消费 kind=PURCHASE、退款 REFUND；入账 direction=IN 并可带 payer / memo"),
    "recurring_charge": obj({"id": ID, "account_id": ID, "merchant": {"type": "string"}, "amount": POSITIVE, "currency": CURRENCY,
                             "occurred_at": TIME, "status": {"enum": STATUS}}, {}, "transaction.recurring_history（可选）"),
    "transfer": obj({"id": ID, "source_account_id": ID, "recipient_id": ID, "amount": POSITIVE, "currency": CURRENCY,
                     "status": {"enum": STATUS}}, {"executed_at": TIME}, "transaction.get：一笔转账的状态"),
    "payee": obj({"id": ID, "name": {"type": "string", "minLength": 1}}, {"currency": CURRENCY, "is_new": FLAG, "customer_id": ID},
                 "payee.resolve：按姓名（或手机号、备注名）或 recipient_id 查找，返回匹配的收款人列表；两者都为空时返回全部收款人。"
                 "is_new 表示银行认为是新添加的收款人（可选）"),
    # ---- cards / subscriptions -------------------------------------------------------------------------------
    "card": obj({"id": ID, "account_id": ID, "card_type": {"type": "string"}, "status": {"enum": ["ACTIVE", "FROZEN", "LOST", "CLOSED"]},
                 "spending_limit": MONEY, "online_enabled": FLAG, "international_enabled": FLAG, "contactless_enabled": FLAG,
                 "version": VERSION}, {"customer_id": ID}, "card.list / card.get"),
    "subscription": obj({"id": ID, "account_id": ID, "merchant": {"type": "string"}, "amount": MONEY, "currency": CURRENCY,
                         "frequency": {"enum": ["WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY"]}, "next_charge_at": TIME,
                         "status": {"type": "string"}, "cancelable": FLAG, "version": VERSION}, {},
                        "subscription.list / direct_debit.list 的每一项（status：ACTIVE 生效，其余为已取消 / 已关闭）"),
    # ---- investments ------------------------------------------------------------------------------------------
    "product": obj({"id": ID, "name": {"type": "string"}, "risk_level": {"type": "integer", "minimum": 1, "maximum": 5},
                    "minimum_amount": MONEY, "term_days": {"type": "integer", "minimum": 0}, "currency": CURRENCY, "active": FLAG},
                   {"risk_note": {"type": "string"}, "issuer": {"type": "string"}, "category": {"type": "string"},
                    "benchmark": {"type": "string"}, "liquidity": {"type": "string"}, "description": {"type": "string"},
                    "restrictions": obj({"purchase_enabled": FLAG, "redemption_enabled": FLAG})},
                   "wealth.list_products 的每一项"),
    "portfolio": obj({"account_id": ID, "product_id": ID, "amount_cents": MONEY, "version": VERSION},
                     {"redeemable_at": TIME, "customer_id": ID}, "portfolio.get"),
    "risk_profile": obj({"customer_id": ID, "risk_level": {"type": "integer", "minimum": 0, "maximum": 5}, "assessed_at": TIME,
                         "expires_at": TIME}, {}, "customer.get_risk_profile"),
    # ---- savings goals ----------------------------------------------------------------------------------------
    "wish_fund": obj({"id": ID, "account_id": ID, "name": {"type": "string"}, "balance": MONEY, "target_amount": MONEY,
                      "release_at": TIME, "status": {"type": "string"}, "version": VERSION}, {"customer_id": ID}, "wish_fund.list / get"),
    "merchant": obj({"id": ID, "name": {"type": "string"}, "item": {"type": "string"}, "price": POSITIVE, "status": {"type": "string"}},
                    {"category": {"type": "string"}}, "merchant.list"),
    "life_event": obj({"id": ID, "person": {"type": "string"}, "kind": {"enum": ["BIRTHDAY", "ANNIVERSARY"]},
                       "month": {"type": "integer", "minimum": 1, "maximum": 12}, "day": {"type": "integer", "minimum": 1, "maximum": 31}},
                      {"relation": {"type": "string"}, "customer_id": ID}, "life_event.list"),
    # ---- deposits / FX / gold / loans / credit account / funds ----------------------------------------------
    "finance_contract": obj({"id": ID, "customer_id": ID, "account_id": ID, "card_id": ID,
                             "type": {"enum": ["loan", "installment", "gold_buy", "gold_sell", "deposit_open", "deposit_withdraw",
                                               "deposit_renew", "fx_buy", "fx_sell", "card_charge", "card_refund", "card_repay",
                                               "card_surplus", "fund_subscribe", "fund_confirm", "fund_cancel", "fund_redeem"]},
                             "status": {"enum": ["DRAFT", "ACTIVE", "SETTLED", "COMPLETED", "ARCHIVED"]}, "version": VERSION,
                             "amount": MONEY, "remaining": MONEY, "paid_fees": MONEY, "schedule": {"type": "array"},
                             "created_at": TIME, "expires_at": TIME},
                            {"card_label": {"type": "string"}, "units": {"type": "integer"}, "price": MONEY, "quote": {"type": ["object", "null"]},
                             "terms": {"type": "string", "description": "银行的产品条款摘要，显示在确认卡上"},
                             "principal": MONEY, "term": {"type": "integer"}, "rate_bps": {"type": "integer"}, "opened": {"type": "string"},
                             "matures": {"type": "string"}, "deposit_id": ID, "foreign_currency": CURRENCY, "rate": {"type": "string"},
                             "product_id": ID, "order_id": ID, "entry_id": ID, "source_id": {"type": "string"}},
                            "finance.quote 的返回、finance.list.contracts 的每一项、finance.get.target。金额为本次需确认的金额；"
                            "DRAFT 为未执行的报价，到 expires_at 失效"),
    "finance_list": obj({"contracts": {"type": "array", "items": {"$ref": "finance_contract"}}, "deposits": {"type": "array"},
                         "fx_wallets": {"type": "array"}, "gold_positions": {"type": "array"}, "credit_states": {"type": "array"},
                         "eligible": {"type": "array"}, "outstanding": MONEY, "credit_limit": MONEY, "credit_card_limit": MONEY,
                         "deposit_rates": {"type": "object"}, "currencies": {"type": "array"}, "fund_products": {"type": "array"}},
                        {"rules": {"type": "string"}}, "finance.list：客户的全部合同、持仓与产品条款"),
    "finance_snapshot": obj({"target": {"$ref": "finance_contract"}, "card": {"$ref": "card"}},
                            {"position": {"type": ["object", "null"]}, "source": {"type": ["object", "null"]},
                             "risk_profile": {"type": ["object", "null"]}, "credit": {"type": "object"},
                             "deposit": {"type": ["object", "null"]}, "wallet": {"type": ["object", "null"]}, "advanced": {"type": "object"}},
                            "finance.get：合同及其执行所依赖的状态；就是签名动作的前置条件，执行前后都按它比对"),
    # ---- writes and verification ------------------------------------------------------------------------------
    "write_result": obj({"id": ID, "status": {"enum": STATUS}}, {"executed_at": TIME},
                        "写请求（BankRequest 带 action）的返回：银行流水号与状态。HTTP 成功只算 CREATED，不算 SETTLED"),
    "verification": obj({"status": {"enum": ["VERIFIED", "MANUAL_REVIEW"]}, "reason": {"type": "string"},
                         "provider_status": {"type": "string"}, "evidence_hash": {"type": "string"}},
                        {"executed_at": {"type": ["number", "null"]}},
                        "provider.verify(action, transaction_id)：只有银行记录与已签名动作逐项一致且已入账时才是 VERIFIED"),
}

# capability -> (schema of each item, is it a list)
READS = {
    "account.get": ("account", False), "account.get_balance": ("balance", False), "transaction.list": ("transaction", True),
    "transaction.recurring_history": ("recurring_charge", True), "payee.resolve": ("payee", True), "card.list": ("card", True),
    "subscription.list": ("subscription", True), "direct_debit.list": ("subscription", True), "wealth.list_products": ("product", True),
    "portfolio.get": ("portfolio", False), "customer.get_risk_profile": ("risk_profile", False), "wish_fund.list": ("wish_fund", True),
    "merchant.list": ("merchant", True), "life_event.list": ("life_event", True), "finance.list": ("finance_list", False),
}


def _type_ok(value, kind):
    kinds = kind if isinstance(kind, list) else [kind]
    for k in kinds:
        if (k == "string" and isinstance(value, str) or k == "boolean" and isinstance(value, bool)
                or k == "integer" and isinstance(value, int) and not isinstance(value, bool)
                or k == "number" and isinstance(value, (int, float)) and not isinstance(value, bool)
                or k == "object" and isinstance(value, dict) or k == "array" and isinstance(value, list) or k == "null" and value is None):
            return True
    return False


def validate(value, schema, path="$"):
    """Errors as a list of 'path: problem' strings (empty when valid). Supports the subset of JSON Schema used above."""
    import re
    if isinstance(schema, str):
        schema = SCHEMAS[schema]
    if "$ref" in schema:
        return validate(value, SCHEMAS[schema["$ref"]], path)
    errors = []
    if "type" in schema and not _type_ok(value, schema["type"]):
        return [f"{path}: 应为 {schema['type']}，实际是 {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: 取值 {value!r} 不在 {schema['enum']} 中")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} 小于 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} 大于 {schema['maximum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: 不能为空")
        if "pattern" in schema and not re.match(schema["pattern"], value):
            errors.append(f"{path}: {value!r} 格式不符（{schema['pattern']}）")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: 缺少必填字段")
        for key, sub in schema.get("properties", {}).items():
            if key in value and (value[key] is not None or "null" in (sub.get("type") if isinstance(sub.get("type"), list) else [sub.get("type")])):
                errors += validate(value[key], sub, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += validate(item, schema["items"], f"{path}[{i}]")
    return errors


def export(folder):
    """Write each schema as a standalone JSON Schema file (draft 2020-12) for bank engineers."""
    import json
    import os
    os.makedirs(folder, exist_ok=True)

    def standard(node):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                out[k] = ("#/$defs/" + v) if k == "$ref" else standard(v)
            return out
        if isinstance(node, list):
            return [standard(v) for v in node]
        return node
    defs = {name: standard(schema) for name, schema in SCHEMAS.items()}
    for name, schema in SCHEMAS.items():
        document = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"trusta-bank/{name}.json",
                    "title": name, **standard(schema), "$defs": {k: v for k, v in defs.items() if k != name}}
        with open(os.path.join(folder, name + ".json"), "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
    return sorted(SCHEMAS)
