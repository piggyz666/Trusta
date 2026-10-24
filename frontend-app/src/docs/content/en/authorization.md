# Authorisation tiers

TRUSTA puts every operation into one of three tiers. The backend policy engine decides the tier from bank rules; neither the model nor the app can set or change it.

| Tier | How it's authorised | Typical operations |
|---|---|---|
| Green | Runs directly | Balances, spending, subscriptions, comparing investments, the risk questionnaire |
| Yellow | You tap Confirm on the card | Transfers, freezing a card, cancelling a subscription or direct debit, locking wish-fund money, updating your risk level |
| Red | Confirm + SMS or authenticator code | Large transfers, new payees, reporting a card lost and cancelling it, unfreezing, PIN reset, raising a limit, enabling a card control, buying or redeeming investments |

## When a transfer turns red

Any one of these requires a code:

- A single amount of **¥5,000** or more (`TRUSTA_HIGH_RISK_CENTS`).
- Today's CNY transfers would exceed **¥1,000** in total (`TRUSTA_DAILY_TRANSFER_CENTS`).
- **3** transfers already in the last **10 minutes**.
- **¥500** or more to a payee added in the last 24 hours.
- Any foreign-currency transfer.

Cards and other operations: unfreezing, reporting lost, cancelling a lost report, resetting a PIN, adding a payee, raising a spending limit, enabling a card control, and any amount of ¥5,000 or more are red. Under the `competition` safety profile, buying and redeeming investments are red too.

## Codes

- Use an authenticator code (TOTP) or an SMS code. In demo mode SMS is simulated and the code appears on the page's “simulated phone”.
- SMS codes are valid for **5 minutes**, allow **3** attempts and only work for that one operation.
- **5** failed verifications in a row lock the account for safety (`TRUSTA_AUTH_FAILURE_LIMIT`).
- Enter codes only on the confirmation card, never in chat.

## Always refused

Whatever the tier, these are refused outright: unknown payee, frozen source account, insufficient balance, account state changed since the card was created (preconditions differ), or transfer protection active.

## 24-hour transfer protection

If something feels wrong, say “开启24小时转账保护” (turn on 24-hour transfer protection). While it's on, transfers, new payees, wish-fund orders and investment purchases are all blocked, and only bank staff can lift it after checking with you (it needs the security operator token).

## In Feishu

Yellow operations can be confirmed on the Feishu card. Red operations come with a link back to the TRUSTA web app to finish verification. See [Feishu](#/feishu).
