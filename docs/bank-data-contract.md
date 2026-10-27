# 银行数据格式规范

本文规定银行适配器与 TRUSTA 之间交换的每一种数据：读取结果、写入结果和核验结果。

- 机器可校验的版本在 `backend/bank_sdk/schemas.py`。
- 导出为标准 JSON Schema（draft 2020-12）：`python -m scripts.bank_check --export-schemas docs/schemas`。
- 接入自检工具 `python -m scripts.bank_check` 会按这些格式逐项检查你的适配器，见[接入真实银行](real-bank-integration.md)第 5 节。

字段表由 `schemas.py` 生成，以代码为准。

## 通用约定

- **金额**：整数，单位为该币种的最小单位（人民币为分）。不得使用小数或字符串。
- **时间**：Unix 秒，可以是整数或小数。日期类字段（如存单到期日）用 `YYYY-MM-DD`。
- **币种**：ISO 4217 三位大写代码。TRUSTA 不做隐式换汇，混币种汇总会被拒绝。
- **布尔值**：`true` / `false`，也接受 `1` / `0`。
- **多余字段**：允许出现，TRUSTA 会忽略。银行自有字段（内部流水号、渠道码等）要在适配器里转换成下面的规范字段，不要原样透传。
- **版本号 `version`**：资源每变化一次就加一。签名动作的前置条件会带上版本号，执行前版本不一致说明状态已变化，必须拒绝执行。
- **错误**：一律抛出 `BankError(稳定错误码)`，例如 `ACCOUNT_ACCESS_DENIED`、`BANK_CAPABILITY_UNAVAILABLE`、`INSUFFICIENT_BALANCE`。写请求结果不确定（超时、5xx、响应损坏）时抛出 `UncertainBankWrite`。

## 写入与核验

写请求是带 `action` 的 `BankRequest`。其中：
- `action` 是已签名的动作；
- `idempotency.key` 是幂等键，`idempotency.action_hash` 是动作哈希；
- `deadline` 是执行截止时间。

适配器必须做到：

1. **幂等**：同一幂等键只执行一次。再次收到同一幂等键时，返回第一次的结果；如果动作哈希不同，抛出 `IDEMPOTENCY_CONFLICT`。
2. **前置条件**：执行前核对动作里的 `preconditions`（例如转账的 `account_version`）。与银行当前状态不一致时，抛出 `BANK_PRECONDITION_CHANGED`，不得执行。
3. **截止时间**：超过 `deadline` 时抛出 `BANK_EXECUTION_EXPIRED`。
4. **返回**：返回 `write_result`，包括银行流水号和状态。HTTP 请求成功只算 `CREATED`，不能直接当作 `SETTLED`。

执行后，TRUSTA 调用 `provider.verify(action, transaction_id)`，期望得到 `verification`：
- 只有银行记录与已签名动作的账户、收款人、金额、币种、幂等键逐项一致，并且已经入账，才能返回 `VERIFIED`；
- 其他情况一律返回 `MANUAL_REVIEW`。

TRUSTA 只对 `VERIFIED` 的操作生成回执；`MANUAL_REVIEW` 的操作会进入人工核对，并且不会自动重发。

## 存款、外汇、黄金、贷款、信用账户、基金的前置条件

这六类业务的签名动作，以 `finance.get` 的返回作为前置条件。TRUSTA 会用规则函数检查能否办理，并计算办理后的预期状态，所以下面列出的字段必须齐全。规则函数是 `backend/banking/finance.py`、`extensions.py`、`advanced.py` 里的 `errors` / `expected`。

| 业务 | `finance.get` 需要的字段 |
|---|---|
| 全部 | `target`（合同，见 `finance_contract`）；`card` 的 `status`、`online_enabled`、`spending_limit`；`credit` 的 `outstanding`、`version`、`financed`（已分期的消费编号列表）；TRUSTA 另读 `account.get` 拿到 `balance`、`version` |
| 黄金 | `position` 的 `units`（0.01 克）、`cost`、`version`；`risk_profile` 的 `risk_level`、`assessed_at`、`expires_at` |
| 消费分期 | `source`：被分期的那笔消费，至少包含 `status`、`kind`、`amount` |
| 定期存款 | `deposit`：存单对象，包括 `id`、`principal`、`term`、`rate_bps`、`opened`、`matures`、`status`、`version`、`earned`、`cycles`。开立前为 `null` |
| 外币 | `wallet`：`currency`、`units`、`cost`、`version` |
| 信用账户、基金 | `advanced`：`version`、`entries`（消费记录）、`orders`（基金订单）、`holdings`（基金持仓）；基金还需要 `risk_profile` |

**建议做法：** 银行内部数据结构不同的，在适配器里把银行状态整理成以上字段。最省事的方式是参照沙箱银行 `backend/banking/finance_desk.py` 的 `state` / `quote`。

## 各数据结构

### `account_list_item`

