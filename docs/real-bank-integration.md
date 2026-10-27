# 接入真实银行

TRUSTA 默认连接内置的沙箱银行，演示数据都是虚构的。接入真实银行时，**不需要修改 TRUSTA 的代码**：写一个银行适配器（Provider），在配置文件里指向它即可。之后 TRUSTA 只通过标准银行接口与银行通信，按银行实际提供的能力决定哪些功能可用。

本文说明：

1. 银行需要提供哪些接口，每个接口对应 TRUSTA 的哪些功能；
2. 如何写适配器、如何配置；
3. 存款、外汇、黄金、基金、贷款与信用账户这几类业务的接口约定；
4. 如何验证接入后功能真实可用。

协议细节（签名、幂等、状态词汇、核验规则）见 [Bank Integration SDK](bank-integration-sdk.md)。

---

## 1. 原则

- **银行是唯一权威。** 余额、账户、卡片、订阅、理财产品等页面和对话里的数据都通过银行接口读取，不读本地沙箱表。`tests/test_real_bank_readiness.py` 会检查这一点：对话层出现对沙箱表的直接读取时，测试失败。
- **按能力启用功能。** 适配器在 `manifest` 里声明它真正支持的能力。没有声明的能力：
  - 对应工具从大模型的工具列表中移除，模型看不到；
  - 对话校验器拒绝这些工具；
  - 银行网关再独立拒绝一次；
  - 功能页标为"不可用"，并说明缺少哪个接口。
- **缺失时降级。** 银行少一项能力时，相关页面照常打开，对应区块显示"你接入的银行没有通过接口提供：……"。
- **资金写入走完整安全链。** 链路为：确认卡 → 签名授权 → 执行合约 → 网关 → 银行 → 回查状态 → 独立核验 → 回执 → 审计。适配器拿不到绕过这条链的入口。

## 2. 能力与功能对照

"必需"表示没有这项能力 TRUSTA 无法运行，其余能力都是可选的。

| 银行能力（规范名） | 对应 TRUSTA 读写 | 点亮的功能 / 页面 | |
|---|---|---|---|
| `accounts.read` | 账户列表、`account.get` | 所有页面的账户、资金总览、新建对话 | **必需** |
| `balance.read` | `account.get_balance` | 余额查询、理财规划里的备用金、余额触发的自动化 | 建议 |
| `transactions.read` | `transaction.list`（按月） | 消费查询与账单分析、总览本月消费、**周期扣费识别**（取近 6 个月流水推算） | 建议 |
| `transactions.recurring.read` | `transaction.recurring_history` | 更精确的周期扣费识别（银行自带周期扣费数据时） | 可选 |
| `payees.read` / `payees.create` | `payee.resolve` / `payee.add` | 转账收款人、AA 分摊 / 新增收款人 | 可选 |
| `payments.create` + `payments.status` | `transfer.create` / `transaction.get` | 转账（写），执行后回查状态并独立核验 | 可选 |
| `cards.read` 及 `cards.freeze / unfreeze / report_lost / limit.change` | `card.*` | 卡片管理、总览中的卡片 | 可选 |
| `cards.controls.update` | `card.transaction_control` | 交易开关、出行管家、自动收紧规则 | 可选 |
| `subscriptions.read / cancel`，`direct_debits.read / disable` | `subscription.*`，`direct_debit.*` | 账单与订阅页的登记订阅 / 代扣、对话取消 | 可选 |
| `investments.products.read / portfolio.read / purchase / redeem` | `wealth.list_products`，`portfolio.get`，`investment.*` | 理财页货架、持仓、为你推荐、申购赎回 | 可选 |
| `customers.risk.read / update` | `customer.get_risk_profile`，`risk_profile.update` | 风险测评、理财适当性校验 | 可选 |
| `savings.goals.*` | `wish_fund.*` | 心愿金 | 可选 |
| `merchants.read`，`customers.events.read` | `merchant.list`，`life_event.list` | 心愿金下单商户、纪念日计划 | 可选 |
| `operations.read / evidence.read` | `operation.*` | 回执详情里的银行凭证 | 可选 |
| `finance.read` + `finance.quote` + `finance.deposit_*` | `finance.list / get / quote / archive`，`finance.deposit_open / withdraw / renew` | 定期存款 | 可选 |
| 同上 + `finance.fx_buy / fx_sell` | | 外币兑换 | 可选 |
| 同上 + `finance.gold_buy / gold_sell` | | 黄金买卖、黄金定投 | 可选 |
| 同上 + `finance.fund_subscribe / confirm / cancel / redeem` | | 基金份额 | 可选 |
| 同上 + `finance.loan_draw / installment_start / credit_repay / credit_settle` | | 贷款与消费分期 | 可选 |
| 同上 + `finance.card_charge / card_refund / card_repay / card_surplus` | | 信用账单 | 可选 |
| 入账推送（webhook） | 推送 → 只触发查询 | 入账明细、工资到账触发的自动化 | 可选 |

