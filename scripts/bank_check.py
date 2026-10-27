"""接入自检：用你的银行适配器检查它能否接入 TRUSTA，并给出一份逐项报告。

    python -m scripts.bank_check --config config/bank.partner.local.toml
    python -m scripts.bank_check --config ... --customer <客户号> --quote                 # 另外试一次报价（不动钱）
    python -m scripts.bank_check --config ... --write-test transfer --payee 张三 --amount 1   # 沙箱里真实转一笔并核验
    python -m scripts.bank_check --config ... --write-test deposit --amount 10000           # 沙箱里存一笔定期再支取
    python -m scripts.bank_check --config ... --report report.json                          # 同时写出 JSON 报告
    python -m scripts.bank_check --sandbox                                                  # 用内置沙箱银行演示本工具
    python -m scripts.bank_check --export-schemas docs/schemas                             # 导出数据格式的 JSON Schema

It runs against a throw-away TRUSTA database, never the operator's own data. Reads are always safe. Anything that writes
at the bank is opt-in, refused for a production environment, and goes through TRUSTA's full signed write path
(confirmation → authorization → bank → status query → independent verification → receipt), exactly as in use.

Checks:
  1. configuration and capability manifest
  2. every read the bank declares: answers, belongs to the customer, matches the data contract (backend/bank_sdk/schemas.py)
  3. which TRUSTA features light up, and why the others do not
  4. pages and conversation start against this bank
  5. optional: a quote (finance.quote) and real sandbox writes, verified
Exit code 0 when nothing failed.
"""
import argparse
import json
import os
import secrets
import sys
import tempfile
import time
from datetime import datetime

PASS, FAIL, SKIP, WARN = "通过", "失败", "未提供", "注意"


class Report:
    def __init__(self):
        self.items = []

    def add(self, section, name, status, detail=""):
        self.items.append({"section": section, "check": name, "status": status, "detail": detail})
        mark = {PASS: "✓", FAIL: "✗", SKIP: "–", WARN: "!"}[status]
        print(f"  {mark} {name}" + (f"：{detail}" if detail else ""))

    def section(self, title):
        print(f"\n【{title}】")

    @property
    def failed(self):
        return [i for i in self.items if i["status"] == FAIL]


def code(exc):
    return getattr(exc, "code", None) or type(exc).__name__


def months(n=2):
    from backend.business import CHINA
    today = datetime.now(CHINA)
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out


def load_provider(args, report, db):
    report.section("1 配置与能力清单")
    if args.sandbox:
        report.add("config", "加载适配器", PASS, "内置沙箱银行（演示用）")
        return "sandbox"
    if not args.config or not os.path.exists(args.config):
        report.add("config", "配置文件", FAIL, f"找不到 {args.config!r}；用 --config 指定，或 --sandbox 演示")
        return None
    from backend.bank_sdk.configuration import reference_from_config
    try:
        provider = reference_from_config(args.config, db)
    except Exception as exc:
        report.add("config", "加载适配器", FAIL, f"{code(exc)}（检查 provider / factory 写法，以及工厂函数返回的对象）")
        return None
    report.add("config", "加载适配器", PASS, type(provider).__name__)
    return provider


def check_manifest(provider, report):
    from backend.bank_sdk.contracts import BankCapabilityManifest, CANONICAL
    m = getattr(provider, "manifest", None)
    if not isinstance(m, BankCapabilityManifest):
        report.add("manifest", "能力清单类型", FAIL, "manifest 必须是 BankCapabilityManifest")
        return False
    report.add("manifest", "银行 / 环境", PASS, f"{m.provider} / {m.environment}")
    unknown = sorted(set(m.capabilities) - set(CANONICAL.values()))
    report.add("manifest", "能力都是规范能力", FAIL if unknown else PASS, ("未知能力：" + "、".join(unknown)) if unknown else f"共 {len(m.capabilities)} 项")
    report.add("manifest", "必需能力 accounts.read", PASS if "accounts.read" in m.capabilities else FAIL)
    report.add("manifest", "币种", PASS if m.currencies else FAIL, "、".join(sorted(m.currencies)) or "未声明")
    for method in ("request", "verify"):
        report.add("manifest", f"提供 {method}()", PASS if callable(getattr(provider, method, None)) else FAIL)
    if m.environment == "production":
        report.add("manifest", "生产环境", WARN, "本工具在生产环境只做读取，不做任何写入")
    return not unknown and "accounts.read" in m.capabilities


