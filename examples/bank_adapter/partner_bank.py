"""银行适配器模板：把它复制到你自己的包里，按注释把 `_api_*` 函数换成调用你们银行的接口。

开箱即可运行：模板自带一份内存里的示例数据，`python -m scripts.bank_check --config examples/bank_adapter/partner-bank.example.toml
--write-test transfer --payee 张三 --amount 100` 会全部通过。你每替换一处真实接口，就再跑一次自检，看哪一项还没对上。

适配器只做两件事：
  1. 把 TRUSTA 的规范请求（BankRequest）翻译成你们银行的接口调用；
  2. 把银行的返回整理成规范字段（字段约定见 docs/bank-data-contract.md，机器可校验的格式见 backend/bank_sdk/schemas.py）。
授权、风控、签名、核验、回执都在 TRUSTA 里，适配器不需要、也不能绕过它们。

本模板声明的能力：账户、余额、流水、收款人、转账与转账状态。要开放更多业务（卡片、订阅、理财、存款……），在 CAPABILITIES
里加上对应的规范能力，并在 HANDLERS 里加处理函数；TRUSTA 会自动点亮对应功能。
"""
import copy
import threading
import time
from datetime import datetime, timezone

from backend.bank_sdk.contracts import (BankCapabilityManifest, BankError, BankResponse, BankVerificationResult,
                                        ExternalBankSafeMode)
from backend.domain import digest, uid

PROVIDER = "partner-bank"
CAPABILITIES = frozenset({"accounts.read", "balance.read", "transactions.read", "payees.read", "payments.create", "payments.status"})

# ---- 示例数据：替换成真实接口后删除 -----------------------------------------------------------------------------
_EXAMPLE = {
    "accounts": [{"id": "acct-1001", "customer_id": "demo", "currency": "CNY", "balance": 1_000_000, "frozen": False, "version": 0,
                  "name": "活期账户"}],
    "payees": [{"id": "payee-2001", "customer_id": "demo", "name": "张三"}],
    "transactions": [
        {"id": "tx-1", "account_id": "acct-1001", "merchant": "示例超市", "category": "GROCERIES", "amount": 8_800, "currency": "CNY",
         "occurred_at": time.time() - 5 * 86400, "kind": "PURCHASE", "status": "SETTLED", "direction": "OUT"},
        {"id": "tx-2", "account_id": "acct-1001", "merchant": "", "payer": "示例科技有限公司", "memo": "工资", "category": "INCOME",
         "amount": 1_200_000, "currency": "CNY", "occurred_at": time.time() - 2 * 86400, "kind": "CREDIT", "status": "SETTLED", "direction": "IN"},
    ],
    "payments": {},       # 银行流水号 -> 付款记录
    "idempotency": {},    # 幂等键 -> (动作哈希, 银行流水号)
}