以下功能不依赖银行接口，接任何银行都可用：生活服务跳转链接、黄金与外汇参考行情（来自公开数据源）、规则问答、反诈冷静期、强验证、自动化规则引擎、会话记录与审计链。

## 3. 存款、外汇、黄金、基金、贷款与信用账户

这六类业务共用一套"报价 → 确认 → 执行"的接口。TRUSTA 不保存这些业务的账，持仓、存单、合同、账单都在银行那一侧，TRUSTA 每次都向银行查询。

| 步骤 | 接口 | 作用 | 是否动钱 |
|---|---|---|---|
| 查询 | `finance.list` | 返回客户的全部合同、存单、外币、黄金持仓、信用账户和基金订单，以及产品条款 | 否 |
| 报价 | `finance.quote` | TRUSTA 提交业务请求，银行按自己的价格和规则生成一份**草稿合同**（含金额、到期时间） | 否 |
| 撤回 | `finance.archive` | 撤回一份还没执行的草稿 | 否 |
| 核对 | `finance.get` | 返回一份合同及其执行所依赖的状态，作为签名动作的前置条件 | 否 |
| 执行 | `finance.<操作>` | 例如 `finance.deposit_open`，走完整签名写路径：确认卡 → 授权 → 银行执行 → 回查 → 独立核验 | **是** |

报价请求（`finance.quote` 的 `request`）按业务分别为：

| 业务 | `type` 与字段 | 之后执行的操作 |
|---|---|---|
| 定期存款 | `deposit_open`：`card_id`, `amount`, `term`（90 / 180 / 365 天）；`deposit_withdraw` / `deposit_renew`：`deposit_id` | `DEPOSIT_OPEN` / `DEPOSIT_WITHDRAW` / `DEPOSIT_RENEW` |
| 外币兑换 | `fx_buy` / `fx_sell`：`card_id`, `currency`（USD / EUR / JPY / HKD）, `units`（外币最小单位）, `quote_mode` | `FX_BUY` / `FX_SELL` |
| 黄金 | `gold_buy` / `gold_sell`：`card_id`, `units`（0.01 克）；定投可附 `reference`（TRUSTA 的计划编号，银行原样保存） | `GOLD_BUY` / `GOLD_SELL` |
| 贷款与分期 | `loan`：`card_id`, `amount`, `months`；`installment`：`card_id`, `source_id`（一笔已入账消费）, `months` | `LOAN_DRAW` / `INSTALLMENT_START`，之后对同一合同 `CREDIT_REPAY` / `CREDIT_SETTLE` |
| 信用账户 | `card_charge` / `card_refund` / `card_repay` / `card_surplus`，字段见 `backend/banking/finance_desk.py` | 同名操作 |
| 基金 | `fund_subscribe` / `fund_confirm` / `fund_cancel` / `fund_redeem` | 同名操作 |

银行可以只开放其中几类业务，只要在能力清单里声明它实际支持的那几项：

- 声明了的业务：功能页显示"可用"，对应页面标签出现。
- 没有声明的业务：页面标签隐藏；对话里提出时会说明"你接入的银行没有提供"；TRUSTA 不会向银行请求报价。

黄金定投计划是客户自己的安排，保存在 TRUSTA。每期到期时，TRUSTA 向银行申请一次普通的黄金报价。暂停或取消计划时，TRUSTA 会撤回这一期还没执行的报价，已经准备好的签名动作因此失效，不会被误执行。

**接入时需要注意：**

- **前置条件格式。** `finance.get` 返回的合同和相关状态，就是签名动作的前置条件。TRUSTA 会用同一套规则（`backend/banking/finance.py` 里的 `errors` / `expected`，以及 `extensions.py`、`advanced.py` 里的同名函数）计算预期结果，银行执行前后都要与之一致。适配器需要把银行自己的数据整理成这些字段。
- **记账凭证格式。** 独立核验会读取 `operations.evidence.read` 返回的记账分录，核对资金方向和金额。沙箱银行使用一个清算账户 `finance-clearing`，真实银行的适配器需要按同样的借贷格式返回凭证。
- **产品条款以银行为准。** 利率、期限、额度、费用都由银行在报价里给出。沙箱银行的数值只是演示条款。

## 4. 写一个适配器

适配器是一个普通的 Python 对象，需要提供三样东西：