def build_service(provider, folder, config_path):
    from backend.config import Config
    from backend.service import TrustaService
    secret = secrets.token_hex(16)
    sandbox = provider == "sandbox"
    service = TrustaService(Config(db_path=os.path.join(folder, "check.db"), signing_key=secrets.token_bytes(32), communication_version=3,
                                   banking_version=5, strong_auth_secret=secret, bank_config_path=None if sandbox else (config_path or "bank-check")),
                            bank_provider=None if sandbox else provider)
    return service, secret


def check_reads(service, customer, report):
    from backend.bank_sdk.contracts import BankRequest
    from backend.bank_sdk.schemas import READS, validate
    report.section("2 读取与数据格式")
    g, provider = service.gateway, service.gateway._adapter.provider
    offers = g._adapter.manifest

    def checked(name, call, schema, many):
        started = time.time()
        try:
            value = call()
        except Exception as exc:
            report.add("reads", name, FAIL, code(exc))
            return None
        took = f"{(time.time() - started) * 1000:.0f} ms"
        items = value if isinstance(value, list) else [value] if value is not None else []
        problems = [p for item in items for p in validate(item, schema)]
        if problems:
            report.add("reads", name, FAIL, f"{len(problems)} 处格式问题，例如 " + "；".join(problems[:3]))
        else:
            report.add("reads", name, PASS, (f"{len(items)} 条，" if many else "") + took)
        return value

    if provider.manifest.environment == "local":  # the bundled sandbox lists accounts from its own records
        from backend.bank_sdk.discovery import customer_accounts
        accounts = checked("账户列表 accounts.read", lambda: customer_accounts(service, customer), "account_list_item", True) or []
    else:
        accounts = checked("账户列表 accounts.read", lambda: provider.request(BankRequest("accounts.read", customer)).data, "account_list_item", True) or []
    if not accounts:
        report.add("reads", "该客户至少有一个账户", FAIL, f"客户 {customer!r} 没有返回账户；用 --customer 指定沙箱里的客户号")
        return []
    account_id = accounts[0]["id"]
    account = checked(f"账户详情 account.get（{account_id}）", lambda: g.read("verifier", customer, "account.get", account_id=account_id), "account", False)
    if account and account.get("customer_id") != customer:
        report.add("reads", "账户归属", FAIL, f"account.get 返回的 customer_id={account.get('customer_id')!r}，应为 {customer!r}")
    if "account.get_balance" in offers:
        checked("余额 account.get_balance", lambda: g.read("transaction", customer, "account.get_balance", account_id=account_id), "balance", False)
    else:
        report.add("reads", "余额 balance.read", SKIP)
    if "transaction.list" in offers:
        for month in months():
            rows = checked(f"交易流水 transaction.list（{month}）", lambda month=month: g.read("finance", customer, "transaction.list", account_id=account_id, month=month), "transaction", True)
            if rows:
                credits = sum(1 for r in rows if r.get("direction") == "IN")
                if not any("direction" in r for r in rows):
                    report.add("reads", f"入账方向（{month}）", WARN, "流水里没有 direction 字段：入账明细与“工资到账”联动规则将看不到入账")
                elif credits:
                    report.add("reads", f"入账（{month}）", PASS, f"{credits} 笔入账")
    else:
        report.add("reads", "交易流水 transactions.read", SKIP)
    params = {"portfolio.get": None, "payee.resolve": {"name": None, "recipient_id": None}}
    actors = {"payee.resolve": "transaction", "wealth.list_products": "wealth", "customer.get_risk_profile": "wealth",
              "transaction.recurring_history": "finance"}
    for tool, (schema, many) in READS.items():
        if tool in {"account.get", "account.get_balance", "transaction.list", "portfolio.get"}:
            continue
        if tool not in offers:
            report.add("reads", tool, SKIP)
            continue
        kwargs = {"account_id": account_id} if tool == "transaction.recurring_history" else params.get(tool) or {}
        value = checked(tool, lambda tool=tool, kwargs=kwargs: g.read(actors.get(tool, "service"), customer, tool, **kwargs), schema, many)
        if tool == "wealth.list_products" and value and "portfolio.get" in offers:
            product = value[0]["id"]
            checked(f"portfolio.get（{product}）", lambda: g.read("wealth", customer, "portfolio.get", account_id=account_id, product_id=product), "portfolio", False)
    return accounts