accounts.read 不带 _tool 时：该客户的全部账户（列表中的每一项）

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `currency` | 是 | string，三位大写币种代码 |
| `balance` |  | integer |
| `frozen` |  | boolean/integer |
| `name` |  | string |

### `account`

account.get：单个账户；customer_id 必须是授权客户

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `customer_id` | 是 | string，非空 |
| `currency` | 是 | string，三位大写币种代码 |
| `balance` | 是 | integer |
| `frozen` | 是 | boolean/integer |
| `version` | 是 | integer，≥0，增加即表示状态变化；用于签名动作的前置条件比对 |
| `name` |  | string |

### `balance`

account.get_balance

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `balance` | 是 | integer |
| `currency` | 是 | string，三位大写币种代码 |
| `version` | 是 | integer，≥0，增加即表示状态变化；用于签名动作的前置条件比对 |

### `transaction`

transaction.list（按月）的每一项。消费 kind=PURCHASE、退款 REFUND；入账 direction=IN 并可带 payer / memo

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `account_id` | 是 | string，非空 |
| `amount` | 是 | integer，≥1 |
| `currency` | 是 | string，三位大写币种代码 |
| `occurred_at` | 是 | number，≥0 |
| `status` | 是 | 取值：CREATED、PENDING、AUTHORIZED、PROCESSING、SETTLED、FAILED、REVERSED、UNKNOWN |
| `kind` | 是 | string |
| `merchant` |  | string |
| `category` |  | string |
| `direction` |  | 取值：IN、OUT、ZERO |
| `payer` |  | string |
| `memo` |  | string |

### `recurring_charge`

transaction.recurring_history（可选）

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `account_id` | 是 | string，非空 |
| `merchant` | 是 | string |
| `amount` | 是 | integer，≥1 |
| `currency` | 是 | string，三位大写币种代码 |
| `occurred_at` | 是 | number，≥0 |
| `status` | 是 | 取值：CREATED、PENDING、AUTHORIZED、PROCESSING、SETTLED、FAILED、REVERSED、UNKNOWN |

### `transfer`

transaction.get：一笔转账的状态

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `source_account_id` | 是 | string，非空 |
| `recipient_id` | 是 | string，非空 |
| `amount` | 是 | integer，≥1 |
| `currency` | 是 | string，三位大写币种代码 |
| `status` | 是 | 取值：CREATED、PENDING、AUTHORIZED、PROCESSING、SETTLED、FAILED、REVERSED、UNKNOWN |
| `executed_at` |  | number，≥0 |

### `payee`

payee.resolve：按姓名（或手机号、备注名）或 recipient_id 查找，返回匹配的收款人列表；两者都为空时返回全部收款人。is_new 表示银行认为是新添加的收款人（可选）

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `name` | 是 | string，非空 |
| `currency` |  | string，三位大写币种代码 |
| `is_new` |  | boolean/integer |
| `customer_id` |  | string，非空 |

### `card`

card.list / card.get

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `account_id` | 是 | string，非空 |
| `card_type` | 是 | string |
| `status` | 是 | 取值：ACTIVE、FROZEN、LOST、CLOSED |
| `spending_limit` | 是 | integer，≥0 |
| `online_enabled` | 是 | boolean/integer |
| `international_enabled` | 是 | boolean/integer |
| `contactless_enabled` | 是 | boolean/integer |
| `version` | 是 | integer，≥0，增加即表示状态变化；用于签名动作的前置条件比对 |
| `customer_id` |  | string，非空 |

### `subscription`

subscription.list / direct_debit.list 的每一项（status：ACTIVE 生效，其余为已取消 / 已关闭）

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `account_id` | 是 | string，非空 |
| `merchant` | 是 | string |
| `amount` | 是 | integer，≥0 |
| `currency` | 是 | string，三位大写币种代码 |
| `frequency` | 是 | 取值：WEEKLY、MONTHLY、QUARTERLY、YEARLY |
| `next_charge_at` | 是 | number，≥0 |
| `status` | 是 | string |
| `cancelable` | 是 | boolean/integer |
| `version` | 是 | integer，≥0，增加即表示状态变化；用于签名动作的前置条件比对 |

### `product`

wealth.list_products 的每一项

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `name` | 是 | string |
| `risk_level` | 是 | integer，≥1，≤5 |
| `minimum_amount` | 是 | integer，≥0 |
| `term_days` | 是 | integer，≥0 |
| `currency` | 是 | string，三位大写币种代码 |
| `active` | 是 | boolean/integer |
| `risk_note` |  | string |
| `issuer` |  | string |
| `category` |  | string |
| `benchmark` |  | string |
| `liquidity` |  | string |
| `description` |  | string |
| `restrictions` |  | object |

### `portfolio`

portfolio.get

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `account_id` | 是 | string，非空 |
| `product_id` | 是 | string，非空 |
| `amount_cents` | 是 | integer，≥0 |
| `version` | 是 | integer，≥0，增加即表示状态变化；用于签名动作的前置条件比对 |
| `redeemable_at` |  | number，≥0 |
| `customer_id` |  | string，非空 |

