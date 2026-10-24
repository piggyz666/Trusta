# What TRUSTA is

TRUSTA is a banking AI assistant that runs on your own computer. You say what you want in one sentence; it understands and gives you a card you can act on. Every step that moves money waits for you to authorise it on that card.

## One sentence, one card

| Scenario | You can say |
|---|---|
| Transfers | “Send ¥500 to Zhang San”, “Pay my landlord ¥3,000 on the 1st of every month” |
| Spending | “How much did I spend this month?”, “How much went on takeaway last month?” |
| Investments | “Recommend something that suits me”, “Buy ¥1,000 of something low-risk”, “Run my risk assessment” |
| Cards | “I lost my card”, “Turn off overseas payments on my credit card” |
| Subscriptions and direct debits | “What am I subscribed to?”, “What's renewing soon?” |
| Across scenarios | “Mum's birthday is next month — save ¥2,000 for a present”, “I'm going to Japan for 5 days next week, sort it out” |

Replies are **interactive cards**: edit the amount on a transfer card, tap a slice of the spending chart for details, subscribe to a product right from the card, flip card controls in place.

> The TRUSTA app and assistant currently work in Chinese; the examples above are translations.

## Design principles

- **The AI proposes; it never authorises.** The model only outputs candidate actions, and every parameter must be traceable to your own words, or it asks again. Typing “confirm” in chat executes nothing — authorisation happens only on a confirmation card.
- **Risk tiers come from bank rules.** Queries run directly, money and card operations need your confirmation, and large amounts, new payees or reporting a card lost also need a one-time code. Neither the model nor the app can change a tier. See [Authorisation tiers](#/authorization).
- **Every step is traceable.** Every message between the six agents is signed and every hand-off leaves evidence you can verify offline later. See [Signed agent messaging](#/agent-communication).
- **Least visibility.** The model never sees balances, statements, card numbers or codes; numbers in replies are filled in from bank queries.

## How it fits together

```
your sentence
  → input safety check (injection, coerced transfers, data fishing)
  → Supervisor agent: intent and parameters (each parameter must quote your words)
  → Planner agent: breaks it into steps, checked by a validator
  → business agents (Transaction / Finance / Wealth / Service): query or propose
  → policy engine sets the tier → confirmation card → you authorise (plus a code if needed)
  → sealed execution contract → bank gateway executes → independent verification → receipt
  → audit hash chain + signed Merkle checkpoints
```

- **Backend**: Python 3.11+, SQLite, `cryptography` (ECDSA P-256).
- **Frontend**: a React web app, the same pages on desktop and phone; a Feishu bot as an optional channel.
- **Bank**: a local virtual bank sandbox by default, with fictional data; external sandboxes such as Revolut or Starling can be configured.
- **Model**: DeepSeek is recommended; any OpenAI-compatible model works; without a model, a rule engine handles standard phrasings.

## Next

- First time: [Quick start](#/quickstart)
- The security design: [Authorisation tiers](#/authorization), [Signed agent messaging](#/agent-communication)