def check_features(chat, customer, report):
    report.section("3 TRUSTA 功能点亮情况")
    for feature in chat.capabilities.public(customer)["features"]:
        status = feature["status"]
        report.add("features", feature["title"], PASS if status in {"AVAILABLE", "DEGRADED"} else SKIP,
                   "" if status == "AVAILABLE" else (feature.get("reason") or status))


def check_pages(chat, customer, report):
    report.section("4 页面与对话")
    from backend.chat.activity import ActivityFeed
    from backend.chat.pages import BillsPage, WealthPage
    from backend.bank_sdk.presentation import bank_overview
    for name, call in (("银行概览", lambda: bank_overview(chat.s, customer)), ("资金总览", lambda: ActivityFeed(chat).overview(customer)),
                       ("账单与订阅", lambda: BillsPage(chat).view(customer)), ("理财", lambda: WealthPage(chat, None).view(customer)),
                       ("新建对话", lambda: chat.create_session(customer))):
        try:
            value = call()
            gaps = value.get("unavailable") if isinstance(value, dict) else None
            report.add("pages", name, PASS, ("银行未提供：" + "、".join(gaps)) if gaps else "")
        except Exception as exc:
            report.add("pages", name, FAIL, code(exc))
    # Transfer up to the confirmation card (nothing is executed): catches payee / balance / policy contract problems.
    from backend.bank_sdk.discovery import supports, customer_accounts
    from backend.bank_sdk.presentation import bank_payees
    s = chat.s
    if supports(s, "transfer.create") and supports(s, "payee.resolve"):
        try:
            payees = bank_payees(s, customer)
            account = customer_accounts(s, customer)[0]
            if not payees:
                report.add("pages", "转账预检（到确认卡为止，不执行）", WARN, "该客户没有收款人，跳过")
                return
            task = s.create_normalized_task("bank_check dry run", customer, account["id"], {"task_type": "TRANSFER", "needs_clarification": False,
                                            "recipient_name": payees[0]["name"], "amount": 1, "currency": account["currency"]})
            ok = task["status"] == "WAITING_USER" and task.get("proposed_actions")
            report.add("pages", "转账预检（到确认卡为止，不执行）", PASS if ok else FAIL,
                       "" if ok else f"{task['status']} {task.get('errors')}")
            if ok:
                s.cancel_draft(task["task_id"], customer)
        except Exception as exc:
            report.add("pages", "转账预检（到确认卡为止，不执行）", FAIL, code(exc))


def debit_card(service, customer):
    from backend.bank_sdk.discovery import optional_read
    return next((c for c in optional_read(service, "service", customer, "card.list") if c.get("card_type") == "DEBIT" and c.get("status") == "ACTIVE"), None)


def check_quote(chat, customer, report):
    from backend.bank_sdk.discovery import supports
    from backend.bank_sdk.schemas import validate
    report.section("5a 报价（不动钱）")
    if not supports(chat.s, "finance.quote"):
        report.add("quote", "finance.quote", SKIP)
        return
    card = debit_card(chat.s, customer)
    if not card:
        report.add("quote", "finance.quote", WARN, "没有可用借记卡，跳过")
        return
    g = chat.s.gateway
    try:
        t = g.read("service", customer, "finance.quote", request={"type": "deposit_open", "card_id": card["id"], "amount": 10000, "term": 90})
        problems = validate(t, "finance_contract")
        report.add("quote", "定期存款报价", FAIL if problems else PASS, "；".join(problems[:3]) or f"草稿 {t['id']}，{t['amount']} 分，{t.get('rate_bps')} bp")
        snapshot = g.read("service", customer, "finance.get", resource_id=t["id"])
        problems = validate(snapshot, "finance_snapshot")
        report.add("quote", "finance.get 前置条件格式", FAIL if problems else PASS, "；".join(problems[:3]))
        archived = g.read("service", customer, "finance.archive", contract_id=t["id"])
        report.add("quote", "撤回草稿 finance.archive", PASS if archived.get("status") == "ARCHIVED" else FAIL, archived.get("status", ""))
    except Exception as exc:
        report.add("quote", "定期存款报价", FAIL, code(exc))