### `risk_profile`

customer.get_risk_profile

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `customer_id` | 是 | string，非空 |
| `risk_level` | 是 | integer，≥0，≤5 |
| `assessed_at` | 是 | number，≥0 |
| `expires_at` | 是 | number，≥0 |

### `wish_fund`

wish_fund.list / get

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `account_id` | 是 | string，非空 |
| `name` | 是 | string |
| `balance` | 是 | integer，≥0 |
| `target_amount` | 是 | integer，≥0 |
| `release_at` | 是 | number，≥0 |
| `status` | 是 | string |
| `version` | 是 | integer，≥0，增加即表示状态变化；用于签名动作的前置条件比对 |
| `customer_id` |  | string，非空 |

### `merchant`

merchant.list

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `name` | 是 | string |
| `item` | 是 | string |
| `price` | 是 | integer，≥1 |
| `status` | 是 | string |
| `category` |  | string |

### `life_event`

life_event.list

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `person` | 是 | string |
| `kind` | 是 | 取值：BIRTHDAY、ANNIVERSARY |
| `month` | 是 | integer，≥1，≤12 |
| `day` | 是 | integer，≥1，≤31 |
| `relation` |  | string |
| `customer_id` |  | string，非空 |

### `finance_contract`

finance.quote 的返回、finance.list.contracts 的每一项、finance.get.target。金额为本次需确认的金额；DRAFT 为未执行的报价，到 expires_at 失效

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `customer_id` | 是 | string，非空 |
| `account_id` | 是 | string，非空 |
| `card_id` | 是 | string，非空 |
| `type` | 是 | 取值：loan、installment、gold_buy、gold_sell、deposit_open、deposit_withdraw、deposit_renew、fx_buy、fx_sell、card_charge、card_refund、card_repay、card_surplus、fund_subscribe、fund_confirm、fund_cancel、fund_redeem |
| `status` | 是 | 取值：DRAFT、ACTIVE、SETTLED、COMPLETED、ARCHIVED |
| `version` | 是 | integer，≥0，增加即表示状态变化；用于签名动作的前置条件比对 |
| `amount` | 是 | integer，≥0 |
| `remaining` | 是 | integer，≥0 |
| `paid_fees` | 是 | integer，≥0 |
| `schedule` | 是 | array |
| `created_at` | 是 | number，≥0 |
| `expires_at` | 是 | number，≥0 |
| `card_label` |  | string |
| `units` |  | integer |
| `price` |  | integer，≥0 |
| `quote` |  | object/null |
| `terms` |  | string，银行的产品条款摘要，显示在确认卡上 |
| `principal` |  | integer，≥0 |
| `term` |  | integer |
| `rate_bps` |  | integer |
| `opened` |  | string |
| `matures` |  | string |
| `deposit_id` |  | string，非空 |
| `foreign_currency` |  | string，三位大写币种代码 |
| `rate` |  | string |
| `product_id` |  | string，非空 |
| `order_id` |  | string，非空 |
| `entry_id` |  | string，非空 |
| `source_id` |  | string |

### `finance_list`

finance.list：客户的全部合同、持仓与产品条款

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `contracts` | 是 | array，每项见 `finance_contract` |
| `deposits` | 是 | array |
| `fx_wallets` | 是 | array |
| `gold_positions` | 是 | array |
| `credit_states` | 是 | array |
| `eligible` | 是 | array |
| `outstanding` | 是 | integer，≥0 |
| `credit_limit` | 是 | integer，≥0 |
| `credit_card_limit` | 是 | integer，≥0 |
| `deposit_rates` | 是 | object |
| `currencies` | 是 | array |
| `fund_products` | 是 | array |
| `rules` |  | string |

### `finance_snapshot`

finance.get：合同及其执行所依赖的状态；就是签名动作的前置条件，执行前后都按它比对

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `target` | 是 | 见 `finance_contract` |
| `card` | 是 | 见 `card` |
| `position` |  | object/null |
| `source` |  | object/null |
| `risk_profile` |  | object/null |
| `credit` |  | object |
| `deposit` |  | object/null |
| `wallet` |  | object/null |
| `advanced` |  | object |

### `write_result`

写请求（BankRequest 带 action）的返回：银行流水号与状态。HTTP 成功只算 CREATED，不算 SETTLED

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `id` | 是 | string，非空 |
| `status` | 是 | 取值：CREATED、PENDING、AUTHORIZED、PROCESSING、SETTLED、FAILED、REVERSED、UNKNOWN |
| `executed_at` |  | number，≥0 |

### `verification`

provider.verify(action, transaction_id)：只有银行记录与已签名动作逐项一致且已入账时才是 VERIFIED

| 字段 | 必填 | 类型与说明 |
|---|---|---|
| `status` | 是 | 取值：VERIFIED、MANUAL_REVIEW |
| `reason` | 是 | string |
| `provider_status` | 是 | string |
| `evidence_hash` | 是 | string |
| `executed_at` |  | number/null |

