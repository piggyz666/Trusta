# Conversations and cards

Each TRUSTA reply is a short text plus one or more cards. The text explains; the cards are where you act.

## Card types

| Card | When it appears | What you can do |
|---|---|---|
| Clarify | Something required is missing, such as the amount | Answer, or tap an option |
| Confirmation | Any write operation on money or cards | Check it, edit the amount, tap Confirm or Decline; red operations also need a code |
| Report | Read-only queries: spending, investments, subscriptions | Tap a category for details, filter, compare |
| Risk alert | A scam script, injection or anomaly is detected | See why; switch on 24-hour transfer protection |
| Result | After an operation runs | See the bank's verification and receipt; export a signed evidence bundle |
| Plan | One request with several steps | Follow each step's status, interrupt at any time |
| Questionnaire | Risk assessment | Answer question by question; scoring is deterministic |

## Confirmation cards

A confirmation card is the only way to authorise anything:

- The card is bound to an `action_hash` and covers exactly what it shows: amount, counterparty, account.
- It's valid for **15 minutes**. If it expires or anything changes, a new one is created.
- Typing “confirm” or “ok” in chat executes nothing; it just points you to the card.
- Before executing, the bank re-checks preconditions such as the balance and account version, and refuses if they no longer match.

## Back-and-forth

Add or change details the way you would with a person:

- “Transfer to Zhang San” → asks for the amount → “500” → confirmation card
- “Make it 800” → a new confirmation card
- “Forget it” → the unexecuted card is voided

## Multi-step plans

A request like “I'm going to Japan for 5 days next week, sort it out” becomes a plan: check the balance, redeem an investment, enable overseas payments, switch them off after the trip, warn when over budget.

- Every step that touches money or cards gets its own confirmation card and waits for you.
- Say “stop” to interrupt: unauthorised steps are cancelled; executed steps aren't rolled back and are listed in the reply.

## Undo

These can be reversed with a new operation (which you still confirm): freeze ↔ unfreeze, report lost ↔ cancel lost report, limit changes (back to the old value), card controls (back to the old setting), wish-fund locks (release), investment purchase ↔ redemption.

These can't be undone directly:

- A completed transfer can't be pulled back by the bank alone; ask the payee to return it.
- A paid merchant order needs a refund from the merchant.
- A cancelled subscription has to be renewed with the merchant.

## Human hand-off

Saying “转人工” (talk to a person) hands the conversation to a human: your messages stop going to the model and join a human queue; “取消转人工” returns you to the assistant. Human agents have no authorisation endpoint and can't confirm money operations for you.

## Automation rules

Set “when … then …” rules in a sentence, for example “remind me when takeaway goes over ¥800 a month” or “when my salary lands, put ¥1,000 in my wish fund”.

- Triggers: a time, an incoming payment, balance below a value, a category over budget, a large purchase.
- Actions: remind, transfer, save to wish fund, buy an investment, freeze a card, switch off a card control, hand off to a human.
- A rule only takes effect after you confirm it; when it fires, money operations still wait for your confirmation one by one.

## Bills and Wealth pages

Two pages in the side navigation show things at a glance:

- **Bills & subscriptions**: fixed monthly spend, a 30-day timeline of upcoming charges, and registered subscriptions and direct debits. TRUSTA also looks for repeating charges in your history: the same merchant and amount, 25–35 days apart, three times. Those appear as "possible subscriptions" and you are told once in the chat. You can confirm one (you will then be reminded 3 days before each charge), mark it as unknown (you get stop-loss options such as freezing the card or reaching a person), or dismiss it.
- **Wealth**: reference quotes for gold and USD, EUR, JPY and HKD against CNY (every number shows its source and time; refresh any time; when offline the last value is shown and labelled), your risk level, your holdings, and the bank's product shelf with term, minimum, liquidity, benchmark and risk notes. Products above your risk level are shown but cannot be bought.

Every cancel, buy or redeem button on these pages goes back into the chat as a confirmation card, under the same authorisation as asking in words. TRUSTA does not recommend individual stocks and never promises returns.

## Developer mode

Turn on Developer mode under 设置 → 外观 (Settings → Appearance) and each reply gets “查看决策过程” (View decision process): every signed message between agents in that turn, re-verified on the page (message signatures, evidence signatures, causal parents). Result cards let you download a signed evidence bundle; see [Verifying evidence offline](#/evidence).
