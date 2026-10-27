# Integrations

At its core TRUSTA is one trusted execution chain: the AI only proposes, agents hand off with signatures, and you authorise money movements. That chain can be reached from several channels, use different models and banks, and anchor its evidence on chain. This page lists only integrations that really work, with how far each was verified:

- **Tested live**: run against a real service or network.
- **Local tests**: implemented and passing local tests, not yet connected to the official environment.
- **Built in**: part of TRUSTA itself, no outside service needed.

## Channels

Every channel shares the same conversation service and the same authorisation rules.

| Integration | Notes | Status |
|---|---|---|
| Web | Desktop browser: interactive cards, overview, settings | Tested live |
| Phone | Pair once by QR on your network; add to home screen; see [Phone access](#/mobile) | Tested live |
| Feishu bot | Persistent connection, no public URL; yellow actions confirmed in-card, red ones finished in the web app; see [Feishu](#/feishu) | Tested live |
| Terminal | `python -m scripts.chat_cli`: chat, confirm and inspect the decision chain | Tested live |
| HTTP API | REST + SSE streaming for your own interface; see [HTTP API](#/api) | Tested live |

## Models

Models only understand and propose; whichever you use, the authorisation rules and signature chain stay the same.

| Integration | Notes | Status |
|---|---|---|
| DeepSeek | Default conversation model `deepseek-flash` | Tested live |
| OpenAI-compatible models | Enter an endpoint and model name in the setup wizard | Built in |
| Kimi | `kimi-k2.6`, really called in the shared 40-scenario evaluation | Tested live |
| MiMo | `mimo-v2.6-flash`, really called in the shared evaluation | Tested live |
| Gemini | `gemini-3.1-flash-lite`, completed a transfer end to end on the local bank in evaluation | Tested live |
| GLM | `glm-4-flash`, really called in the shared evaluation | Tested live |
| Rule engine | Works with no model; understands standard phrasings | Built in |

> The shared evaluation uses the same frozen prompt, business contract and 40 scenarios (20 normal, 20 attacks). Results differ a lot between models; the raw results are kept in `docs/LLM_Final_Live_Acceptance.md` and `docs/LLM_Integration_Planning_Final_Acceptance.md` in the repository.

## Banks

TRUSTA enables features based on what is actually connected and passes its checks.

| Integration | Notes | Status |
|---|---|---|
| Local virtual bank | All six scenarios incl. incoming-payment push; all data fictional | Built in |
| Revolut sandbox | Official sandbox: real accounts and transactions read back, plus one individually authorised 0.01 EUR payment | Tested live |
| Starling | Adapter and request signing implemented, passing local protocol tests; not yet called against the official sandbox | Local tests |

To connect an external bank sandbox, write a `config/bank.*.local.toml` based on `config/bank.*.example.toml` and choose an external bank at the bank step of the [setup wizard](#/setup).

### Bank capabilities and features

Pages and chat read only through the standard bank interface: whatever capabilities the bank offers, the matching features light up. When a capability is missing, the page still opens and that section says the bank does not provide it.

| Bank capability | Features it enables |
|---|---|
| `accounts.read` (required) | Accounts, overview, new conversations |
| `balance.read` | Balance queries, cash reserve in the allocation plan, balance-triggered automations |
| `transactions.read` | Spending queries, bill analysis, recurring-charge detection (from the last 6 months of transactions) |
| `payees.*` + `payments.create/status` | Transfers, bill splitting, new payees (confirmed one by one, then verified) |
| `cards.*` | Card management, card controls, trip assistant |
| `subscriptions.*` / `direct_debits.*` | Bills & subscriptions page, cancelling in chat |
| `investments.*` + `customers.risk.*` | Product shelf, holdings, personalised plan, buy/redeem, risk assessment |
| `savings.goals.*` | Wish funds |
| `finance.read` + `finance.quote` + each business's execute capability | Term deposits, FX, gold and gold savings, fund units, loans and instalments, credit statement (each lit up separately) |

Everyday-service links, market quotes, rule Q&A, the anti-fraud cooling-off period and strong authentication need no bank interface. Deposits, FX, gold, funds, loans and the credit account run as "bank quote → confirmation → signed execution", with the books kept at the bank; a business the bank does not offer has its tab hidden and requests for it are clearly declined.

## Trust infrastructure

| Integration | Notes | Status |
|---|---|---|
| ECDSA P-256 | A key per agent, signing every message; see [Signed agent messaging](#/agent-communication) | Built in |
| FISCO BCOS + WeID | Consortium-chain identity and the `CollaborationAnchor` contract; see [Cross-institution collaboration](#/cross-institution) | Tested live (single-host 8-node test chain) |
| Groth16 | Zero-knowledge admission proofs, really generated and verified | Tested live (development trusted setup) |
| Ethereum Sepolia | Audit roots written to the public testnet; public acceptance passed | Tested live (testnet, not mainnet) |
| MetaMask | Transactions signed in the wallet; the key never leaves it | Tested live |

## Network and automation

| Integration | Notes | Status |
|---|---|---|
| LAN pairing | One-time pairing codes, a token per device, revocable | Tested live |
| Cloudflare Tunnel | Public access if you already run a tunnel | Built in |
| Automation rules | “When … then …”: 5 triggers (a time, an incoming payment, balance below a value, a category over budget, a large purchase) × 7 actions (remind, transfer, save to wish fund, buy an investment, freeze a card, switch off a card control, hand off to a human); money actions still need each confirmation | Built in |
| Scheduled plans | Birthday orders, recurring transfers and more, started by a background worker when due and confirmed by you | Built in |

## Adding a channel or a bank

- New channel: follow `backend/channels/feishu.py` — pass messages to the unified chat endpoint `/api/chat` and render confirmation cards as that channel's interactive cards. A channel can never bypass the confirmation card.
- New bank: no change to TRUSTA's code. Write an adapter (a capability manifest plus `request` and `verify`) and configure `provider = "module"`, `factory = "your_module:create"`. Field contracts, sample code and checks are in `docs/real-bank-integration.md` in the repository; `tests/test_real_bank_readiness.py` can be reused as a self-test.
- Conformance check: `python -m scripts.bank_check --config your-config` checks an adapter item by item (manifest, every read against the data contract, features lit up, pages and chat, optional verified sandbox writes) and prints a report; the adapter template is in `examples/bank_adapter/`, the data contract in `docs/bank-data-contract.md`.