def run_task(service, customer, task, secret):
    from backend.domain import digest
    if "action_records" not in task:  # single-action task (transfer): authorised as a whole
        if task.get("status") != "WAITING_USER" or not task.get("proposed_actions"):
            return task, "NOT_WAITING:" + task.get("status", "")
        return service.authorize(task["task_id"], customer, digest(task["proposed_actions"][0]), True, secret), None
    record = next((r for r in task.get("action_records", []) if r.get("status") == "WAITING_USER"), None)
    if not record:
        return task, "NOT_WAITING:" + task.get("status", "")
    done = service.banking.authorize(task["task_id"], customer, digest(record["action"]), True, secret, record["action"]["action_id"])
    final = next((r for r in done.get("action_records", []) if r["action"]["action_id"] == record["action"]["action_id"]), {})
    # Verification and receipt live on the action record (banking runtime) or on the task (single-action transfer).
    done = dict(done, verification=final.get("verification") or done.get("verification"), receipt=final.get("receipt") or done.get("receipt"))
    return done, None


def write_tests(args, service, chat, customer, secret, accounts, report):
    report.section("5b 沙箱写入（真实执行并核验）")
    m = service.gateway._adapter.provider.manifest
    if m.environment == "production":
        report.add("writes", "写入测试", SKIP, "生产环境不做写入")
        return
    if args.write_test == "transfer":
        if not args.payee or not args.amount:
            report.add("writes", "转账", FAIL, "需要 --payee 收款人姓名 与 --amount 金额（最小币种单位）")
            return
        account = args.account or accounts[0]["id"]
        currency = next((a["currency"] for a in accounts if a["id"] == account), "CNY")
        try:
            task = service.create_normalized_task("bank_check transfer", customer, account, {"task_type": "TRANSFER", "needs_clarification": False,
                                                                                          "recipient_name": args.payee, "amount": args.amount, "currency": currency})
            done, problem = run_task(service, customer, task, secret)
        except Exception as exc:
            report.add("writes", "转账", FAIL, code(exc))
            return
        verification = done.get("verification") or {}
        ok = done.get("status") == "COMPLETED" and verification.get("status") == "VERIFIED" and done.get("receipt")
        report.add("writes", "转账 → 执行 → 核验 → 回执", PASS if ok else FAIL,
                   problem or f"任务 {done.get('status')}，核验 {verification.get('status')}（{verification.get('reason', '')}）")
    elif args.write_test == "deposit":
        from backend.banking.finance import Finance
        from backend.banking.extensions import Extensions
        card = debit_card(service, customer)
        if not card:
            report.add("writes", "定期存款", FAIL, "没有可用借记卡")
            return
        try:
            f = Finance(chat)
            for label, request, op in (("存入定期", {"type": "deposit_open", "card_id": card["id"], "amount": args.amount or 10000, "term": 90}, "DEPOSIT_OPEN"),):
                t = f.create(customer, request)["contract"]
                task = service.banking.create(label, customer, t["account_id"], {"task_type": "FINANCE_MANAGEMENT", "operation": op, "target_id": t["id"],
                                                                                 "parameters": {"amount": t["amount"], "currency": "CNY"}})
                done, problem = run_task(service, customer, task, secret)
                v = done.get("verification") or {}
                report.add("writes", f"{label} → 核验 → 回执", PASS if done.get("status") == "COMPLETED" and v.get("status") == "VERIFIED" and done.get("receipt") else FAIL,
                           problem or f"任务 {done.get('status')}，核验 {v.get('status')}")
            deposit = next((d for d in Extensions(chat).view(customer)["deposits"] if d["status"] == "ACTIVE"), None)
            if deposit:
                t = f.create(customer, {"type": "deposit_withdraw", "deposit_id": deposit["id"]})["contract"]
                task = service.banking.create("支取定期", customer, t["account_id"], {"task_type": "FINANCE_MANAGEMENT", "operation": "DEPOSIT_WITHDRAW",
                                                                                     "target_id": t["id"], "parameters": {"amount": t["amount"], "currency": "CNY"}})
                done, problem = run_task(service, customer, task, secret)
                report.add("writes", "支取定期（还原）", PASS if done.get("status") == "COMPLETED" else FAIL, problem or done.get("status", ""))
        except Exception as exc:
            report.add("writes", "定期存款", FAIL, code(exc))