```python
from backend.bank_sdk.contracts import BankCapabilityManifest, BankResponse, BankError

class PartnerBank:
    def __init__(self, raw, db):
        self.customer_id = raw["customer_id"]
        self.manifest = BankCapabilityManifest(
            provider="partner-bank", environment="sandbox",           # 或 "production"
            capabilities=frozenset({"accounts.read", "balance.read", "transactions.read"}),
            currencies=frozenset({"CNY"}), asynchronous=False, rate_limit_per_second=5)

    def request(self, req):                                           # BankRequest -> BankResponse
        tool = req.parameters.get("_tool")                            # 规范工具名，例如 "account.get"
        if req.capability not in self.manifest.capabilities:
            raise BankError("BANK_CAPABILITY_UNAVAILABLE")
        if req.capability == "accounts.read" and tool is None:        # 列出该客户的全部账户
            return BankResponse(self._list_accounts(req.customer_id), "partner-bank", "sandbox")
        ...                                                           # 其余按下面的数据约定返回
        
    def verify(self, action, transaction_id):                        # 写操作后的银行侧核验
        ...

def create(raw, db):                                                  # 工厂函数
    return PartnerBank(raw, db)
```

配置文件（例如 `config/bank.partner.local.toml`，已被 `.gitignore` 忽略），并在 `.env` 中设置 `TRUSTA_BANK_CONFIG=config/bank.partner.local.toml`：

```toml
provider = "module"
factory = "partner_bank.adapter:create"   # 你的模块:工厂函数
customer_id = "..."                        # 其余字段原样传给工厂
```

TRUSTA 启动时会检查：

- `manifest` 必须是 `BankCapabilityManifest`；
- 环境必须是 `sandbox` 或 `production`；
- 声明的能力必须都是规范能力，且包含 `accounts.read`；
- 适配器必须提供 `request` 和 `verify`。

任一项不满足就拒绝启动，报 `INVALID_BANK_PROVIDER_CONFIGURATION`。

已有参考实现：

- `backend/bank_sdk/revolut.py`：Revolut 沙箱只读，已实测。
- `backend/bank_sdk/revolut_transfer.py`：小额付款，已实测。
- `backend/bank_sdk/starling.py`：通过协议测试，未连官方环境。

### 数据约定

所有金额都用**整数最小币种单位**（例如分），时间用 Unix 秒。厂商自有的字段在适配器内部转换成下面的规范字段，不要原样透传给 TRUSTA。

| 读取 | 返回字段 |
|---|---|
| 账户列表 / `account.get` | `id`, `customer_id`, `currency`, `balance`, `frozen`，可选 `name` |
| `account.get_balance` | `id`（账户号）, `balance`, `currency` |
| `transaction.list` | 列表：`id`, `account_id`, `merchant`, `category`, `amount`（>0）, `currency`, `occurred_at`, `kind`（`PURCHASE` / `REFUND` / 其他）, `status`（`SETTLED` 等） |
| `card.list` | `id`, `account_id`, `card_type`, `status`, `spending_limit`, `online_enabled`, `international_enabled`, `contactless_enabled` |
| `subscription.list` / `direct_debit.list` | `id`, `account_id`, `merchant`, `amount`, `currency`, `frequency`（`MONTHLY` 等）, `next_charge_at`, `status`, `cancelable` |
| `wealth.list_products` | `id`, `name`, `risk_level`（1–5）, `minimum_amount`, `term_days`, `currency`, `active`, `risk_note`, `restrictions{purchase_enabled, redemption_enabled}`；可选 `issuer`, `category`, `benchmark`, `liquidity`, `description`（用于页面展示，优先于本地资料） |
| `portfolio.get` | `amount_cents`, `redeemable_at` |

错误一律抛出 `BankError(稳定错误码)`。写请求结果不确定（超时、5xx、响应损坏）时抛出 `UncertainBankWrite`。TRUSTA 会把这类写入记为待人工核对，**不会自动重发**。

## 5. 验证接入有效

1. `python -m scripts.setup --probe`：用配置的凭据真实调用一次账户与余额读取。
2. 打开"功能"页：确认点亮的功能和你的银行能力一致。不可用的功能会写明缺哪个接口。
3. 依次打开资金总览、账单与订阅、理财，在对话里问"查一下余额""这个月花了多少钱""我有哪些订阅"：数据应来自你的银行。
4. 回归测试：`python -m pytest tests/test_real_bank_readiness.py`。测试通过配置文件加载一个"外部银行"，它的数据在 TRUSTA 读不到的独立数据库里，并且本地沙箱表已清空。测试分两种情况：
   - **全功能**：各页面和对话正常工作，转账生成确认卡，周期扣费能从普通流水中识别出来。定期存款、外币、黄金、贷款、基金、信用消费逐项完整办理一遍，并核对**银行账本**里的余额、存单和持仓都已变化，TRUSTA 自己的数据库里没有这些账；
   - **部分业务（银行不提供黄金和基金）**：这两项显示不可用、拒绝报价，其余业务照常；
   - **只读（与 Revolut 只读一致）**：各页面正常打开，不支持的区块标注"银行未提供"，转账、存款等功能明确说明不可用。

   可以仿照它，用你自己的适配器跑一遍同样的检查。