class PartnerBank:
    def __init__(self, raw, db):
        environment = raw.get("environment", "sandbox")
        if environment not in {"sandbox", "production"}:
            raise ValueError("environment must be sandbox or production")
        self.customer_id = raw["customer_id"]
        self.manifest = BankCapabilityManifest(PROVIDER, environment, CAPABILITIES, frozenset({"CNY"}), asynchronous=False,
                                               rate_limit_per_second=5)
        # 写入安全阀（TRUSTA 的银行概览会显示它）：默认关闭；只允许白名单账户、白名单收款人、单笔上限以内。
        self.safe_mode = ExternalBankSafeMode(environment, bool(raw.get("writes_enabled", False)) and environment == "sandbox",
                                              frozenset(raw.get("source_accounts", [])), frozenset(raw.get("payees", [])),
                                              int(raw.get("maximum_amount", 100)))
        self._data = copy.deepcopy(_EXAMPLE)
        self._lock = threading.Lock()
        # self._session = 你们银行 SDK / HTTP 客户端；凭据从环境变量读取，例如 os.environ[raw["token_env"]]

    # ---- TRUSTA 调用的唯一入口 -----------------------------------------------------------------------------------
    def request(self, req):
        if req.capability not in self.manifest.capabilities:
            raise BankError("BANK_CAPABILITY_UNAVAILABLE")
        if req.customer_id != self.customer_id:
            raise BankError("ACCOUNT_ACCESS_DENIED")      # 只服务已授权的客户
        tool = req.parameters.get("_tool")
        params = {k: v for k, v in req.parameters.items() if k != "_tool"}
        handler = HANDLERS.get((req.capability, tool))
        if handler is None:
            raise BankError("BANK_CAPABILITY_UNAVAILABLE")
        data, status = getattr(self, handler)(req, params)
        return BankResponse(data, PROVIDER, self.manifest.environment, status)

    # ---- 读取 ------------------------------------------------------------------------------------------------------
    def list_accounts(self, req, params):
        rows = self._api_list_accounts(req.customer_id)          # ← 换成：GET /accounts
        return [{k: r[k] for k in ("id", "currency", "balance", "frozen", "name") if k in r} for r in rows], None

    def get_account(self, req, params):
        row = self._api_get_account(params["account_id"])         # ← 换成：GET /accounts/{id}
        if row is None or row["customer_id"] != req.customer_id:
            raise BankError("ACCOUNT_ACCESS_DENIED")
        return {k: row[k] for k in ("id", "customer_id", "currency", "balance", "frozen", "version", "name")}, None

    def get_balance(self, req, params):
        row, _ = self.get_account(req, params)
        return {"id": row["id"], "balance": row["balance"], "currency": row["currency"], "version": row["version"]}, None

    def list_transactions(self, req, params):
        self.get_account(req, {"account_id": params["account_id"]})
        start, end = _month(params["month"])
        rows = self._api_list_transactions(params["account_id"], start, end)   # ← 换成：GET /transactions?from=&to=（翻完所有页）
        return sorted(rows, key=lambda r: (r["occurred_at"], r["id"])), None

    def resolve_payee(self, req, params):
        # 返回匹配的收款人列表（TRUSTA 要求恰好一个才会继续转账）；姓名与 recipient_id 都为空时返回全部收款人。
        name, recipient_id = params.get("name"), params.get("recipient_id")
        rows = [p for p in self._api_list_payees(req.customer_id)              # ← 换成：GET /payees（可按姓名、手机号、备注名查）
                if (recipient_id is None or p["id"] == recipient_id) and (name is None or name in (p["name"], p.get("phone"), p.get("alias")))]
        return [{"id": p["id"], "name": p["name"], "customer_id": req.customer_id} for p in rows], None

    def get_payment(self, req, params):
        payment = self._api_get_payment(params["transaction_id"])              # ← 换成：GET /payments/{id}
        if payment is None:
            raise BankError("TRANSACTION_NOT_FOUND")
        return payment, payment["status"]

    # ---- 写入：转账 ------------------------------------------------------------------------------------------------
    def create_payment(self, req, params):
        action = req.action
        self.safe_mode.check(action)                              # 白名单、单笔上限、写入开关
        if time.time() >= req.deadline:
            raise BankError("BANK_EXECUTION_EXPIRED")
        with self._lock:
            prior = self._data["idempotency"].get(req.idempotency.key)
            if prior:                                             # 同一幂等键只执行一次；哈希不同说明动作被改过
                if prior[0] != req.idempotency.action_hash:
                    raise BankError("IDEMPOTENCY_CONFLICT")
                payment = self._data["payments"][prior[1]]
                return payment, payment["status"]
            # 前置条件：账户版本必须与签名动作里的一致，否则说明账户已变化，拒绝执行
            account = self._api_get_account(action["source_account_id"])
            if account["version"] != action["preconditions"]["account_version"]:
                raise BankError("BANK_PRECONDITION_CHANGED")
            payment = self._api_create_payment(action, req.idempotency.key)    # ← 换成：POST /payments（带幂等键）
            self._data["idempotency"][req.idempotency.key] = (req.idempotency.action_hash, payment["id"])
            return payment, payment["status"]

    # ---- 核验：只有银行记录与已签名动作逐项一致且已入账，才是 VERIFIED --------------------------------------------
    def verify(self, action, transaction_id):
        payment = self._api_get_payment(transaction_id)
        match = bool(payment and payment["status"] == "SETTLED" and payment["source_account_id"] == action["source_account_id"]
                     and payment["recipient_id"] == action["recipient_id"] and payment["amount"] == action["amount"]
                     and payment["currency"] == action["currency"] and payment.get("idempotency_key") == action["idempotency_key"])
        return BankVerificationResult("VERIFIED" if match else "MANUAL_REVIEW", "BANK_RECORD_MATCH" if match else "BANK_RECORD_MISMATCH",
                                      payment["status"] if payment else "UNKNOWN", digest(payment), payment.get("executed_at") if match else None)

    # ==== 以下 `_api_*` 是要替换成真实接口的地方；返回值保持同样的字段即可 =============================================
    def _api_list_accounts(self, customer_id):
        return [a for a in self._data["accounts"] if a["customer_id"] == customer_id]

    def _api_get_account(self, account_id):
        return next((a for a in self._data["accounts"] if a["id"] == account_id), None)

    def _api_list_transactions(self, account_id, start, end):
        return [t for t in self._data["transactions"] if t["account_id"] == account_id and start <= t["occurred_at"] < end]

    def _api_list_payees(self, customer_id):
        return [p for p in self._data["payees"] if p["customer_id"] == customer_id]

    def _api_get_payment(self, payment_id):
        return self._data["payments"].get(payment_id)

    def _api_create_payment(self, action, idempotency_key):
        account = self._api_get_account(action["source_account_id"])
        if account["balance"] < action["amount"]:
            raise BankError("INSUFFICIENT_BALANCE")
        account["balance"] -= action["amount"]
        account["version"] += 1
        payment = {"id": "pay-" + uid()[:12], "source_account_id": action["source_account_id"], "recipient_id": action["recipient_id"],
                   "amount": action["amount"], "currency": action["currency"], "status": "SETTLED", "executed_at": time.time(),
                   "idempotency_key": idempotency_key}
        self._data["payments"][payment["id"]] = payment
        return payment


# (规范能力, 规范工具名) -> 处理方法名。_tool 为 None 的 accounts.read 表示“列出全部账户”。
HANDLERS = {
    ("accounts.read", None): "list_accounts",
    ("accounts.read", "account.get"): "get_account",
    ("balance.read", "account.get_balance"): "get_balance",
    ("transactions.read", "transaction.list"): "list_transactions",
    ("payees.read", "payee.resolve"): "resolve_payee",
    ("payments.status", "transaction.get"): "get_payment",
    ("payments.create", "transfer.create"): "create_payment",
}


def _month(month):
    year, number = (int(x) for x in month.split("-"))
    start = datetime(year, number, 1, tzinfo=timezone.utc).timestamp()
    end = datetime(year + (number == 12), number % 12 + 1, 1, tzinfo=timezone.utc).timestamp()
    return start, end


def create(raw, db):
    """工厂函数：配置文件里 factory = "examples.bank_adapter.partner_bank:create"。"""
    return PartnerBank(raw, db)