def main(argv=None):
    parser = argparse.ArgumentParser(description="TRUSTA 银行接入自检")
    parser.add_argument("--config", default=os.environ.get("TRUSTA_BANK_CONFIG"), help="银行配置文件（provider = \"module\" 等）")
    parser.add_argument("--sandbox", action="store_true", help="用内置沙箱银行演示本工具")
    parser.add_argument("--customer", help="客户号（默认取适配器的 customer_id）")
    parser.add_argument("--quote", action="store_true", help="另外试一次报价并撤回（不动钱）")
    parser.add_argument("--write-test", choices=["transfer", "deposit"], help="在沙箱环境真实写入一次并核验")
    parser.add_argument("--payee"), parser.add_argument("--amount", type=int), parser.add_argument("--account")
    parser.add_argument("--report", help="把结果另存为 JSON")
    parser.add_argument("--export-schemas", metavar="DIR", help="导出数据格式的 JSON Schema 后退出")
    args = parser.parse_args(argv)
    os.environ.setdefault("TRUSTA_DEMO_MODE", "false")
    if args.export_schemas:
        from backend.bank_sdk.schemas import export
        names = export(args.export_schemas)
        print(f"已导出 {len(names)} 个 JSON Schema 到 {args.export_schemas}")
        return 0
    report = Report()
    print("TRUSTA 银行接入自检" + ("（内置沙箱银行演示）" if args.sandbox else ""))
    with tempfile.TemporaryDirectory() as folder:
        from backend.persistence import Database
        provider = load_provider(args, report, Database(os.path.join(folder, "adapter.db")))
        ready = provider is not None and (provider == "sandbox" or check_manifest(provider, report))
        if ready:
            customer = args.customer or getattr(provider, "customer_id", None) or "demo"
            try:
                service, secret = build_service(provider, folder, None if args.sandbox else args.config)
                if provider == "sandbox":
                    check_manifest(service.gateway._adapter.provider, report)
                from backend.chat.service import ChatService
                chat = ChatService(service, _OfflineModel())
            except Exception as exc:
                report.add("config", "启动 TRUSTA", FAIL, code(exc))
            else:
                accounts = check_reads(service, customer, report)
                check_features(chat, customer, report)
                if accounts:
                    check_pages(chat, customer, report)
                    if args.quote:
                        check_quote(chat, customer, report)
                    if args.write_test:
                        write_tests(args, service, chat, customer, secret, accounts, report)
    counts = {s: sum(1 for i in report.items if i["status"] == s) for s in (PASS, FAIL, WARN, SKIP)}
    print(f"\n结果：通过 {counts[PASS]} 项，失败 {counts[FAIL]} 项，注意 {counts[WARN]} 项，银行未提供 {counts[SKIP]} 项。")
    print("失败项需要修改适配器；“银行未提供”只表示对应功能不会出现，不影响其他功能。" if counts[FAIL] else "可以接入：未提供的能力对应的功能会自动隐藏。")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump({"generated_at": time.time(), "summary": counts, "items": report.items}, handle, ensure_ascii=False, indent=2)
        print(f"报告已写入 {args.report}")
    return 1 if counts[FAIL] else 0


class _OfflineModel:
    """The checker never calls a language model."""
    available = False

    def complete(self, *args, **kwargs):
        raise RuntimeError("offline")


if __name__ == "__main__":
    sys.exit(main())
