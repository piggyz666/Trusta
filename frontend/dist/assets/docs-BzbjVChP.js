var De=Object.defineProperty;var Me=(n,e,t)=>e in n?De(n,e,{enumerable:!0,configurable:!0,writable:!0,value:t}):n[e]=t;var b=(n,e,t)=>Me(n,typeof e!="symbol"?e+"":e,t);import{r as T,j as d,c as ze}from"./client-C9yhCDaK.js";import{u as Fe,G as ue,B as $e}from"./links-D8020E6M.js";const je=`# Signed agent messaging

The worst case for a multi-agent system is an agent that gets prompt-injected or compromised, impersonates another, alters messages or proposes things beyond its role — and afterwards nobody can say what happened. TRUSTA makes every message between agents **verifiable, traceable and non-repudiable**.

![Figure 1: trusted multi-agent communication](/figs/fig1.webp)

## The six agents

| Agent | Responsible for |
|---|---|
| Supervisor | Understanding intent, extracting parameters, summarising results |
| Planner | Turning a request into steps (a task graph) checked by a validator |
| Transaction | Queries and proposals about transfers, payees and balances |
| Finance | Statements, spending analysis, anomaly detection |
| Wealth | Product filtering, comparison, purchase and redemption proposals |
| Service | Card management, subscriptions, knowledge questions |

A separate audit checkpoint worker produces signed checkpoints for the audit chain.

**Agents can only propose.** Money moves only through a deterministic executor and a sealed execution contract; a compromised agent can't touch funds directly.

## How each message is signed

Each agent has its own **ECDSA P-256** identity with an encrypted private key. Agents exchange V3 signed message envelopes, and the receiver checks every field:

| Mechanism | Stops |
|---|---|
| Separate key identity per agent; registered as an on-chain WeID for cross-institution work | Impersonating another agent |
| Canonical payload hash, task revision, 5-minute validity, one-time nonce, bound recipient | Tampering, replay, stale messages, misdelivery |
| Capability snapshot: a commitment to the sender's current permissions, rejected if it differs from the registry | Privilege escalation, quietly widened permissions |
| Communication edges: each task type only allows the planned (sender, receiver, message type) combinations | e.g. the Finance agent suddenly sending a “transfer proposal” |
| Hand-off evidence chain: the runtime signs every hand-off again and points to the evidence before it | Denial afterwards, reordered steps |
| Intent provenance: a hash of your words, how they were understood and the model call record go into the Supervisor's signed request | Not being able to show which sentence a proposal came from |
| Key rotation, revocation, identity suspension | Handling a leaked key |

## Evidence chain and audit

1. For every hand-off the runtime issues **hand-off evidence**: the full message envelope, input and output hashes, the hashes of the evidence it depends on, and hashes of each stage of the related money operation (proposal, authorisation, contract, verification, receipt).
2. Evidence and audit events go into an **append-only** audit hash chain: change any record and every later hash breaks.
3. A separate audit worker regularly seals new records with a **signed Merkle checkpoint**; any single record can be verified offline with its inclusion proof.

## See it yourself

- Turn on **Developer mode** under 设置 → 外观 (Settings → Appearance). Each reply gets “查看决策过程” (View decision process), listing every signed message between agents and re-verifying it on the spot (message signatures, evidence signatures, causal parents).
- Download a **signed evidence bundle** from a result card and verify it on the command line; see [Verifying evidence offline](#/evidence).
- Re-run the attack benchmark:

\`\`\`bash
python -m benchmarks.security_benchmark --banking-version 5
\`\`\`

See [Security benchmarks](#/benchmarks).

## Managing keys

\`\`\`bash
python -m scripts.manage_crypto_identity --agent transaction --rotate          # rotate an agent's key
python -m scripts.manage_crypto_identity --agent transaction --status SUSPENDED # suspend an identity
python -m scripts.manage_crypto_identity --revoke-key KEY_ID                    # revoke a key
\`\`\`

After rotation the old key has a grace period (300 seconds by default, \`--grace-seconds\`); after that it's rejected.
`,Be='# HTTP API\n\nThe web app, the terminal client and Feishu all work through the same local HTTP API, which you can use for your own interface too.\n\n## Conventions\n\n- Address: `http://127.0.0.1:8000`.\n- Everything except `/health` and static pages needs `Authorization: Bearer <token>`. The token is printed at start-up and saved in `data/api.token` by default; paired phones use their own device tokens.\n- The server checks `Host` and `Origin`; the customer identity is decided server-side and clients **cannot** send a `user_id`.\n- POST bodies are `Content-Type: application/json`.\n- Amounts are always **integer minor units** of the account currency (fen for CNY); there is no implicit currency conversion.\n\n## Chat\n\n| Method | Path | Purpose |\n|---|---|---|\n| POST | `/api/chat/sessions` | Create a session |\n| GET | `/api/chat/sessions/{sid}` | Read a session: messages, current plan, pending cards (for reconnecting) |\n| POST | `/api/chat/sessions/{sid}/messages` | Send a message |\n| POST | `/api/chat/sessions/{sid}/actions/{action_id}/decision` | Decide on a confirmation card |\n| POST | `/api/chat/sessions/{sid}/actions/{action_id}/sms` | Send a simulated SMS code for a red card |\n| POST | `/api/chat/sessions/{sid}/interrupt` | Interrupt the current plan |\n| POST | `/api/chat/sessions/{sid}/close` | Close the session |\n\n### Sending a message\n\n```json\nPOST /api/chat/sessions/{sid}/messages\n{\n  "message_id": "m_01J…",\n  "revision": 3,\n  "text": "给我妈转两千，备注生活费"\n}\n```\n\n- `message_id` is generated by the client (1–64 characters); retrying with the same ID returns the original result without reprocessing.\n- `revision` is the version from the previous response; a mismatch returns `CHAT_STALE`.\n- `Accept: text/event-stream` streams Server-Sent Events; `Accept: application/json` returns once the whole turn is done.\n\nSSE events include `turn.started`, `guard.result`, `intent.recognized`, `plan.created` / `plan.updated`, `tool.started` / `tool.finished`, `message.delta`, `card`, `turn.completed` and `turn.failed`. Treat the full result in `turn.completed` as final.\n\n### Deciding on a card\n\n```json\nPOST /api/chat/sessions/{sid}/actions/{action_id}/decision\n{\n  "action_hash": "sha256:…",\n  "approve": true,\n  "verification": { "method": "SMS", "code": "123456" }\n}\n```\n\n`action_hash` must match the card exactly; `verification` is only needed for red operations (`TOTP` or `SMS`).\n\n## Tasks (lower level)\n\nUnderneath the chat layer is the task API, where every money action lands with its hash binding, expiry and anti-replay rules intact:\n\n| Method | Path | Purpose |\n|---|---|---|\n| POST | `/api/tasks` | Create a task, e.g. `{"text":"给张三转 2000 元"}` |\n| GET | `/api/tasks` | Summary of the customer\'s tasks |\n| GET | `/api/tasks/{id}` | Status, task graph, authorisation and evidence |\n| POST | `/api/tasks/{id}/authorize` | Authorise: `{"action_hash":"…","approve":true}` |\n| GET | `/api/balance` | Account balance |\n| GET | `/api/transactions?account_id=…&month=YYYY-MM` | Transactions for an account and month |\n| GET | `/api/audit` | Whether the audit chain is intact: `{"valid": true}` |\n| GET | `/api/audit/proof` | Merkle inclusion proof for an audit record |\n\nAn HTTP success doesn\'t mean the money moved; always check the task status:\n\n| Status | Meaning |\n|---|---|\n| `WAITING_USER` | Waiting for confirmation or missing details |\n| `COMPLETED` | Settled, and the bank-side state was verified |\n| `BLOCKED` | Stopped by a rule or safety gate |\n| `CANCELLED` | Declined by the user |\n| `FAILED` | Failed before execution or in the bank transaction |\n| `MANUAL_REVIEW` | May have executed and needs a human check — **do not repeat the transfer** |\n\n## Errors\n\n| Status | Meaning |\n|---|---|\n| 400 | Input or safety validation failed |\n| 403 | Identity, origin or ownership refused |\n| 404 | Unknown endpoint |\n| 500 | Unclassified error; refresh the original task\'s status instead of starting a new money operation |\n\nThere is no endpoint for clients to write to the bank directly, no execution endpoint for agents, and no automatic retry of money writes.\n\nThe full protocol is in `docs/chat-protocol-v1.md` and `docs/api.md` in the repository.\n',He=`# Authorisation tiers

TRUSTA puts every operation into one of three tiers. The backend policy engine decides the tier from bank rules; neither the model nor the app can set or change it.

| Tier | How it's authorised | Typical operations |
|---|---|---|
| Green | Runs directly | Balances, spending, subscriptions, comparing investments, the risk questionnaire |
| Yellow | You tap Confirm on the card | Transfers, freezing a card, cancelling a subscription or direct debit, locking wish-fund money, updating your risk level |
| Red | Confirm + SMS or authenticator code | Large transfers, new payees, reporting a card lost and cancelling it, unfreezing, PIN reset, raising a limit, enabling a card control, buying or redeeming investments |

## When a transfer turns red

Any one of these requires a code:

- A single amount of **¥5,000** or more (\`TRUSTA_HIGH_RISK_CENTS\`).
- Today's CNY transfers would exceed **¥1,000** in total (\`TRUSTA_DAILY_TRANSFER_CENTS\`).
- **3** transfers already in the last **10 minutes**.
- **¥500** or more to a payee added in the last 24 hours.
- Any foreign-currency transfer.

Cards and other operations: unfreezing, reporting lost, cancelling a lost report, resetting a PIN, adding a payee, raising a spending limit, enabling a card control, and any amount of ¥5,000 or more are red. Under the \`competition\` safety profile, buying and redeeming investments are red too.

## Codes

- Use an authenticator code (TOTP) or an SMS code. In demo mode SMS is simulated and the code appears on the page's “simulated phone”.
- SMS codes are valid for **5 minutes**, allow **3** attempts and only work for that one operation.
- **5** failed verifications in a row lock the account for safety (\`TRUSTA_AUTH_FAILURE_LIMIT\`).
- Enter codes only on the confirmation card, never in chat.

## Always refused

Whatever the tier, these are refused outright: unknown payee, frozen source account, insufficient balance, account state changed since the card was created (preconditions differ), or transfer protection active.

## 24-hour transfer protection

If something feels wrong, say “开启24小时转账保护” (turn on 24-hour transfer protection). While it's on, transfers, new payees, wish-fund orders and investment purchases are all blocked, and only bank staff can lift it after checking with you (it needs the security operator token).

## In Feishu

Yellow operations can be confirmed on the Feishu card. Red operations come with a link back to the TRUSTA web app to finish verification. See [Feishu](#/feishu).
`,We=`# Security benchmarks

TRUSTA ships a repeatable attack benchmark that checks whether multi-agent messaging and the execution chain stop forgery and tampering.

\`\`\`bash
python -m benchmarks.security_benchmark --banking-version 5
\`\`\`

Results are written as JSON (\`docs/phase4-security-benchmark.json\` by default; change it with \`--output\`). \`docs/phase5-security-benchmark.json\` in the repository is the latest formal run.

## Results

**18 attack classes, 5 runs each — 90 attempts, all blocked, 0 unintended transactions; 0 of 135 legitimate messages wrongly rejected.**

| Attack | What it tries |
|---|---|
| Forged agent identity | Sending as an identity not in the registry |
| Invalid signature | A signature that doesn't match the message |
| Payload tampering | Changing the message after signing |
| Replay | Resending a message already used |
| Expired message | A message past its validity |
| Revoked key | Signing with a revoked key |
| Old key outside grace | Using the old key after rotation |
| Capability escalation | Claiming a capability the agent doesn't have |
| Capability snapshot tampering | Altering the permission commitment in a message |
| Unauthorised tool call | Calling a tool outside the plan |
| Authorisation hash mismatch | An authorisation that doesn't match the operation |
| Duplicate execution | Executing the same money operation twice |
| Audit event tampering | Altering an audit record |
| Merkle leaf tampering | Altering a proven record |
| Invalid Merkle proof | A forged inclusion proof |
| Checkpoint tampering | Altering a signed checkpoint |
| Anchor root mismatch | A checkpoint that doesn't match the anchored root |
| Signature algorithm downgrade | Coaxing the system into a weaker algorithm |

## Performance

| Operation | Median | P95 |
|---|---|---|
| Sign a message | 15.1 ms | 22.5 ms |
| Verify a message | 5.5 ms | 9.0 ms |
| Produce a checkpoint | 16.7 ms | 31.2 ms |

Environment: Windows 11, Python 3.13, \`cryptography\` 47. Verification covers the signature, identity, capabilities and task context; a checkpoint covers whole-chain validation, Merkle batching, signing and persistence.

## Six-scenario closed-loop audit

\`\`\`bash
python -m benchmarks.scenario_audit
\`\`\`

Runs **49** end-to-end checks across transfers, spending, investments, cards, subscriptions and cross-scenario plans.

## Limits

- These are local, controlled attacks — not a production penetration test, and not an estimate of the probability of being secure.
- They use a synthetic sandbox bank and audit records; timings depend on the host's filesystem and SQLite.
- Passing every test doesn't prove there are no unknown vulnerabilities.
`,qe=`# Changelog

## 2026-10-25

- Landing page: ink-fluid hero, product demo, trust layers, measured results and quick start; Chinese and English.
- Documentation site.
- One-line installers (Windows / macOS / Linux), \`start.sh\`, double-click \`TRUSTA.cmd\`.
- Windows portable package and automated build.
- Fix: \`requirements.txt\` now lists everything the server needs at start-up, so clean installs no longer fail.

## 2026-10-24

- Web and phone app: chat home, interactive cards, overview and records, feature switches, settings.
- Interactive water background, liquid-glass interface, new icon; light and dark follow the system.
- The decision trace moved behind developer mode.
- Feishu links for red operations open on phones too; conversations are named by what they did.
- README: figures for trusted multi-agent communication and cross-institution collaboration.

## 2026-10-23

- Access modes: this computer only / local network / tunnel; one-time phone pairing with device tokens.
- Feishu persistent-connection worker and binding codes.
- Setup wizard: validates pasted secrets; probe results show details.
- Chat text can never stand in for a confirmation card; Feishu rule-confirmation buttons.

## 2026-10-22

- Capability registry and setup wizard; automation rules engine; trip manager.
- Untrusted-text quarantine; anti-fraud cooling-off; money operations run one at a time.
- Six-scenario closed-loop audit.

## 2026-10-21

- Unified chat API and card protocol.
- Signed multi-agent messaging and the terminal client.
`,Ge=`# Conversations and cards

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

- The card is bound to an \`action_hash\` and covers exactly what it shows: amount, counterparty, account.
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

## Developer mode

Turn on Developer mode under 设置 → 外观 (Settings → Appearance) and each reply gets “查看决策过程” (View decision process): every signed message between agents in that turn, re-verified on the page (message signatures, evidence signatures, causal parents). Result cards let you download a signed evidence bundle; see [Verifying evidence offline](#/evidence).
`,Ke="# Command line\n\nRun every command from the project folder. If you used an installer, activate the virtual environment first (`source .venv/bin/activate`, or `.venv\\Scripts\\activate` on Windows), or replace `python` with `.venv/bin/python`.\n\n## Everyday\n\n| Command | Does |\n|---|---|\n| `./start.sh` / `.\\start.ps1` / double-click `TRUSTA.cmd` | Start the server and background workers |\n| `python -m scripts.setup` | Setup wizard |\n| `python -m scripts.setup --status` | Show the feature table |\n| `python -m scripts.setup --probe` | Check model, bank and Feishu connectivity, then show the feature table |\n| `python -m scripts.pair` | Phone pairing code |\n| `python -m scripts.pair --feishu` | Feishu binding code |\n| `python -m scripts.pair --devices` | List paired devices |\n| `python -m scripts.pair --revoke ID` | Revoke a device |\n\n## Chat in a terminal\n\n```bash\npython -m scripts.chat_cli             # connect to the running server\npython -m scripts.chat_cli --sandbox   # in-process server + throw-away database; data/ untouched\n```\n\nCommands inside the chat:\n\n| Command | Does |\n|---|---|\n| `/y N` / `/n N` | Confirm / decline card N |\n| `/sms N` | Send a simulated SMS code for red card N |\n| `/otp N CODE` | Confirm red card N with an SMS code |\n| `/totp N CODE` | Confirm red card N with an authenticator code |\n| `/stop` | Interrupt the current plan |\n| `/trace` | Show the decision chain of the last turn |\n| `/events` | List scheduled plans |\n| `/advance ID` | Run a scheduled plan now (demo mode only) |\n| `/quit` | Exit |\n\n## Verification and audit\n\n| Command | Does |\n|---|---|\n| `python -m scripts.verify_handoff_evidence BUNDLE --trusted-registry REGISTRY` | Verify a signed evidence bundle offline |\n| `python -m scripts.verify_audit_record PROOF --trusted-public-key KEY` | Verify one audit record's Merkle inclusion proof |\n| `python -m scripts.manage_crypto_identity --agent NAME --rotate` | Rotate an agent's key |\n| `python -m scripts.manage_crypto_identity --agent NAME --status SUSPENDED` | Suspend an agent identity |\n| `python -m scripts.manage_crypto_identity --revoke-key KEY_ID` | Revoke a key |\n\n## Tests and evaluation\n\n| Command | Does |\n|---|---|\n| `python -m pytest -q` | All automated tests (about 1,400) |\n| `python -m benchmarks.scenario_audit` | 49 closed-loop checks across six scenarios |\n| `python -m benchmarks.security_benchmark --banking-version 5` | The 18-class attack benchmark |\n\n## Background workers\n\nThe start script runs these for you:\n\n| Command | Does |\n|---|---|\n| `python -m scripts.audit_worker --watch` | Produces signed Merkle checkpoints |\n| `python -m scripts.scheduled_worker --watch` | Runs due plans (birthday orders, recurring transfers…) |\n| `python -m scripts.feishu_worker` | Feishu persistent connection |\n\n## Packaging\n\n```bash\npython -m scripts.build_portable --python-embed python-3.12.10-embed-amd64.zip --out dist\n```\n\nBuilds the Windows portable package `dist/TRUSTA-windows-x64.zip`. The repository's `Packages` GitHub Actions workflow downloads the embeddable Python, builds the package, starts it on Windows as a smoke test, and attaches it to the release when a `v*` tag is pushed.\n",Ye="# Configuration\n\nTRUSTA is configured with environment variables kept in `.env` in the project folder (written by the [setup wizard](#/setup) and ignored by git). You can also set them in the environment before starting; environment values win. Money amounts are in **cents (fen)**.\n\n## Model\n\n| Variable | Default | Meaning |\n|---|---|---|\n| `DEEPSEEK_API_KEY` | — | Model API key; empty means the rule engine is used |\n| `TRUSTA_CHAT_LLM_ENDPOINT` | `https://api.deepseek.com/chat/completions` | OpenAI-compatible endpoint |\n| `TRUSTA_CHAT_LLM_MODEL` | `deepseek-flash` | Model name |\n| `TRUSTA_CHAT_LLM_PROXY` | — | HTTPS proxy |\n| `TRUSTA_CHAT_LLM_TIMEOUT` | `30` | Per-call timeout (seconds) |\n| `TRUSTA_CHAT_LLM_CONCURRENCY` | `4` | Maximum concurrent calls |\n| `TRUSTA_CHAT_LLM_DAILY_CALLS` | `300` | Daily calls per user; once used up, the rule engine takes over for the day |\n| `TRUSTA_LLM_CONFIG` | — | Model configuration file used by evaluations |\n\n## Bank\n\n| Variable | Default | Meaning |\n|---|---|---|\n| `TRUSTA_BANK_CONFIG` | — | External bank sandbox config (`config/bank.*.local.toml`); empty means the local sandbox bank |\n| `TRUSTA_BANKING_VERSION` | `5` | Bank action set version |\n| `TRUSTA_DISABLED_FEATURES` | — | Comma-separated features to switch off, e.g. `wealth,trip` |\n\n## Access and channels\n\n| Variable | Default | Meaning |\n|---|---|---|\n| `TRUSTA_ACCESS_MODE` | `local` | `local` this computer only / `lan` local network / `tunnel` |\n| `TRUSTA_PUBLIC_HOST` | — | Public hostname in tunnel mode (no `https://`) |\n| `TRUSTA_LAN_HOST` | auto-detected | This computer's address in local-network mode |\n| `TRUSTA_PORT` | `8000` | Port used in Feishu “back to the app” links (the server itself always listens on 8000) |\n| `TRUSTA_API_TOKEN` | generated | Local access token; saved in `data/api.token` by default |\n| `TRUSTA_APP_URL` | derived from access mode | Address used by “back to the app” links in Feishu |\n| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | — | Feishu app credentials |\n| `FEISHU_MODE` | `longconn` | `longconn` persistent connection / `webhook` |\n| `FEISHU_VERIFICATION_TOKEN` / `FEISHU_ENCRYPT_KEY` | — | For webhook mode |\n| `FEISHU_USER_BINDINGS` | — | Operator-preset bindings, `open_id:owner,open_id:owner` |\n\n## Safety\n\n| Variable | Default | Meaning |\n|---|---|---|\n| `TRUSTA_SAFETY_PROFILE` | `competition` | `competition` strong verification / `legacy` |\n| `TRUSTA_DEMO_MODE` | `false` (`true` in the wizard) | Demo mode: simulated SMS codes shown, plans can be fast-forwarded |\n| `TRUSTA_HIGH_RISK_CENTS` | `500000` | A single amount at or above this is red (¥5,000) |\n| `TRUSTA_DAILY_TRANSFER_CENTS` | `100000` | Daily total above this is red (¥1,000) |\n| `TRUSTA_ANOMALY_LARGE_CENTS` | `100000` | Threshold for flagging large purchases |\n| `TRUSTA_ANOMALY_DUPLICATE_SECONDS` | `300` | Window for flagging possible duplicate charges |\n| `TRUSTA_AUTH_FAILURE_LIMIT` | `5` | Failed verifications in a row before a safety lock |\n| `TRUSTA_EXECUTION_FAILURE_LIMIT` | `3` | Failed executions in a row before a safety lock |\n| `TRUSTA_SECURITY_OPERATOR_TOKEN` | — | Bank staff token: lifting transfer protection, human hand-off |\n| `TRUSTA_STRONG_AUTH_SECRET` | — | Strong-auth passphrase for red operations under the `legacy` profile |\n\n## Signed messaging and audit\n\n| Variable | Default | Meaning |\n|---|---|---|\n| `TRUSTA_COMMUNICATION_VERSION` | `3` | Signed messaging protocol version; at 3 the audit and scheduled-plan workers start |\n| `TRUSTA_CRYPTO_KEY_DIR` | `data/trusta.keys` | Agent private key folder (a `.keys` folder named after the database) |\n| `TRUSTA_AGENT_TIMEOUT_SECONDS` | `20` | Per-agent timeout |\n| `TRUSTA_FINANCE_PROCESS_ENABLED` | `false` | Run the Finance agent as a separate process |\n| `TRUSTA_BLOCKCHAIN_ANCHOR_ENABLED` | `false` | Anchor audit checkpoints on chain |\n| `TRUSTA_DID_ENABLED` | `true` | On-chain identities for cross-institution work |\n| `TRUSTA_ZKP_ENABLED` | `false` | Zero-knowledge capability admission |\n| `TRUSTA_ZKP_ARTIFACT_DIR` / `TRUSTA_ZKP_MANIFEST_SHA256` | — | ZK artefact folder and manifest hash |\n\n## Data\n\n| Variable | Default | Meaning |\n|---|---|---|\n| `TRUSTA_DB` | `data/trusta.sqlite3` | Database path |\n\n`data/` holds the database, the root signing key (`signing.key`), agent keys and logs; `.env` holds your secrets. Never commit or share either.\n",Ze=`# Cross-institution collaboration: WeID · ZK admission · on-chain commitments

When agents from several institutions need to work on something together — say, banks' agents cross-checking the same risk conclusion — local signatures aren't enough. How does the other side know who you are, whether you're qualified to take part, and whether the final conclusion was altered? On top of [signed agent messaging](#/agent-communication), TRUSTA adds a trust layer that can be checked publicly on a consortium chain.

![Figure 2: cross-institution trust layer and attack results](/figs/fig2.webp)

## The parts

### On-chain identity: WeID

Each collaborating agent registers a **WeID** (WeBank's open-source decentralised identity) on a **FISCO BCOS** consortium chain, and its P-256 message key is bound to that WeID. Identities can be registered, resolved, deactivated and restored. Collaboration rounds accept only on-chain WeIDs; if the chain is unavailable they refuse outright rather than falling back to a locally self-signed \`did:key\`.

### M1: zero-knowledge capability admission

An agent proves with **Groth16** that its score on a fixed evaluation set (an F1 computed from TP / FP / FN by a fixed formula) meets the policy threshold, **without revealing the evaluation details or its predictions**. The proof is bound to the policy, the epoch, a model commitment and the evaluation record; it can be revoked and it expires. Agents can't simply declare a score to get in.

### M2: binding collaboration messages

Beyond the V3 signature, collaboration messages are bound to the on-chain identity, the admission credential, the round and the epoch. Bad signatures, cross-round / cross-task / cross-epoch messages, revoked identities, wrong models and replays are all rejected.

### OTC: full-window collection

Collaboration is k-of-n: evidence is frozen only at the deadline, and the round is voided if fewer than k valid conclusions arrived. Late messages are kept for telemetry only.

### M3: on-chain decision commitment

The **Merkle root** of the conclusion and the member snapshot are written to the \`CollaborationAnchor\` contract and read back over two nodes' RPC endpoints. If a submission's outcome is unknown, it is recovered by looking up the original hash — never blindly resubmitted.

## Important limits

- On-chain identities and signatures grant **no bank write permission**; money operations still need the user's authorisation on a confirmation card.
- The current environment is a **single-host, 8-node test consortium chain** (chain ID 101); the two verification RPCs belong to two nodes on the same host and don't represent independent institutions.
- Groth16 uses a **development trusted setup** (single-host Phase-2), not a production MPC ceremony.
- The zero-knowledge circuit proves a restricted statistical relation; it doesn't run model inference inside the circuit.

All of this is recorded in \`docs/ANCHOR_Integration_Final_Acceptance.md\` in the repository.

## Registered agents

| Agent | WeID |
|---|---|
| Finance | \`did:weid:101:0xa968103852c7eae1a245b77e98660730d8ccc3d3\` |
| Wealth | \`did:weid:101:0xa8fac68a926fb16debb085af40a1bb5d840df7de\` |
| Transaction | \`did:weid:101:0xf9fb420c820cf567b5250f816dd9a21c80f88c31\` |

## Turning it on

This layer is off by default. Start the test consortium chain in WSL or Linux first:

\`\`\`bash
bash scripts/anchor_network_lifecycle.sh run
\`\`\`

Related settings: \`TRUSTA_DID_ENABLED\`, \`TRUSTA_ZKP_ENABLED\`, \`TRUSTA_ZKP_ARTIFACT_DIR\`, \`TRUSTA_ZKP_MANIFEST_SHA256\`; see [Configuration](#/configuration). Third parties can re-verify a public evidence package with \`python -m scripts.verify_anchor_collaboration\`.
`,Ve=`# Verifying evidence offline

Every TRUSTA result card can export a **signed evidence bundle**. Whoever holds it can check — without trusting the server, and without contacting the bank or the model — that the hand-offs between agents for that request really happened, completely and in order.

## Export

1. Turn on Developer mode under 设置 → 外观 (Settings → Appearance).
2. On a result card, open “查看决策过程” (View decision process) and click **下载证据包** (Download evidence bundle) to get \`trusta-evidence-xxxxxxxx.json\`.

## Verify

\`\`\`bash
python -m scripts.verify_handoff_evidence trusta-evidence-xxxxxxxx.json --trusted-registry registry.json
\`\`\`

On success it prints:

\`\`\`json
{"verified": true, "scope": "RUNTIME_CAUSAL_ATTESTATION_NOT_BANK_AUTHORIZATION"}
\`\`\`

and exits with 0. On failure \`verified\` is \`false\` and the exit code is 1. Changing any byte of the bundle makes verification fail.

The repository includes a sample you can try right away:

\`\`\`bash
python -m scripts.verify_handoff_evidence docs/agent-communication-demo.json --trusted-registry docs/agent-communication-trusted-registry.json
\`\`\`

## What it checks

- The signature on every piece of hand-off evidence, and the signer's identity and key in the registry.
- The causal parent links between pieces of evidence: unknown or missing parents, duplicates, reordering and cross-task mixing are rejected.
- That identities, capability commitments and key ownership weren't swapped.
- The Merkle inclusion proofs of the audit records.

## What it doesn't check

- As \`scope\` says, this is a **runtime causal attestation**, not a bank authorisation. Authorisation for money operations comes from your decision on the confirmation card.
- Hidden payloads aren't exported; matching input and output hashes don't prove the content was semantically right.

## Where the registry comes from

\`--trusted-registry\` is the registry of agent public keys and capabilities. Verifiers should **obtain and pin it independently** (for example, fetch it once from a trusted channel and keep it), never read it from the bundle itself. A historical snapshot only describes that moment; it doesn't prove a key hasn't been revoked since.
`,Qe=`# FAQ

## Will it touch my real bank account?

Not by default. TRUSTA connects to a local virtual bank sandbox, and all demo data is fictional. Connecting a real bank needs an adapter configured under \`config/\` and authorisation on the bank's side.

## Does it work without a language model?

Yes. Without a model it uses the rule engine, which understands standard phrasings such as “给张三转500元” (send ¥500 to Zhang San) or “这个月花了多少钱” (how much did I spend this month). With a model it handles more casual wording and multi-step requests.

## Can the model see my balance?

No. The model sees your words, dates and resource names (such as payee or product names) — never balances, statements, card numbers or codes, and long digit strings are masked before sending. Numbers in replies are filled in from bank queries and checked against that turn's results.

## I typed “confirm” in chat. Why didn't anything happen?

By design. Authorisation only happens on a confirmation card and is bound to exactly what the card shows. That way, even a manipulated model or someone else at your chat window can't move money with a sentence.

## The confirmation card expired.

Cards are valid for 15 minutes. Ask again to get a fresh one.

## Where do I find the code?

In demo mode SMS is simulated: tap “发送验证码” (send code) on the confirmation card and the code appears on the page's simulated phone. An authenticator code works too.

## The web page says I don't have access.

Open the full link printed in the start-up window (the one with \`#token=…\`). Phones need to be [paired](#/mobile) first.

## My phone can't open it.

Check three things: access is set to local network and TRUSTA was restarted; the phone and computer are on the same Wi-Fi or hotspot; the phone has been paired with a code. Some public Wi-Fi networks isolate devices — try a phone hotspot.

## The Feishu bot doesn't reply.

Check \`data/feishu-worker.log\` for a successful connection; make sure \`requirements-channels.txt\` is installed; check that the persistent connection is selected and a version is published on the Feishu Open Platform; make sure you bound your account with \`python -m scripts.pair --feishu\`.

## Windows says running scripts is disabled on this system.

Start TRUSTA by double-clicking \`TRUSTA.cmd\`, which runs the start script with \`-ExecutionPolicy Bypass\`. Or in PowerShell:

\`\`\`powershell
powershell -ExecutionPolicy Bypass -File .\\start.ps1
\`\`\`

## Installing dependencies is slow or fails.

From mainland China, a pip mirror helps, for example:

\`\`\`bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
\`\`\`

then run the install command again.

## Where is my data, and how do I uninstall?

Everything lives in the TRUSTA folder: \`data/\` holds the database, keys and logs; \`.env\` holds your configuration. Delete the folder to uninstall completely.

## Do I need the blockchain and zero-knowledge parts?

No. That layer is for agents from several institutions working together and is off by default. On a single machine, signed messaging, the evidence chain and the audit trail are already fully active. See [Cross-institution collaboration](#/cross-institution).
`,Xe=`# Feishu

Connect TRUSTA as a Feishu (Lark) bot and you can chat with it in Feishu. The default is a **long connection**: TRUSTA connects out to Feishu, so no public address is needed.

## 1. Create an app on the Feishu Open Platform

1. Create a custom app and enable the **Bot** capability.
2. Under Events & Callbacks, choose **receive events through a persistent connection**.
3. Add the \`im.message.receive_v1\` event and card callbacks.
4. Publish a version.
5. Find the **App ID** (starts with \`cli_\`) and **App Secret** under Credentials & Basic Info.

## 2. Add it to TRUSTA

\`\`\`bash
pip install -r requirements-channels.txt   # the long connection needs lark-oapi
python -m scripts.setup
\`\`\`

Answer \`y\` to “connect a Feishu bot”, fill in the App ID and App Secret, and pick \`longconn\`. Restart TRUSTA; the window shows that the Feishu long connection has started, and logs go to \`data/feishu-worker.log\`.

## 3. Bind your account

A Feishu user must be bound before acting as you in TRUSTA:

\`\`\`bash
python -m scripts.pair --feishu
\`\`\`

It prints a message like “绑定 123456”. Send that message to the bot in a private chat. The code is valid for 5 minutes and works once.

## What you can do in Feishu

- All queries and conversation.
- **Yellow** operations: confirm on the Feishu card.
- **Red** operations: Feishu can't take codes, so the bot sends a link that opens the same conversation in the TRUSTA web app to finish verification. The link uses the address from your [access setting](#/setup) (local network or tunnel), so it opens on your phone too.

## Webhook mode

If you already have a public address, choose \`webhook\`: Feishu pushes events to \`POST /api/channels/feishu/webhook\`. You'll also need the Verification Token and, optionally, the Encrypt Key.
`,Je=`# Installation

There are four ways to install TRUSTA; they end up the same, so pick whichever suits you. Every option installs into a single folder and leaves your system untouched.

| Option | Best for | Needs |
|---|---|---|
| Portable package | Just using it, without installing Python (Windows) | Nothing |
| One-liner | Most people | Python 3.11+ |
| pip | People used to Python tools (coming soon) | Python 3.11+ |
| From source | Changing code, running tests, wiring in your own bank or model | Python 3.11+, git |

## Portable package (Windows)

1. Download \`TRUSTA-windows-x64.zip\` from the [releases page](https://github.com/piggyz666/Trusta/releases).
2. Unzip it anywhere, for example \`D:\\TRUSTA\`.
3. Double-click \`TRUSTA.cmd\`. The first run opens the [setup wizard](#/setup); after that it starts straight away.

The zip carries its own Python runtime (under \`runtime\\\`) and every dependency, so nothing else needs installing. Data lives in the \`data\\\` folder next to it; deleting the folder uninstalls TRUSTA completely.

macOS and Linux packages are coming soon; use the one-liner for now.

## One-liner

**Windows (PowerShell)**

\`\`\`powershell
irm https://raw.githubusercontent.com/piggyz666/Trusta/main/install.ps1 | iex
\`\`\`

**macOS / Linux**

\`\`\`bash
curl -fsSL https://raw.githubusercontent.com/piggyz666/Trusta/main/install.sh | bash
\`\`\`

The installer:

1. Checks for Python 3.11+. On Windows, if it's missing, it offers to install Python 3.12 for the current user with \`winget\`; on macOS / Linux it explains how to install it and stops.
2. Downloads the code to \`~/TRUSTA\` (\`%USERPROFILE%\\TRUSTA\` on Windows), with \`git clone\` when git is available and a source archive otherwise.
3. Creates a private virtual environment \`.venv\` in that folder and installs dependencies.
4. Opens the setup wizard.

Environment variables you can set first:

| Variable | Effect | Default |
|---|---|---|
| \`TRUSTA_HOME\` | Install folder | \`~/TRUSTA\` |
| \`TRUSTA_BRANCH\` | Which branch to download | \`main\` |
| \`TRUSTA_NO_SETUP\` | \`1\` skips the setup wizard | — |

Running the same command again updates the code (\`git pull\` for git installs) and keeps your configuration and data.

## pip (coming soon)

Once published to PyPI:

\`\`\`bash
pip install trusta
trusta setup && trusta start
\`\`\`

## From source

\`\`\`bash
git clone https://github.com/piggyz666/Trusta.git && cd Trusta
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt -r requirements-channels.txt
python -m scripts.setup
./start.sh                           # Windows: .\\start.ps1
\`\`\`

- \`requirements.txt\` is what TRUSTA needs to run.
- \`requirements-channels.txt\` is optional: Feishu long connection and terminal QR codes.
- \`requirements-llm.txt\`, \`requirements-phase6.txt\` and \`requirements-evaluation.txt\` are for evaluation and external audit integrations; everyday use doesn't need them.

After changing the web app, rebuild it (needs Node.js):

\`\`\`bash
cd frontend-app
npm install
npm run build
\`\`\`

The build lands in \`frontend/dist\` and is served by the backend.

## Starting and stopping

| System | Start |
|---|---|
| Windows | Double-click \`TRUSTA.cmd\`, or \`.\\start.ps1\` in PowerShell |
| macOS / Linux | \`./start.sh\` |

The start script runs the API server, the audit checkpoint worker and the scheduled-plan worker (birthday orders, recurring transfers and so on), plus the Feishu worker when a long connection is configured. Worker logs go to \`data/*.log\`. Closing the window or pressing \`Ctrl+C\` stops everything.

The start script picks Python in this order: the \`TRUSTA_PYTHON\` environment variable → the portable package's \`runtime/python\` → the installer's \`.venv\` → \`python\` on your PATH.

## Uninstalling

TRUSTA only writes to its own folder. Delete the folder; back up \`data/\` (database and keys) and \`.env\` (your configuration) first if you need them.
`,en=`# Phone access

The web app and the phone app are the same pages, adapting to the screen. By default TRUSTA only opens on the computer it runs on; to use a phone, switch access to local network or tunnel and pair once.

## 1. Turn on local-network access

\`\`\`bash
python -m scripts.setup
\`\`\`

Choose **local network** at the access step, then restart TRUSTA.

## 2. Pair your phone

Either way works:

- **In the web app**: open 设置 → 访问与设备 (Settings → Access & devices), generate a pairing code and scan it with your phone.
- **In a terminal**:

\`\`\`bash
python -m scripts.pair
\`\`\`

It shows the pairing address, a QR code (when the \`qrcode\` package is installed) and a 6-digit code. Put the phone on the same Wi-Fi or hotspot, then scan or open the address in the phone's browser. The code is valid for 5 minutes and works once.

Once paired, the phone keeps its own device token and opens directly from then on. Use the browser's “Add to Home Screen” to run it full-screen like an app.

## Managing devices

\`\`\`bash
python -m scripts.pair --devices        # list paired devices
python -m scripts.pair --revoke DEVICE  # revoke one
\`\`\`

You can also view and revoke devices under 设置 → 访问与设备 (Settings → Access & devices).

## Away from your network

If you already have a public address such as a Cloudflare Tunnel, choose **tunnel** in the wizard and enter the hostname (without \`https://\`). Without a tunnel hostname it falls back to local network.

> On local network and tunnel, every device must pair before it can use TRUSTA — but “this computer only” remains the safest setting.
`,nn=`# What TRUSTA is

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

\`\`\`
your sentence
  → input safety check (injection, coerced transfers, data fishing)
  → Supervisor agent: intent and parameters (each parameter must quote your words)
  → Planner agent: breaks it into steps, checked by a validator
  → business agents (Transaction / Finance / Wealth / Service): query or propose
  → policy engine sets the tier → confirmation card → you authorise (plus a code if needed)
  → sealed execution contract → bank gateway executes → independent verification → receipt
  → audit hash chain + signed Merkle checkpoints
\`\`\`

- **Backend**: Python 3.11+, SQLite, \`cryptography\` (ECDSA P-256).
- **Frontend**: a React web app, the same pages on desktop and phone; a Feishu bot as an optional channel.
- **Bank**: a local virtual bank sandbox by default, with fictional data; external sandboxes such as Revolut or Starling can be configured.
- **Model**: DeepSeek is recommended; any OpenAI-compatible model works; without a model, a rule engine handles standard phrasings.

## Next

- First time: [Quick start](#/quickstart)
- The security design: [Authorisation tiers](#/authorization), [Signed agent messaging](#/agent-communication)
`,tn=`# Quick start

Get TRUSTA running on your own computer and make your first (virtual) transfer in about five minutes.

> TRUSTA connects to a local virtual bank sandbox by default. All demo data is fictional; no real account is touched.

## 1. Install

Pick one option; see [Installation](#/install) for details.

**Windows**: download the portable zip, unzip it and double-click \`TRUSTA.cmd\`. Or run in PowerShell:

\`\`\`powershell
irm https://raw.githubusercontent.com/piggyz666/Trusta/main/install.ps1 | iex
\`\`\`

**macOS / Linux**:

\`\`\`bash
curl -fsSL https://raw.githubusercontent.com/piggyz666/Trusta/main/install.sh | bash
\`\`\`

## 2. Configure

The setup wizard opens right after installation. It has four steps, and the first time you can just press Enter through them:

1. **Model**: choose DeepSeek and paste an API key, or choose “none” to use the rule engine.
2. **Bank**: the local sandbox bank (default).
3. **Access**: this computer only (default, safest).
4. **Safety and demo**: the \`competition\` safety profile plus demo mode (simulated SMS codes are shown on the page).

Run \`python -m scripts.setup\` any time to change it. See [First-run setup](#/setup).

## 3. Start

\`\`\`bash
./start.sh          # macOS / Linux
\`\`\`

\`\`\`powershell
.\\start.ps1         # Windows, or double-click TRUSTA.cmd
\`\`\`

The window prints a \`http://127.0.0.1:8000/#token=…\` link; open it in your browser. The token is this computer's access credential — don't share it.

## 4. Try a few sentences

- “我这个月花了多少钱” (How much did I spend this month?): read-only, returns a report card straight away.
- “给张三转500元” (Send ¥500 to Zhang San): creates a yellow confirmation card; nothing happens until you tap Confirm.
- “我的卡丢了” (I lost my card): a red operation that needs a code; in demo mode the code appears on the “simulated phone”.

## 5. See how it works

Turn on **Developer mode** under 设置 → 外观 (Settings → Appearance). Each reply gets a “查看决策过程” (View decision process) link listing every signed message between agents, re-verified on the spot.

## Next

- Use it on your phone: [Phone access](#/mobile)
- Connect Feishu: [Feishu](#/feishu)
- Chat in a terminal: \`python -m scripts.chat_cli\`
`,sn=`# First-run setup

The setup wizard writes your settings to \`.env\` in the project folder. The file stays on your computer and is ignored by git; the wizard tightens its permissions and doesn't echo secrets as you type.

\`\`\`bash
python -m scripts.setup            # interactive setup
python -m scripts.setup --status   # show the feature table only
python -m scripts.setup --probe    # check connectivity, then show the feature table (makes one model call)
\`\`\`

## 1/4 Model

| Option | Notes |
|---|---|
| DeepSeek (recommended) | Paste an API key (starts with \`sk-\`); the model name defaults to \`deepseek-flash\` |
| Any OpenAI-compatible model | Endpoint (\`https://…/chat/completions\`), model name and key |
| None | The rule engine, which understands standard phrasings such as “给张三转500元” |

You can add an HTTPS proxy if needed. Whichever model you connect, it can only propose actions and never sees balances, statements, card numbers or codes.

## 2/4 Bank

| Option | Notes |
|---|---|
| Local sandbox bank (default) | All six scenarios work, including incoming-payment notifications; all data is fictional |
| External bank sandbox | Revolut, Starling and similar; write a \`config/bank.*.local.toml\` based on \`config/bank.*.example.toml\` |

## 3/4 Access and channels

| Option | Notes |
|---|---|
| This computer only (default) | Opens only in this computer's browser; the safest choice |
| Local network | Phones on the same Wi-Fi or hotspot pair once by QR code; see [Phone access](#/mobile) |
| Tunnel | When you already have a public address such as a Cloudflare Tunnel; without a tunnel hostname it falls back to local network |

It then asks whether to connect a Feishu bot; see [Feishu](#/feishu).

## 4/4 Safety and demo

| Setting | Notes |
|---|---|
| Safety profile \`competition\` (default) | Strong verification: daily totals, transfer frequency, new payees and investment trades are all enforced |
| Safety profile \`legacy\` | Earlier rules, kept for comparison |
| Demo mode \`true\` (default) | Simulated SMS codes appear on the page's “simulated phone”; scheduled plans can be fast-forwarded |
| Demo mode \`false\` | Codes are not shown |

## The feature table

The wizard ends with a feature table. TRUSTA enables features based on what is actually connected and passes its checks:

\`\`\`
Model: deepseek-flash (VERIFIED)  Bank: virtual/local  Safety: competition

[Six scenarios]
  ✔ available  Transfers (name / phone / nickname / scheduled / recurring / memo)
  ✔ available  Spending analysis / queries / anomaly detection
  ...
[Channels]
  ✘ unavailable  Feishu — missing: Feishu bot
        To enable: run python -m scripts.setup and fill in FEISHU_APP_ID / FEISHU_APP_SECRET
\`\`\`

(The real output is in Chinese.) Features that aren't enabled are invisible to the assistant and can't be called; the app's Features page also tells you what each one needs.

All settings are listed under [Configuration](#/configuration).
`,an=`# 多 Agent 签名通信

多 Agent 系统最怕的是：某个 Agent 被提示注入或被攻破后，冒充别人、篡改消息、越权提案，而事后谁也说不清发生了什么。TRUSTA 让 Agent 之间的每一次通信都**可验证、可追溯、不可抵赖**。

![图 1 多 Agent 可信通信](/figs/fig1.webp)

## 六个 Agent

| Agent | 负责 |
|---|---|
| 总控（Supervisor） | 理解意图、抽取参数、汇总结果 |
| 规划（Planner） | 把请求拆成步骤（任务图），交给校验器检查 |
| 交易（Transaction） | 转账、收款人、余额相关的查询与提案 |
| 财务（Finance） | 账单、消费分析、异常识别 |
| 理财（Wealth） | 产品筛选、对比、申赎提案 |
| 客服（Service） | 卡片管理、订阅、知识问答 |

另有一个独立的审计检查点 worker，负责给审计链生成签名检查点。

**Agent 只能提案。** 真正执行资金动作的是确定性执行器和封印的执行合约；被攻破的 Agent 无法直接动钱。

## 每条消息怎么签名

每个 Agent 有独立的 **ECDSA P-256** 密钥身份，私钥加密存储。Agent 之间发送的是 V3 签名消息信封，接收方逐项检查：

| 机制 | 防住什么 |
|---|---|
| 独立密钥身份；跨机构协作时登记为链上 WeID | 冒充其他 Agent |
| 规范化负载哈希、任务版本、5 分钟有效期、一次性 nonce、绑定接收方 | 篡改、重放、过期消息、错发 |
| 能力快照：消息里带发送方当前权限的承诺，与注册表不一致即拒收 | Agent 越权、权限被悄悄放大 |
| 通信边约束：每类任务只允许计划内的（发送方、接收方、消息类型）组合 | 例如财务 Agent 突然发出一条“转账提案” |
| 交接证据因果链：运行时对每次交接再签名，并引用前序证据 | 事后否认、顺序被调换 |
| 意图来源证明：用户原话哈希、理解方式、模型调用记录哈希写进总控签名的请求 | 无法证明某个提案来自用户的哪句话 |
| 密钥轮换、吊销、身份暂停 | 密钥泄露后的处置 |

## 证据链和审计

1. 每次交接，运行时签发一条**交接证据**，包含完整的消息信封、输入输出哈希、引用的前序证据哈希，以及对应金融操作各阶段（提案、授权、合约、核验、回执）的哈希。
2. 证据和审计事件一起写入**只追加**的审计哈希链：任何一条被改动，后面的哈希都对不上。
3. 独立的审计 worker 定期对新记录生成**签名 Merkle 检查点**；任意一条记录都可以用包含证明（inclusion proof）离线验证。

## 亲眼验证

- 网页“设置 → 外观”打开**开发者模式**，每条回复下出现“查看决策过程”：逐条列出 Agent 间的签名消息，并当场重新验签（消息签名、证据签名、因果父节点）。
- 在结果卡上下载**签名证据包**，用命令行离线验证，见[证据包离线验证](#/evidence)。
- 重跑攻击实测：

\`\`\`bash
python -m benchmarks.security_benchmark --banking-version 5
\`\`\`

见[安全基准](#/benchmarks)。

## 管理密钥

\`\`\`bash
python -m scripts.manage_crypto_identity --agent transaction --rotate          # 轮换某个 Agent 的密钥
python -m scripts.manage_crypto_identity --agent transaction --status SUSPENDED # 暂停身份
python -m scripts.manage_crypto_identity --revoke-key KEY_ID                    # 吊销某把密钥
\`\`\`

轮换后旧密钥有一个宽限期（默认 300 秒，\`--grace-seconds\`），之后一律拒收。
`,on='# HTTP API\n\nTRUSTA 的网页、终端客户端和飞书都通过同一套本地 HTTP API 工作。你也可以用它接入自己的界面。\n\n## 基本约定\n\n- 地址：`http://127.0.0.1:8000`。\n- 除 `/health` 和静态页面外，所有接口都需要 `Authorization: Bearer <token>`。token 在启动时打印，默认保存在 `data/api.token`；配对过的手机使用自己的设备令牌。\n- 服务端会校验 `Host` 和 `Origin`；客户身份由服务端决定，客户端**不能**传 `user_id`。\n- POST 使用 `Content-Type: application/json`。\n- 金额一律是账户币种的**整数最小单位**（人民币为分），不做隐式换汇。\n\n## 对话\n\n| 方法 | 路径 | 用途 |\n|---|---|---|\n| POST | `/api/chat/sessions` | 新建会话 |\n| GET | `/api/chat/sessions/{sid}` | 读取会话：历史消息、当前计划、待确认卡片（断线重连用） |\n| POST | `/api/chat/sessions/{sid}/messages` | 发送一条消息 |\n| POST | `/api/chat/sessions/{sid}/actions/{action_id}/decision` | 对确认卡做决定 |\n| POST | `/api/chat/sessions/{sid}/actions/{action_id}/sms` | 为红色卡片发送模拟短信验证码 |\n| POST | `/api/chat/sessions/{sid}/interrupt` | 中断当前计划 |\n| POST | `/api/chat/sessions/{sid}/close` | 关闭会话 |\n\n### 发送消息\n\n```json\nPOST /api/chat/sessions/{sid}/messages\n{\n  "message_id": "m_01J…",\n  "revision": 3,\n  "text": "给我妈转两千，备注生活费"\n}\n```\n\n- `message_id` 由客户端生成（1–64 位）；同一个 ID 重试返回原结果，不会重复处理。\n- `revision` 是上一次响应里的版本号；不一致会返回 `CHAT_STALE`。\n- 请求头 `Accept: text/event-stream` 返回 SSE 流；`Accept: application/json` 等整轮处理完一次性返回。\n\nSSE 事件包括 `turn.started`、`guard.result`、`intent.recognized`、`plan.created` / `plan.updated`、`tool.started` / `tool.finished`、`message.delta`、`card`、`turn.completed`、`turn.failed`。以 `turn.completed` 里的完整结果为准。\n\n### 对确认卡做决定\n\n```json\nPOST /api/chat/sessions/{sid}/actions/{action_id}/decision\n{\n  "action_hash": "sha256:…",\n  "approve": true,\n  "verification": { "method": "SMS", "code": "123456" }\n}\n```\n\n`action_hash` 必须和卡片上的完全一致；`verification` 只有红色操作需要（`TOTP` 或 `SMS`）。\n\n## 任务（底层）\n\n对话层下面是任务接口，每个资金动作都落到这里，保持哈希绑定、过期和防重放规则：\n\n| 方法 | 路径 | 用途 |\n|---|---|---|\n| POST | `/api/tasks` | 创建任务，例如 `{"text":"给张三转 2000 元"}` |\n| GET | `/api/tasks` | 当前客户的任务摘要 |\n| GET | `/api/tasks/{id}` | 任务状态、任务图、授权及证据 |\n| POST | `/api/tasks/{id}/authorize` | 授权，`{"action_hash":"…","approve":true}` |\n| GET | `/api/balance` | 账户余额 |\n| GET | `/api/transactions?account_id=…&month=YYYY-MM` | 指定账户和月份的交易记录 |\n| GET | `/api/audit` | 审计链是否完整：`{"valid": true}` |\n| GET | `/api/audit/proof` | 审计记录的 Merkle 包含证明 |\n\nHTTP 成功并不代表资金执行成功，必须检查任务状态：\n\n| 状态 | 含义 |\n|---|---|\n| `WAITING_USER` | 等待确认或补全信息 |\n| `COMPLETED` | 已结算，并且银行侧状态核验通过 |\n| `BLOCKED` | 被规则或安全关卡阻止 |\n| `CANCELLED` | 用户拒绝 |\n| `FAILED` | 执行前或银行事务失败 |\n| `MANUAL_REVIEW` | 可能已执行，需要人工核查，**禁止重复转账** |\n\n## 错误\n\n| 状态码 | 含义 |\n|---|---|\n| 400 | 输入或安全校验失败 |\n| 403 | 身份、来源或归属被拒绝 |\n| 404 | 未知接口 |\n| 500 | 未分类异常；应刷新原任务状态，不要据此再次发起资金操作 |\n\n没有让客户端直接写银行的接口，没有给 Agent 使用的执行接口，也没有自动的资金写入重试。\n\n完整协议见仓库中的 `docs/chat-protocol-v1.md` 和 `docs/api.md`。\n',rn=`# 授权等级

TRUSTA 把每个操作分成三级。等级由后端策略引擎根据银行规则决定，大模型和前端都无法指定或调整。

| 等级 | 怎么授权 | 典型操作 |
|---|---|---|
| 绿色 | 直接执行 | 查余额、看账单、查订阅、比较理财产品、风险测评问卷 |
| 黄色 | 你在确认卡上点“确认” | 转账、冻结卡、取消订阅或代扣、锁定心愿金、更新风险等级 |
| 红色 | 确认 + 短信或验证器动态码 | 大额转账、新收款人、挂失与解除挂失、解冻、改密码、提高限额、开启交易开关、理财申购与赎回 |

## 什么情况会变成红色

转账在以下任一情况下需要验证码：

- 单笔金额 ≥ **¥5,000**（\`TRUSTA_HIGH_RISK_CENTS\`）。
- 人民币转账当日累计超过 **¥1,000**（\`TRUSTA_DAILY_TRANSFER_CENTS\`）。
- **10 分钟**内已有 **3** 笔转账，下一笔需要验证码。
- 24 小时内新添加的收款人，单笔 ≥ **¥500**。
- 外币转账一律需要验证码。

卡片和其他操作：解冻、挂失、解除挂失、重置密码、添加收款人、提高消费限额、开启某项交易开关，以及金额 ≥ ¥5,000 的操作，都是红色。\`competition\` 安全档位下，理财申购和赎回也是红色。

## 验证码

- 可以用认证器动态码（TOTP）或短信验证码。演示模式下短信是模拟的，验证码显示在页面的“模拟手机”里。
- 短信验证码 **5 分钟**内有效，最多尝试 **3** 次，只对这一笔操作有效。
- 连续验证失败 **5** 次会安全锁定账户（\`TRUSTA_AUTH_FAILURE_LIMIT\`）。
- 验证码只在确认卡里输入，不要在聊天里发送。

## 一定会被拦下的情况

不论等级，以下情况直接拒绝执行：收款人不存在、付款账户已冻结、余额不足、卡片生成后账户状态已变化（前置条件不一致）、账户处于转账保护期。

## 24 小时转账保护

遇到可疑情况，可以说“开启24小时转账保护”。保护期内转账、添加收款人、心愿金下单、理财申购都会被拦下，只能由银行人员核实后解除（需要安全操作员令牌）。

## 在飞书里

黄色操作可以直接在飞书卡片上确认；红色操作会给出一个链接，引导你回到 TRUSTA 网页完成验证。见[飞书接入](#/feishu)。
`,ln=`# 安全基准

TRUSTA 用一组可以重复运行的攻击实测，检查多 Agent 通信和执行链能不能拦住各种伪造和篡改。

\`\`\`bash
python -m benchmarks.security_benchmark --banking-version 5
\`\`\`

结果写入 JSON 文件（默认 \`docs/phase4-security-benchmark.json\`，可用 \`--output\` 指定）。仓库里的 \`docs/phase5-security-benchmark.json\` 是最近一次正式运行的结果。

## 结果

**18 类攻击各 5 次，共 90 次，全部拦截，0 笔意外交易；135 条正常消息 0 误拒。**

| 攻击 | 说明 |
|---|---|
| 伪造 Agent 身份 | 用不在注册表里的身份发消息 |
| 无效签名 | 签名与消息不符 |
| 负载篡改 | 签名后改动消息内容 |
| 重放 | 重复发送已用过的消息 |
| 过期消息 | 超过有效期的消息 |
| 已吊销密钥 | 用吊销的密钥签名 |
| 宽限期外旧密钥 | 轮换后仍用旧密钥 |
| 越权 | 声称没有的能力 |
| 能力快照篡改 | 改动消息里的权限承诺 |
| 未授权工具调用 | 调用计划之外的工具 |
| 授权哈希不符 | 授权与操作不对应 |
| 重复执行 | 同一笔资金操作执行两次 |
| 审计事件篡改 | 改动审计记录 |
| Merkle 叶子篡改 | 改动被证明的记录 |
| 无效 Merkle 证明 | 伪造包含证明 |
| 检查点篡改 | 改动签名检查点 |
| 锚定根不符 | 检查点与锚定根不一致 |
| 签名算法降级 | 诱导使用更弱的算法 |

## 性能

| 操作 | 中位数 | P95 |
|---|---|---|
| 签名一条消息 | 15.1 ms | 22.5 ms |
| 验证一条消息 | 5.5 ms | 9.0 ms |
| 生成一个检查点 | 16.7 ms | 31.2 ms |

测试环境：Windows 11，Python 3.13，\`cryptography\` 47。验签包含签名、身份、能力和任务上下文检查；检查点包含整链校验、Merkle 批处理、签名和持久化。

## 六大场景闭环审计

\`\`\`bash
python -m benchmarks.scenario_audit
\`\`\`

对转账、账单、理财、卡片、订阅、跨场景联动六大场景做 **49 项**端到端检查。

## 局限

- 这是本地受控攻击，不是生产环境渗透测试，也不是“安全概率”估计。
- 使用合成的沙箱银行和审计记录；耗时受主机文件系统和 SQLite 影响。
- 测试全部通过不代表不存在未知漏洞。
`,cn=`# 更新日志

## 2026-10-25

- 宣传页：墨水流体首屏、产品演示、可信机制、实测数据和快速开始；中英双语。
- 文档站上线。
- 一行命令安装（Windows / macOS / Linux）、\`start.sh\`、\`TRUSTA.cmd\` 双击启动。
- Windows 便携版打包和自动化构建。
- 修复：\`requirements.txt\` 补全服务启动所需的依赖，全新环境安装不再失败。

## 2026-10-24

- 网页和手机端：对话首页、交互卡片、总览与记录、功能开关、设置。
- 交互式水波背景、液态玻璃界面、新图标；深浅色跟随系统。
- 决策过程移到开发者模式。
- 飞书红色操作的链接在手机上也能打开；会话按内容自动命名。
- README：多 Agent 可信通信与跨机构协作配图。

## 2026-10-23

- 访问方式：仅本机 / 局域网 / 隧道；手机一次性配对和设备令牌。
- 飞书长连接 worker 和绑定码。
- 配置向导：校验密钥输入；探测结果显示详情。
- 聊天里的文字不能代替确认卡；飞书规则确认按钮。

## 2026-10-22

- 功能注册表和配置向导；自动化规则引擎；出行管家。
- 外部文本隔离；反诈冷静期；资金操作串行执行。
- 六大场景闭环审计。

## 2026-10-21

- 统一对话接口与卡片协议。
- 多 Agent 签名通信与终端客户端。
`,hn=`# 对话与结果卡片

TRUSTA 的每次回复由一段文字和若干张卡片组成。文字负责说明，卡片负责操作。

## 卡片类型

| 卡片 | 什么时候出现 | 你可以做什么 |
|---|---|---|
| 追问 | 缺少必要信息，例如没说金额 | 直接回答，或点选项 |
| 确认卡 | 任何涉及资金或卡片的写操作 | 核对、修改金额，点“确认”或“拒绝”；红色操作还要输入验证码 |
| 报告卡 | 账单、理财、订阅等只读查询 | 点分类看明细、筛选、对比 |
| 风险提示 | 识别到诈骗话术、注入或异常 | 了解原因；可以开启 24 小时转账保护 |
| 结果卡 | 操作执行后 | 查看银行核验结果和回执；可以导出签名证据包 |
| 计划 | 一句话里有多个步骤 | 查看每一步的状态，随时中断 |
| 问卷 | 风险测评 | 逐题作答，结果由确定性规则评分 |

## 确认卡

确认卡是唯一的授权入口：

- 卡片绑定一个 \`action_hash\`，只对卡上显示的那一笔（金额、对象、账户）有效。
- **15 分钟**内有效。过期或者信息有变化，需要重新生成。
- 在聊天里说“确认”“好的”不会执行，只会提示你去点卡片。
- 执行前，银行还会再次核对余额、账户版本等前置条件，不一致会拒绝执行。

## 多轮对话

可以像跟人说话一样补充和修改：

- “给张三转账” → 追问金额 → “500” → 生成确认卡
- “改成800” → 重新生成确认卡
- “算了不转了” → 作废未执行的卡片

## 多步计划

“下周去日本玩5天，帮我安排一下”这类请求会拆成一个计划：核对余额、赎回理财、开通境外交易、返程后自动关闭、超预算提醒。

- 每个涉及资金或卡片的步骤，都会单独生成确认卡，逐笔等你确认。
- 说“停下”可以中断计划：没授权的步骤全部取消；已经执行的不回滚，回复里会列出来。

## 撤销

可以生成反向操作（仍需你确认）的有：冻结 ↔ 解冻、挂失 ↔ 解除挂失、限额调整（改回原值）、交易开关（恢复原设置）、心愿金锁定（释放）、理财申购 ↔ 赎回。

不能直接撤销的有：

- 转账已经到账，银行无法单方面撤回；可以联系收款人退回。
- 商户订单已支付，需要联系商户退款。
- 订阅已取消，需要在商户那里重新开通。

## 转人工

说“转人工”“找客服”会进入人工接管：此后你的消息不再发给模型，而是进入人工队列；说“取消转人工”回到智能助手。客服没有任何授权接口，不能替你确认资金操作。

## 自动化规则

可以用一句话设置“当……就……”的规则，例如“外卖每月超过800就提醒我”“工资到账后存1000到心愿金”。

- 触发条件：到点、入账、余额低于某值、某类消费超预算、出现大额消费。
- 动作：提醒、转账、存心愿金、申购理财、冻结卡、关闭交易开关、转人工。
- 规则要你确认后才生效；触发时涉及资金的操作仍然逐笔请你确认。

## 开发者模式

“设置 → 外观”里打开开发者模式后，每条回复下会出现“查看决策过程”：列出这一轮 Agent 之间的每条签名消息，并在页面上重新验签（消息签名、证据签名、因果父节点）。结果卡上可以下载签名证据包，见[证据包离线验证](#/evidence)。
`,dn="# 命令行\n\n所有命令都在项目根目录运行。用安装脚本安装时，请先激活虚拟环境（`source .venv/bin/activate`，Windows 为 `.venv\\Scripts\\activate`），或者把 `python` 换成 `.venv/bin/python`。\n\n## 日常\n\n| 命令 | 作用 |\n|---|---|\n| `./start.sh` / `.\\start.ps1` / 双击 `TRUSTA.cmd` | 启动服务和后台任务 |\n| `python -m scripts.setup` | 配置向导 |\n| `python -m scripts.setup --status` | 显示功能表 |\n| `python -m scripts.setup --probe` | 探测模型、银行、飞书的连通性后显示功能表 |\n| `python -m scripts.pair` | 生成手机配对码 |\n| `python -m scripts.pair --feishu` | 生成飞书绑定码 |\n| `python -m scripts.pair --devices` | 列出已配对设备 |\n| `python -m scripts.pair --revoke ID` | 吊销设备 |\n\n## 在终端里对话\n\n```bash\npython -m scripts.chat_cli             # 连接正在运行的服务\npython -m scripts.chat_cli --sandbox   # 进程内启动 + 临时数据库，不影响 data/\n```\n\n对话里可以用这些命令：\n\n| 命令 | 作用 |\n|---|---|\n| `/y N` / `/n N` | 确认 / 拒绝第 N 张卡片 |\n| `/sms N` | 为红色卡片 N 发送模拟短信验证码 |\n| `/otp N CODE` | 用短信验证码确认红色卡片 N |\n| `/totp N CODE` | 用认证器动态码确认红色卡片 N |\n| `/stop` | 中断当前计划 |\n| `/trace` | 查看上一轮的决策链 |\n| `/events` | 列出计划任务 |\n| `/advance ID` | 立即执行某个计划（仅演示模式） |\n| `/quit` | 退出 |\n\n## 验证与审计\n\n| 命令 | 作用 |\n|---|---|\n| `python -m scripts.verify_handoff_evidence BUNDLE --trusted-registry REGISTRY` | 离线验证签名证据包 |\n| `python -m scripts.verify_audit_record PROOF --trusted-public-key KEY` | 验证单条审计记录的 Merkle 包含证明 |\n| `python -m scripts.manage_crypto_identity --agent NAME --rotate` | 轮换 Agent 密钥 |\n| `python -m scripts.manage_crypto_identity --agent NAME --status SUSPENDED` | 暂停 Agent 身份 |\n| `python -m scripts.manage_crypto_identity --revoke-key KEY_ID` | 吊销密钥 |\n\n## 测试与评测\n\n| 命令 | 作用 |\n|---|---|\n| `python -m pytest -q` | 全部自动化测试（约 1,400 项） |\n| `python -m benchmarks.scenario_audit` | 六大场景 49 项闭环检查 |\n| `python -m benchmarks.security_benchmark --banking-version 5` | 18 类攻击实测 |\n\n## 后台 worker\n\n启动脚本会自动运行它们，一般不需要手动启动：\n\n| 命令 | 作用 |\n|---|---|\n| `python -m scripts.audit_worker --watch` | 生成签名 Merkle 检查点 |\n| `python -m scripts.scheduled_worker --watch` | 执行到期的计划（生日下单、周期转账等） |\n| `python -m scripts.feishu_worker` | 飞书长连接 |\n\n## 打包\n\n```bash\npython -m scripts.build_portable --python-embed python-3.12.10-embed-amd64.zip --out dist\n```\n\n生成 Windows 便携版 `dist/TRUSTA-windows-x64.zip`。仓库里的 GitHub Actions 工作流 `Packages` 会自动完成下载嵌入式 Python、打包、在 Windows 上启动自检，并在推送 `v*` 标签时附到发布页。\n",pn="# 配置项\n\nTRUSTA 的配置都是环境变量，保存在项目目录下的 `.env`（由[配置向导](#/setup)写入，已被 git 忽略）。也可以在启动前直接设置环境变量，环境变量优先。金额类配置的单位是**分**。\n\n## 大模型\n\n| 变量 | 默认 | 说明 |\n|---|---|---|\n| `DEEPSEEK_API_KEY` | — | 模型 API Key；为空时使用规则引擎 |\n| `TRUSTA_CHAT_LLM_ENDPOINT` | `https://api.deepseek.com/chat/completions` | 兼容 OpenAI 接口的地址 |\n| `TRUSTA_CHAT_LLM_MODEL` | `deepseek-flash` | 模型名 |\n| `TRUSTA_CHAT_LLM_PROXY` | — | HTTPS 代理 |\n| `TRUSTA_CHAT_LLM_TIMEOUT` | `30` | 单次调用超时（秒） |\n| `TRUSTA_CHAT_LLM_CONCURRENCY` | `4` | 并发调用上限 |\n| `TRUSTA_CHAT_LLM_DAILY_CALLS` | `300` | 每个用户每日调用上限；用完后当天改用规则引擎 |\n| `TRUSTA_LLM_CONFIG` | — | 评测用的模型配置文件路径 |\n\n## 银行\n\n| 变量 | 默认 | 说明 |\n|---|---|---|\n| `TRUSTA_BANK_CONFIG` | — | 外部银行沙箱配置文件（`config/bank.*.local.toml`）；为空时使用本地沙箱银行 |\n| `TRUSTA_BANKING_VERSION` | `5` | 银行动作集版本 |\n| `TRUSTA_DISABLED_FEATURES` | — | 逗号分隔，关闭指定功能，例如 `wealth,trip` |\n\n## 访问与渠道\n\n| 变量 | 默认 | 说明 |\n|---|---|---|\n| `TRUSTA_ACCESS_MODE` | `local` | `local` 仅本机 / `lan` 局域网 / `tunnel` 隧道 |\n| `TRUSTA_PUBLIC_HOST` | — | 隧道模式的公网域名（不带 `https://`） |\n| `TRUSTA_LAN_HOST` | 自动检测 | 局域网模式下手动指定本机地址 |\n| `TRUSTA_PORT` | `8000` | 飞书“回到 APP”链接里使用的端口（服务本身固定监听 8000） |\n| `TRUSTA_API_TOKEN` | 自动生成 | 本机访问令牌；默认保存在 `data/api.token` |\n| `TRUSTA_APP_URL` | 按访问方式推算 | 飞书里“回到 APP”链接使用的地址 |\n| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | — | 飞书应用凭据 |\n| `FEISHU_MODE` | `longconn` | `longconn` 长连接 / `webhook` |\n| `FEISHU_VERIFICATION_TOKEN` / `FEISHU_ENCRYPT_KEY` | — | webhook 模式使用 |\n| `FEISHU_USER_BINDINGS` | — | 运营方预置绑定，格式 `open_id:owner,open_id:owner` |\n\n## 安全\n\n| 变量 | 默认 | 说明 |\n|---|---|---|\n| `TRUSTA_SAFETY_PROFILE` | `competition` | `competition` 强验证 / `legacy` |\n| `TRUSTA_DEMO_MODE` | `false`（向导默认 `true`） | 演示模式：模拟短信验证码回显、计划可快进 |\n| `TRUSTA_HIGH_RISK_CENTS` | `500000` | 单笔 ≥ 此金额为红色（¥5,000） |\n| `TRUSTA_DAILY_TRANSFER_CENTS` | `100000` | 当日累计超过此金额为红色（¥1,000） |\n| `TRUSTA_ANOMALY_LARGE_CENTS` | `100000` | 大额消费异常提醒阈值 |\n| `TRUSTA_ANOMALY_DUPLICATE_SECONDS` | `300` | 疑似重复扣费的时间窗口 |\n| `TRUSTA_AUTH_FAILURE_LIMIT` | `5` | 连续验证失败多少次后安全锁定 |\n| `TRUSTA_EXECUTION_FAILURE_LIMIT` | `3` | 连续执行失败多少次后安全锁定 |\n| `TRUSTA_SECURITY_OPERATOR_TOKEN` | — | 银行人员令牌：解除转账保护、人工接管 |\n| `TRUSTA_STRONG_AUTH_SECRET` | — | `legacy` 安全档位下红色操作使用的强验证口令 |\n\n## 可信通信与审计\n\n| 变量 | 默认 | 说明 |\n|---|---|---|\n| `TRUSTA_COMMUNICATION_VERSION` | `3` | 签名通信协议版本；为 3 时启动审计和计划任务 worker |\n| `TRUSTA_CRYPTO_KEY_DIR` | `data/trusta.keys` | Agent 私钥存放目录（与数据库同名的 `.keys` 目录） |\n| `TRUSTA_AGENT_TIMEOUT_SECONDS` | `20` | 单个 Agent 处理超时 |\n| `TRUSTA_FINANCE_PROCESS_ENABLED` | `false` | 财务 Agent 以独立进程运行 |\n| `TRUSTA_BLOCKCHAIN_ANCHOR_ENABLED` | `false` | 审计检查点上链锚定 |\n| `TRUSTA_DID_ENABLED` | `true` | 跨机构协作的链上身份 |\n| `TRUSTA_ZKP_ENABLED` | `false` | 零知识能力准入 |\n| `TRUSTA_ZKP_ARTIFACT_DIR` / `TRUSTA_ZKP_MANIFEST_SHA256` | — | 零知识证明产物目录和清单哈希 |\n\n## 数据\n\n| 变量 | 默认 | 说明 |\n|---|---|---|\n| `TRUSTA_DB` | `data/trusta.sqlite3` | 数据库路径 |\n\n`data/` 里保存数据库、根签名密钥（`signing.key`）、Agent 密钥和日志，`.env` 保存你的密钥配置。两者都不要提交或分享。\n",un=`# 跨机构协作：WeID · 零知识准入 · 链上承诺

当多家机构的 Agent 需要共同完成一件事（例如几家银行的 Agent 交叉核对同一份风险结论），只靠本机签名还不够：对方怎么确认“你是谁”“你够不够格参与”“最后的结论有没有被改过”？TRUSTA 在[多 Agent 签名通信](#/agent-communication)的基础上叠加了一层可以在联盟链上公开核验的信任层。

![图 2 跨机构协作信任层与攻击实测](/figs/fig2.webp)

## 四个部分

### 链上身份：WeID

每个参与协作的 Agent 在 **FISCO BCOS** 联盟链上登记 **WeID**（微众银行开源的分布式身份），其 P-256 消息密钥与 WeID 绑定。身份可以注册、解析、停用和恢复。协作轮次只认链上 WeID；链不可用时直接拒绝，不会退回本地自签的 \`did:key\`。

### M1：零知识能力准入

Agent 用 **Groth16** 零知识证明说明：自己在固定评测集上的成绩（由 TP / FP / FN 按固定公式算出的 F1）达到策略阈值，同时**不公开评测明细和预测结果**。证明绑定策略、周期（Epoch）、模型承诺和评测记录；可以撤销，会到期。Agent 不能自己声明分数获得准入。

### M2：协作消息绑定

在原有 V3 签名之外，协作消息还要绑定链上身份、准入凭证、协作轮次和 Epoch。错误签名、跨轮次 / 任务 / Epoch、已撤销、模型不符、重放都会被拒绝。

### OTC：全窗口收集

协作采用 k-of-n：到截止时间才冻结证据，收到的有效结论不足 k 份则本轮作废；迟到的消息只用于遥测，不进入结论。

### M3：链上决策承诺

协作结论的 **Merkle 根**与成员快照写入 \`CollaborationAnchor\` 合约，并由两个节点的 RPC 分别读回核对。提交结果未知时按原哈希查找恢复，绝不盲目重发。

## 重要边界

- 链上身份和签名都**不授予任何银行写入权限**；资金操作仍须用户在确认卡上授权。
- 当前环境是**单机 8 节点测试联盟链**（Chain ID 101），两个验证 RPC 属于两个节点但在同一主机，不代表独立机构。
- Groth16 使用**开发用可信设置**（单机 Phase-2），不是生产级 MPC。
- 零知识电路证明的是受限的统计关系，不在电路里执行大模型推理。

这些限制都如实记录在仓库的 \`docs/ANCHOR_Integration_Final_Acceptance.md\` 中。

## 已登记的 Agent

| Agent | WeID |
|---|---|
| 财务 Finance | \`did:weid:101:0xa968103852c7eae1a245b77e98660730d8ccc3d3\` |
| 理财 Wealth | \`did:weid:101:0xa8fac68a926fb16debb085af40a1bb5d840df7de\` |
| 交易 Transaction | \`did:weid:101:0xf9fb420c820cf567b5250f816dd9a21c80f88c31\` |

## 开启方式

这一层默认关闭，需要先在 WSL / Linux 中运行测试联盟链：

\`\`\`bash
bash scripts/anchor_network_lifecycle.sh run
\`\`\`

相关配置项：\`TRUSTA_DID_ENABLED\`、\`TRUSTA_ZKP_ENABLED\`、\`TRUSTA_ZKP_ARTIFACT_DIR\`、\`TRUSTA_ZKP_MANIFEST_SHA256\`，见[配置项](#/configuration)。第三方可以用 \`python -m scripts.verify_anchor_collaboration\` 复验公开证据包。
`,gn=`# 证据包离线验证

TRUSTA 的每张结果卡都可以导出一个**签名证据包**。拿到证据包的人不需要相信这台服务器，也不需要连接银行或大模型，就能独立验证这一次处理过程中 Agent 之间的交接是否真实、完整、按顺序发生。

## 导出

1. 在网页“设置 → 外观”里打开开发者模式。
2. 在一张结果卡上打开“查看决策过程”，点击**下载证据包**，得到 \`trusta-evidence-xxxxxxxx.json\`。

## 验证

\`\`\`bash
python -m scripts.verify_handoff_evidence trusta-evidence-xxxxxxxx.json --trusted-registry registry.json
\`\`\`

成功时输出：

\`\`\`json
{"verified": true, "scope": "RUNTIME_CAUSAL_ATTESTATION_NOT_BANK_AUTHORIZATION"}
\`\`\`

退出码为 0；验证失败时 \`verified\` 为 \`false\`，退出码为 1。改动证据包里任何一个字节都会验证失败。

仓库里带有一份示例，可以直接试：

\`\`\`bash
python -m scripts.verify_handoff_evidence docs/agent-communication-demo.json --trusted-registry docs/agent-communication-trusted-registry.json
\`\`\`

## 验证了什么

- 每条交接证据的签名，以及签名者在注册表中的身份和密钥。
- 证据之间的因果父子关系：拒绝未知或缺失的父证据、重复或重排、跨任务混入。
- 身份、能力承诺、密钥归属没有被替换。
- 审计记录的 Merkle 包含证明。

## 没有验证什么

- \`scope\` 写得很清楚：这是**运行时因果声明**，不是银行授权。资金操作的授权来自你在确认卡上的决定。
- 隐藏的负载内容不会导出；输入输出哈希一致不等于内容语义正确。

## 公钥注册表从哪来

\`--trusted-registry\` 是各 Agent 公钥和能力的注册表。验证者应当**独立取得并固定**这份注册表（例如事先从可信渠道拿到并保存），而不是从证据包本身读取。历史快照只说明当时的状态，不表示密钥现在仍未被吊销。
`,mn=`# 常见问题

## 会动到我真实的银行账户吗？

默认不会。TRUSTA 默认连接本地虚拟银行沙箱，演示数据全部虚构。接入真实银行需要按 \`config/\` 中的适配器配置，并完成银行侧授权。

## 不接大模型能用吗？

能。不配置大模型时使用规则引擎，只识别标准说法，例如“给张三转500元”“这个月花了多少钱”。接入模型后可以理解更口语化的表达和多步请求。

## 大模型会看到我的余额吗？

不会。模型只看到你的原话、日期和资源名称（如收款人姓名、产品名），看不到余额、流水、卡号和验证码；长串数字发送前会被遮盖。回复里的数字都由银行系统查询后填入，并会核对是否出现在本轮查询结果中。

## 我在聊天里说“确认”，为什么没有执行？

这是有意的设计。授权只能在确认卡上完成，并且绑定卡上显示的那一笔。这样即使模型被诱导，或者有人冒用你的聊天窗口，也不能靠一句话转走钱。

## 确认卡过期了怎么办？

确认卡 15 分钟内有效。过期后重新说一遍需求，生成新的卡片即可。

## 验证码在哪？

演示模式下短信是模拟的：点击确认卡上的“发送验证码”，验证码会显示在页面的“模拟手机”里。也可以使用认证器动态码。

## 打开网页提示没有权限？

请使用启动窗口里打印的完整链接（带 \`#token=…\`）。手机需要先[配对](#/mobile)。

## 手机打不开？

确认三件事：访问方式设为局域网并已重启；手机和电脑连同一个 Wi-Fi 或热点；已经用配对码配对过。部分公共 Wi-Fi 会隔离设备，可以改用手机热点。

## 飞书机器人不回复？

检查 \`data/feishu-worker.log\` 是否显示已连接；确认已安装 \`requirements-channels.txt\`；确认在飞书开放平台选择了长连接并发布了版本；确认已用 \`python -m scripts.pair --feishu\` 绑定。

## Windows 提示“无法加载脚本，因为在此系统上禁止运行脚本”？

双击 \`TRUSTA.cmd\` 启动即可，它会以 \`-ExecutionPolicy Bypass\` 运行启动脚本。或者在 PowerShell 里运行：

\`\`\`powershell
powershell -ExecutionPolicy Bypass -File .\\start.ps1
\`\`\`

## 安装依赖很慢或失败？

国内网络可以给 pip 配置镜像，例如：

\`\`\`bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
\`\`\`

然后重新运行安装命令。

## 数据保存在哪？怎么卸载？

所有数据都在 TRUSTA 文件夹里：\`data/\` 是数据库、密钥和日志，\`.env\` 是配置。删除整个文件夹即完全卸载。

## 区块链和零知识证明是必须的吗？

不是。这一层用于多家机构的 Agent 协作，默认关闭；单机使用时，签名通信、证据链和审计已经全部生效。见[跨机构协作](#/cross-institution)。
`,fn=`# 飞书接入

把 TRUSTA 接成飞书机器人后，可以在飞书里和它对话。默认使用**长连接**：TRUSTA 主动连到飞书，不需要公网地址。

## 1. 在飞书开放平台创建应用

1. 创建企业自建应用，开启**机器人**能力。
2. “事件与回调”选择**使用长连接接收事件**。
3. 添加事件 \`im.message.receive_v1\`，以及卡片回调。
4. 发布版本。
5. 在“凭证与基础信息”里找到 **App ID**（以 \`cli_\` 开头）和 **App Secret**。

## 2. 填进 TRUSTA

\`\`\`bash
pip install -r requirements-channels.txt   # 长连接需要 lark-oapi
python -m scripts.setup
\`\`\`

在“接入飞书机器人？”一步选 \`y\`，填 App ID、App Secret，连接方式选 \`longconn\`。然后重启 TRUSTA。启动时窗口里会显示“飞书长连接已启动”，日志在 \`data/feishu-worker.log\`。

## 3. 绑定你的账户

飞书用户必须先绑定，才能以你的身份使用 TRUSTA：

\`\`\`bash
python -m scripts.pair --feishu
\`\`\`

窗口会显示一条“绑定 123456”。把这条消息私聊发给机器人即可。绑定码 5 分钟内有效，只能用一次。

## 在飞书里能做什么

- 所有查询和对话都可以直接在飞书完成。
- **黄色**操作：在飞书卡片上点确认。
- **红色**操作：飞书里不能输入验证码，机器人会给一个链接，在 TRUSTA 网页里打开同一个会话完成验证。这个链接使用你在[访问方式](#/setup)里设置的地址（局域网或隧道），所以手机也能打开。

## Webhook 模式

如果你已经有公网地址，也可以选 \`webhook\` 模式：飞书把事件推送到 \`POST /api/channels/feishu/webhook\`。需要额外填写 Verification Token，以及可选的 Encrypt Key。
`,yn=`# 安装

TRUSTA 有四种安装方式，效果一样，按你的习惯选。所有方式都只在一个文件夹里安装，不会改动系统环境。

| 方式 | 适合 | 需要 |
|---|---|---|
| 便携版安装包 | 只想用，不想装 Python（Windows） | 无 |
| 一行命令 | 大多数人 | Python 3.11+ |
| pip | 习惯用 Python 工具（即将推出） | Python 3.11+ |
| 源码 | 想改代码、跑测试、接自己的银行和模型 | Python 3.11+、git |

## 便携版（Windows）

1. 下载 \`TRUSTA-windows-x64.zip\`（[发布页](https://github.com/piggyz666/Trusta/releases)）。
2. 解压到任意文件夹，例如 \`D:\\TRUSTA\`。
3. 双击 \`TRUSTA.cmd\`。首次运行会打开[配置向导](#/setup)，之后直接启动。

压缩包里自带 Python 运行环境（在 \`runtime\\\` 里）和全部依赖，不需要另外安装任何东西。数据保存在同一文件夹的 \`data\\\` 里，删除文件夹即完全卸载。

macOS 和 Linux 的安装包即将推出，目前请用一行命令安装。

## 一行命令

**Windows（PowerShell）**

\`\`\`powershell
irm https://raw.githubusercontent.com/piggyz666/Trusta/main/install.ps1 | iex
\`\`\`

**macOS / Linux**

\`\`\`bash
curl -fsSL https://raw.githubusercontent.com/piggyz666/Trusta/main/install.sh | bash
\`\`\`

安装脚本会依次：

1. 检查 Python 3.11+。Windows 上没有时，会询问是否用 \`winget\` 为当前用户安装 Python 3.12；macOS / Linux 会提示安装方法后退出。
2. 把代码下载到 \`~/TRUSTA\`（Windows 为 \`%USERPROFILE%\\TRUSTA\`）。有 git 时用 \`git clone\`，没有时下载源码压缩包。
3. 在该目录创建独立的虚拟环境 \`.venv\`，安装依赖。
4. 打开配置向导。

可以用环境变量调整：

| 变量 | 作用 | 默认 |
|---|---|---|
| \`TRUSTA_HOME\` | 安装目录 | \`~/TRUSTA\` |
| \`TRUSTA_BRANCH\` | 下载哪个分支 | \`main\` |
| \`TRUSTA_NO_SETUP\` | 设为 \`1\` 时跳过配置向导 | — |

再次运行同一条命令会更新代码（git 安装时执行 \`git pull\`）并保留你的配置和数据。

## pip（即将推出）

发布到 PyPI 后：

\`\`\`bash
pip install trusta
trusta setup && trusta start
\`\`\`

## 从源码运行

\`\`\`bash
git clone https://github.com/piggyz666/Trusta.git && cd Trusta
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt -r requirements-channels.txt
python -m scripts.setup
./start.sh                           # Windows: .\\start.ps1
\`\`\`

- \`requirements.txt\` 是运行必需的依赖。
- \`requirements-channels.txt\` 是可选的：飞书长连接和终端二维码。
- \`requirements-llm.txt\`、\`requirements-phase6.txt\`、\`requirements-evaluation.txt\` 用于评测和外部审计集成，日常使用不需要。

修改网页前端后需要重新构建（需要 Node.js）：

\`\`\`bash
cd frontend-app
npm install
npm run build
\`\`\`

构建结果在 \`frontend/dist\`，由后端直接提供。

## 启动和停止

| 系统 | 启动 |
|---|---|
| Windows | 双击 \`TRUSTA.cmd\`，或在 PowerShell 里 \`.\\start.ps1\` |
| macOS / Linux | \`./start.sh\` |

启动脚本会同时运行：API 服务、审计检查点 worker、计划任务 worker（生日下单、周期转账等）；配置了飞书长连接时还会启动飞书 worker。后台 worker 的日志写在 \`data/*.log\`。关闭窗口或按 \`Ctrl+C\` 会一起停止。

启动脚本按以下顺序选择 Python：环境变量 \`TRUSTA_PYTHON\` → 便携版自带的 \`runtime/python\` → 安装脚本创建的 \`.venv\` → 系统里的 \`python\`。

## 卸载

TRUSTA 只写入它自己的文件夹。删除整个文件夹即可；\`data/\` 里是数据库和密钥，\`.env\` 里是你的配置，如有需要先备份。
`,kn=`# 手机访问

网页和手机 H5 是同一套页面，按屏幕自动适配。默认只能在本机访问；要用手机，需要把访问方式改为局域网或隧道，并配对一次。

## 1. 打开局域网访问

\`\`\`bash
python -m scripts.setup
\`\`\`

在“访问方式”一步选 **局域网**，然后重启 TRUSTA。

## 2. 配对手机

两种方式任选：

- **网页里**：打开“设置 → 访问与设备”，生成配对码，用手机扫码。
- **终端里**：

\`\`\`bash
python -m scripts.pair
\`\`\`

会显示配对地址、二维码（安装了 \`qrcode\` 包时）和 6 位配对码。手机连接同一个 Wi-Fi 或热点，扫码或在手机浏览器打开地址即可。配对码 5 分钟内有效，只能用一次。

配对成功后，手机会拿到自己的设备令牌，以后直接打开即可。在手机浏览器里选择“添加到主屏幕”，可以像 App 一样全屏使用。

## 管理设备

\`\`\`bash
python -m scripts.pair --devices        # 列出已配对设备
python -m scripts.pair --revoke 设备ID   # 吊销某台设备
\`\`\`

网页“设置 → 访问与设备”里也可以查看和吊销。

## 不在同一个网络时

如果你已经有 Cloudflare Tunnel 等公网地址，在配置向导里选择 **隧道**，填写域名（不带 \`https://\`）。没有隧道域名时会自动改为局域网模式。

> 局域网和隧道模式下，任何能访问这个地址的设备都需要配对才能使用；不过仅本机模式仍然是最安全的选择。
`,bn=`# TRUSTA 是什么

TRUSTA 是一个运行在你自己电脑上的银行 AI 助手。你用一句话说出想办的事，它理解后给出一张可以操作的卡片；涉及资金的每一步，都要你在卡片上亲手授权。

## 一句话，一张卡片

| 场景 | 可以这样说 |
|---|---|
| 智能转账 | “给张三转500元”“每月1号给房东转3000” |
| 账单分析 | “这个月花了多少钱”“上个月外卖花了多少” |
| 理财 | “推荐适合我的理财”“买1000低风险”“做一下风险测评” |
| 卡片管理 | “我的卡丢了”“关闭信用卡境外交易” |
| 订阅与代扣 | “我有哪些订阅”“最近有什么要续费” |
| 跨场景联动 | “下个月妈妈生日，帮我存2000买礼物”“下周去日本玩5天，帮我安排一下” |

回复是**可交互卡片**：转账卡上直接改金额，账单饼图点分类看明细，理财卡选产品直接申购，卡片开关直接拨动。

## 设计原则

- **AI 只能提议，不能授权。** 大模型只输出候选操作；每个参数都必须能在你的原话里找到出处，否则追问。在聊天里说“确认”不会执行任何操作，授权只能在确认卡上完成。
- **风险分级由银行规则决定。** 查询直接执行，资金和卡片操作要你确认，大额、新收款人、挂失等还要验证码。模型和前端都改不了等级。详见[授权等级](#/authorization)。
- **每一步都可追溯。** 六个 Agent 之间的每条消息都签名，每次交接都留下证据，事后可以离线验证。详见[多 Agent 签名通信](#/agent-communication)。
- **最少可见。** 大模型看不到余额、流水、卡号和验证码；回复里的数字由银行系统查询后填入。

## 它由什么组成

\`\`\`
你的一句话
  → 输入安全检查（注入、诱导转账、信息套取）
  → 总控 Agent：理解意图、抽取参数（每个参数要有原话出处）
  → 规划 Agent：拆成步骤，校验器检查
  → 业务 Agent（交易 / 财务 / 理财 / 客服）：查询或提出方案
  → 策略引擎定级 → 确认卡 → 你授权（必要时加验证码）
  → 封印的执行合约 → 银行网关执行 → 独立核验 → 回执
  → 审计哈希链 + 签名 Merkle 检查点
\`\`\`

- **后端**：Python 3.11+，SQLite，\`cryptography\`（ECDSA P-256）。
- **前端**：React 网页，桌面和手机同一套页面；也可以接入飞书机器人。
- **银行**：默认是本地虚拟银行沙箱，演示数据全部虚构；也可以按配置接入 Revolut / Starling 等外部银行沙箱。
- **大模型**：推荐 DeepSeek，也支持其他兼容 OpenAI 接口的模型；不接模型时用规则引擎，只认标准说法。

## 下一步

- 第一次使用：[快速开始](#/quickstart)
- 想了解安全设计：[授权等级](#/authorization)、[多 Agent 签名通信](#/agent-communication)
`,Tn=`# 快速开始

五分钟内在你自己的电脑上跑起 TRUSTA，并完成第一笔（虚拟的）转账。

> TRUSTA 默认连接本地虚拟银行沙箱，演示数据全部虚构，不会动到任何真实账户。

## 1. 安装

选一种方式即可，详见[安装](#/install)。

**Windows**：下载便携版压缩包，解压后双击 \`TRUSTA.cmd\`；或者在 PowerShell 里运行：

\`\`\`powershell
irm https://raw.githubusercontent.com/piggyz666/Trusta/main/install.ps1 | iex
\`\`\`

**macOS / Linux**：

\`\`\`bash
curl -fsSL https://raw.githubusercontent.com/piggyz666/Trusta/main/install.sh | bash
\`\`\`

## 2. 配置

安装完成后会自动打开配置向导，一共四步。第一次可以全部直接回车：

1. **大模型**：选 DeepSeek 并填 API Key；或者先选“不接入”，用规则引擎。
2. **银行**：本地沙箱银行（默认）。
3. **访问方式**：仅本机（默认，最安全）。
4. **安全与演示**：\`competition\` 安全档位 + 演示模式（短信验证码会直接显示在页面上）。

以后随时可以重新运行 \`python -m scripts.setup\` 修改。详见[首次配置](#/setup)。

## 3. 启动

\`\`\`bash
./start.sh          # macOS / Linux
\`\`\`

\`\`\`powershell
.\\start.ps1         # Windows，或者双击 TRUSTA.cmd
\`\`\`

窗口里会打印一条 \`http://127.0.0.1:8000/#token=…\` 链接，用浏览器打开。链接里的 token 是本机访问凭证，不要发给别人。

## 4. 试几句话

- “我这个月花了多少钱”：只读查询，直接返回报告卡。
- “给张三转500元”：生成一张黄色确认卡，点“确认”才会执行。
- “我的卡丢了”：红色操作，需要验证码；演示模式下验证码显示在“模拟手机”里。

## 5. 看看它是怎么做到的

在网页“设置 → 外观”里打开**开发者模式**，每条回复下面会出现“查看决策过程”，可以看到 Agent 之间的每条签名消息，并当场重新验签。

## 接下来

- 用手机访问：[手机访问](#/mobile)
- 接入飞书：[飞书接入](#/feishu)
- 在终端里对话：\`python -m scripts.chat_cli\`
`,wn=`# 首次配置

配置向导会把设置写入项目目录下的 \`.env\`。这个文件只在你的电脑上，已被 git 忽略；向导写入时会收紧文件权限，输入密钥时不回显。

\`\`\`bash
python -m scripts.setup            # 交互式配置
python -m scripts.setup --status   # 只显示功能表
python -m scripts.setup --probe    # 探测连通性后显示功能表（会调用一次模型）
\`\`\`

## 1/4 大模型

| 选项 | 说明 |
|---|---|
| DeepSeek（推荐） | 填 API Key（以 \`sk-\` 开头），模型名默认 \`deepseek-flash\` |
| 其他兼容 OpenAI 接口的模型 | 填接口地址（\`https://…/chat/completions\`）、模型名和 Key |
| 不接入 | 使用规则引擎，只识别标准说法，例如“给张三转500元” |

需要时可以填 HTTPS 代理。无论接哪个模型，模型都只能提出方案，看不到余额、流水、卡号和验证码。

## 2/4 银行

| 选项 | 说明 |
|---|---|
| 本地沙箱银行（默认） | 六大场景全部可用，包括入账推送；演示数据全部虚构 |
| 外部银行沙箱 | Revolut / Starling 等，需要按 \`config/bank.*.example.toml\` 写一份 \`config/bank.*.local.toml\` |

## 3/4 访问方式与渠道

| 选项 | 说明 |
|---|---|
| 仅本机（默认） | 只能在这台电脑的浏览器里打开，最安全 |
| 局域网 | 手机连同一 Wi-Fi 或热点，扫码配对一次即可使用，见[手机访问](#/mobile) |
| 隧道 | 已有 Cloudflare Tunnel 等公网地址时使用；没有隧道域名会自动改为局域网 |

接着会询问是否接入飞书机器人，见[飞书接入](#/feishu)。

## 4/4 安全与演示

| 设置 | 说明 |
|---|---|
| 安全档位 \`competition\`（默认） | 强验证：当日累计、转账频率、新收款人、理财申赎等规则全部生效 |
| 安全档位 \`legacy\` | 早期版本的规则，仅用于对比 |
| 演示模式 \`true\`（默认） | 模拟短信验证码直接显示在页面的“模拟手机”里；计划任务可以快进 |
| 演示模式 \`false\` | 不回显验证码 |

## 功能表

向导最后会显示一张功能表。TRUSTA 根据实际接入、并且探测通过的能力决定哪些功能可用：

\`\`\`
模型：deepseek-flash（VERIFIED）  银行：virtual/local  安全档位：competition

【六大场景】
  ✔ 可用  智能转账（姓名 / 手机号 / 称呼 / 定时 / 周期 / 备注）
  ✔ 可用  账单分析 / 消费查询 / 异常识别
  ...
【渠道】
  ✘ 不可用 飞书渠道  —— 缺少：飞书机器人
        开通方法：运行 python -m scripts.setup 填写 FEISHU_APP_ID / FEISHU_APP_SECRET
\`\`\`

没开通的功能，助手看不到也调用不了；网页的“功能”页也会告诉你需要接什么。

完整的配置项见[配置项](#/configuration)。
`;function se(){return{async:!1,breaks:!1,extensions:null,gfm:!0,hooks:null,pedantic:!1,renderer:null,silent:!1,tokenizer:null,walkTokens:null}}var L=se();function we(n){L=n}var H={exec:()=>null};function k(n,e=""){let t=typeof n=="string"?n:n.source;const s={replace:(a,i)=>{let c=typeof i=="string"?i:i.source;return c=c.replace(x.caret,"$1"),t=t.replace(a,c),s},getRegex:()=>new RegExp(t,e)};return s}var x={codeRemoveIndent:/^(?: {1,4}| {0,3}\t)/gm,outputLinkReplace:/\\([\[\]])/g,indentCodeCompensation:/^(\s+)(?:```)/,beginningSpace:/^\s+/,endingHash:/#$/,startingSpaceChar:/^ /,endingSpaceChar:/ $/,nonSpaceChar:/[^ ]/,newLineCharGlobal:/\n/g,tabCharGlobal:/\t/g,multipleSpaceGlobal:/\s+/g,blankLine:/^[ \t]*$/,doubleBlankLine:/\n[ \t]*\n[ \t]*$/,blockquoteStart:/^ {0,3}>/,blockquoteSetextReplace:/\n {0,3}((?:=+|-+) *)(?=\n|$)/g,blockquoteSetextReplace2:/^ {0,3}>[ \t]?/gm,listReplaceTabs:/^\t+/,listReplaceNesting:/^ {1,4}(?=( {4})*[^ ])/g,listIsTask:/^\[[ xX]\] /,listReplaceTask:/^\[[ xX]\] +/,anyLine:/\n.*\n/,hrefBrackets:/^<(.*)>$/,tableDelimiter:/[:|]/,tableAlignChars:/^\||\| *$/g,tableRowBlankLine:/\n[ \t]*$/,tableAlignRight:/^ *-+: *$/,tableAlignCenter:/^ *:-+: *$/,tableAlignLeft:/^ *:-+ *$/,startATag:/^<a /i,endATag:/^<\/a>/i,startPreScriptTag:/^<(pre|code|kbd|script)(\s|>)/i,endPreScriptTag:/^<\/(pre|code|kbd|script)(\s|>)/i,startAngleBracket:/^</,endAngleBracket:/>$/,pedanticHrefTitle:/^([^'"]*[^\s])\s+(['"])(.*)\2/,unicodeAlphaNumeric:/[\p{L}\p{N}]/u,escapeTest:/[&<>"']/,escapeReplace:/[&<>"']/g,escapeTestNoEncode:/[<>"']|&(?!(#\d{1,7}|#[Xx][a-fA-F0-9]{1,6}|\w+);)/,escapeReplaceNoEncode:/[<>"']|&(?!(#\d{1,7}|#[Xx][a-fA-F0-9]{1,6}|\w+);)/g,unescapeTest:/&(#(?:\d+)|(?:#x[0-9A-Fa-f]+)|(?:\w+));?/ig,caret:/(^|[^\[])\^/g,percentDecode:/%25/g,findPipe:/\|/g,splitPipe:/ \|/,slashPipe:/\\\|/g,carriageReturn:/\r\n|\r/g,spaceLine:/^ +$/gm,notSpaceStart:/^\S*/,endingNewline:/\n$/,listItemRegex:n=>new RegExp(`^( {0,3}${n})((?:[	 ][^\\n]*)?(?:\\n|$))`),nextBulletRegex:n=>new RegExp(`^ {0,${Math.min(3,n-1)}}(?:[*+-]|\\d{1,9}[.)])((?:[ 	][^\\n]*)?(?:\\n|$))`),hrRegex:n=>new RegExp(`^ {0,${Math.min(3,n-1)}}((?:- *){3,}|(?:_ *){3,}|(?:\\* *){3,})(?:\\n+|$)`),fencesBeginRegex:n=>new RegExp(`^ {0,${Math.min(3,n-1)}}(?:\`\`\`|~~~)`),headingBeginRegex:n=>new RegExp(`^ {0,${Math.min(3,n-1)}}#`),htmlBeginRegex:n=>new RegExp(`^ {0,${Math.min(3,n-1)}}<(?:[a-z].*>|!--)`,"i")},Sn=/^(?:[ \t]*(?:\n|$))+/,vn=/^((?: {4}| {0,3}\t)[^\n]+(?:\n(?:[ \t]*(?:\n|$))*)?)+/,_n=/^ {0,3}(`{3,}(?=[^`\n]*(?:\n|$))|~{3,})([^\n]*)(?:\n|$)(?:|([\s\S]*?)(?:\n|$))(?: {0,3}\1[~`]* *(?=\n|$)|$)/,W=/^ {0,3}((?:-[\t ]*){3,}|(?:_[ \t]*){3,}|(?:\*[ \t]*){3,})(?:\n+|$)/,An=/^ {0,3}(#{1,6})(?=\s|$)(.*)(?:\n+|$)/,ae=/(?:[*+-]|\d{1,9}[.)])/,Se=/^(?!bull |blockCode|fences|blockquote|heading|html|table)((?:.|\n(?!\s*?\n|bull |blockCode|fences|blockquote|heading|html|table))+?)\n {0,3}(=+|-+) *(?:\n+|$)/,ve=k(Se).replace(/bull/g,ae).replace(/blockCode/g,/(?: {4}| {0,3}\t)/).replace(/fences/g,/ {0,3}(?:`{3,}|~{3,})/).replace(/blockquote/g,/ {0,3}>/).replace(/heading/g,/ {0,3}#{1,6}/).replace(/html/g,/ {0,3}<[^\n>]+>\n/).replace(/\|table/g,"").getRegex(),xn=k(Se).replace(/bull/g,ae).replace(/blockCode/g,/(?: {4}| {0,3}\t)/).replace(/fences/g,/ {0,3}(?:`{3,}|~{3,})/).replace(/blockquote/g,/ {0,3}>/).replace(/heading/g,/ {0,3}#{1,6}/).replace(/html/g,/ {0,3}<[^\n>]+>\n/).replace(/table/g,/ {0,3}\|?(?:[:\- ]*\|)+[\:\- ]*\n/).getRegex(),ie=/^([^\n]+(?:\n(?!hr|heading|lheading|blockquote|fences|list|html|table| +\n)[^\n]+)*)/,Rn=/^[^\n]+/,oe=/(?!\s*\])(?:\\.|[^\[\]\\])+/,En=k(/^ {0,3}\[(label)\]: *(?:\n[ \t]*)?([^<\s][^\s]*|<.*?>)(?:(?: +(?:\n[ \t]*)?| *\n[ \t]*)(title))? *(?:\n+|$)/).replace("label",oe).replace("title",/(?:"(?:\\"?|[^"\\])*"|'[^'\n]*(?:\n[^'\n]+)*\n?'|\([^()]*\))/).getRegex(),In=k(/^( {0,3}bull)([ \t][^\n]+?)?(?:\n|$)/).replace(/bull/g,ae).getRegex(),Q="address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|meta|nav|noframes|ol|optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul",re=/<!--(?:-?>|[\s\S]*?(?:-->|$))/,Cn=k("^ {0,3}(?:<(script|pre|style|textarea)[\\s>][\\s\\S]*?(?:</\\1>[^\\n]*\\n+|$)|comment[^\\n]*(\\n+|$)|<\\?[\\s\\S]*?(?:\\?>\\n*|$)|<![A-Z][\\s\\S]*?(?:>\\n*|$)|<!\\[CDATA\\[[\\s\\S]*?(?:\\]\\]>\\n*|$)|</?(tag)(?: +|\\n|/?>)[\\s\\S]*?(?:(?:\\n[ 	]*)+\\n|$)|<(?!script|pre|style|textarea)([a-z][\\w-]*)(?:attribute)*? */?>(?=[ \\t]*(?:\\n|$))[\\s\\S]*?(?:(?:\\n[ 	]*)+\\n|$)|</(?!script|pre|style|textarea)[a-z][\\w-]*\\s*>(?=[ \\t]*(?:\\n|$))[\\s\\S]*?(?:(?:\\n[ 	]*)+\\n|$))","i").replace("comment",re).replace("tag",Q).replace("attribute",/ +[a-zA-Z:_][\w.:-]*(?: *= *"[^"\n]*"| *= *'[^'\n]*'| *= *[^\s"'=<>`]+)?/).getRegex(),_e=k(ie).replace("hr",W).replace("heading"," {0,3}#{1,6}(?:\\s|$)").replace("|lheading","").replace("|table","").replace("blockquote"," {0,3}>").replace("fences"," {0,3}(?:`{3,}(?=[^`\\n]*\\n)|~{3,})[^\\n]*\\n").replace("list"," {0,3}(?:[*+-]|1[.)]) ").replace("html","</?(?:tag)(?: +|\\n|/?>)|<(?:script|pre|style|textarea|!--)").replace("tag",Q).getRegex(),Pn=k(/^( {0,3}> ?(paragraph|[^\n]*)(?:\n|$))+/).replace("paragraph",_e).getRegex(),le={blockquote:Pn,code:vn,def:En,fences:_n,heading:An,hr:W,html:Cn,lheading:ve,list:In,newline:Sn,paragraph:_e,table:H,text:Rn},ge=k("^ *([^\\n ].*)\\n {0,3}((?:\\| *)?:?-+:? *(?:\\| *:?-+:? *)*(?:\\| *)?)(?:\\n((?:(?! *\\n|hr|heading|blockquote|code|fences|list|html).*(?:\\n|$))*)\\n*|$)").replace("hr",W).replace("heading"," {0,3}#{1,6}(?:\\s|$)").replace("blockquote"," {0,3}>").replace("code","(?: {4}| {0,3}	)[^\\n]").replace("fences"," {0,3}(?:`{3,}(?=[^`\\n]*\\n)|~{3,})[^\\n]*\\n").replace("list"," {0,3}(?:[*+-]|1[.)]) ").replace("html","</?(?:tag)(?: +|\\n|/?>)|<(?:script|pre|style|textarea|!--)").replace("tag",Q).getRegex(),Un={...le,lheading:xn,table:ge,paragraph:k(ie).replace("hr",W).replace("heading"," {0,3}#{1,6}(?:\\s|$)").replace("|lheading","").replace("table",ge).replace("blockquote"," {0,3}>").replace("fences"," {0,3}(?:`{3,}(?=[^`\\n]*\\n)|~{3,})[^\\n]*\\n").replace("list"," {0,3}(?:[*+-]|1[.)]) ").replace("html","</?(?:tag)(?: +|\\n|/?>)|<(?:script|pre|style|textarea|!--)").replace("tag",Q).getRegex()},Nn={...le,html:k(`^ *(?:comment *(?:\\n|\\s*$)|<(tag)[\\s\\S]+?</\\1> *(?:\\n{2,}|\\s*$)|<tag(?:"[^"]*"|'[^']*'|\\s[^'"/>\\s]*)*?/?> *(?:\\n{2,}|\\s*$))`).replace("comment",re).replace(/tag/g,"(?!(?:a|em|strong|small|s|cite|q|dfn|abbr|data|time|code|var|samp|kbd|sub|sup|i|b|u|mark|ruby|rt|rp|bdi|bdo|span|br|wbr|ins|del|img)\\b)\\w+(?!:|[^\\w\\s@]*@)\\b").getRegex(),def:/^ *\[([^\]]+)\]: *<?([^\s>]+)>?(?: +(["(][^\n]+[")]))? *(?:\n+|$)/,heading:/^(#{1,6})(.*)(?:\n+|$)/,fences:H,lheading:/^(.+?)\n {0,3}(=+|-+) *(?:\n+|$)/,paragraph:k(ie).replace("hr",W).replace("heading",` *#{1,6} *[^
]`).replace("lheading",ve).replace("|table","").replace("blockquote"," {0,3}>").replace("|fences","").replace("|list","").replace("|html","").replace("|tag","").getRegex()},Ln=/^\\([!"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])/,On=/^(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/,Ae=/^( {2,}|\\)\n(?!\s*$)/,Dn=/^(`+|[^`])(?:(?= {2,}\n)|[\s\S]*?(?:(?=[\\<!\[`*_]|\b_|$)|[^ ](?= {2,}\n)))/,X=/[\p{P}\p{S}]/u,ce=/[\s\p{P}\p{S}]/u,xe=/[^\s\p{P}\p{S}]/u,Mn=k(/^((?![*_])punctSpace)/,"u").replace(/punctSpace/g,ce).getRegex(),Re=/(?!~)[\p{P}\p{S}]/u,zn=/(?!~)[\s\p{P}\p{S}]/u,Fn=/(?:[^\s\p{P}\p{S}]|~)/u,$n=/\[[^[\]]*?\]\((?:\\.|[^\\\(\)]|\((?:\\.|[^\\\(\)])*\))*\)|`[^`]*?`|<[^<>]*?>/g,Ee=/^(?:\*+(?:((?!\*)punct)|[^\s*]))|^_+(?:((?!_)punct)|([^\s_]))/,jn=k(Ee,"u").replace(/punct/g,X).getRegex(),Bn=k(Ee,"u").replace(/punct/g,Re).getRegex(),Ie="^[^_*]*?__[^_*]*?\\*[^_*]*?(?=__)|[^*]+(?=[^*])|(?!\\*)punct(\\*+)(?=[\\s]|$)|notPunctSpace(\\*+)(?!\\*)(?=punctSpace|$)|(?!\\*)punctSpace(\\*+)(?=notPunctSpace)|[\\s](\\*+)(?!\\*)(?=punct)|(?!\\*)punct(\\*+)(?!\\*)(?=punct)|notPunctSpace(\\*+)(?=notPunctSpace)",Hn=k(Ie,"gu").replace(/notPunctSpace/g,xe).replace(/punctSpace/g,ce).replace(/punct/g,X).getRegex(),Wn=k(Ie,"gu").replace(/notPunctSpace/g,Fn).replace(/punctSpace/g,zn).replace(/punct/g,Re).getRegex(),qn=k("^[^_*]*?\\*\\*[^_*]*?_[^_*]*?(?=\\*\\*)|[^_]+(?=[^_])|(?!_)punct(_+)(?=[\\s]|$)|notPunctSpace(_+)(?!_)(?=punctSpace|$)|(?!_)punctSpace(_+)(?=notPunctSpace)|[\\s](_+)(?!_)(?=punct)|(?!_)punct(_+)(?!_)(?=punct)","gu").replace(/notPunctSpace/g,xe).replace(/punctSpace/g,ce).replace(/punct/g,X).getRegex(),Gn=k(/\\(punct)/,"gu").replace(/punct/g,X).getRegex(),Kn=k(/^<(scheme:[^\s\x00-\x1f<>]*|email)>/).replace("scheme",/[a-zA-Z][a-zA-Z0-9+.-]{1,31}/).replace("email",/[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+(@)[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+(?![-_])/).getRegex(),Yn=k(re).replace("(?:-->|$)","-->").getRegex(),Zn=k("^comment|^</[a-zA-Z][\\w:-]*\\s*>|^<[a-zA-Z][\\w-]*(?:attribute)*?\\s*/?>|^<\\?[\\s\\S]*?\\?>|^<![a-zA-Z]+\\s[\\s\\S]*?>|^<!\\[CDATA\\[[\\s\\S]*?\\]\\]>").replace("comment",Yn).replace("attribute",/\s+[a-zA-Z:_][\w.:-]*(?:\s*=\s*"[^"]*"|\s*=\s*'[^']*'|\s*=\s*[^\s"'=<>`]+)?/).getRegex(),Y=/(?:\[(?:\\.|[^\[\]\\])*\]|\\.|`[^`]*`|[^\[\]\\`])*?/,Vn=k(/^!?\[(label)\]\(\s*(href)(?:(?:[ \t]*(?:\n[ \t]*)?)(title))?\s*\)/).replace("label",Y).replace("href",/<(?:\\.|[^\n<>\\])+>|[^ \t\n\x00-\x1f]*/).replace("title",/"(?:\\"?|[^"\\])*"|'(?:\\'?|[^'\\])*'|\((?:\\\)?|[^)\\])*\)/).getRegex(),Ce=k(/^!?\[(label)\]\[(ref)\]/).replace("label",Y).replace("ref",oe).getRegex(),Pe=k(/^!?\[(ref)\](?:\[\])?/).replace("ref",oe).getRegex(),Qn=k("reflink|nolink(?!\\()","g").replace("reflink",Ce).replace("nolink",Pe).getRegex(),he={_backpedal:H,anyPunctuation:Gn,autolink:Kn,blockSkip:$n,br:Ae,code:On,del:H,emStrongLDelim:jn,emStrongRDelimAst:Hn,emStrongRDelimUnd:qn,escape:Ln,link:Vn,nolink:Pe,punctuation:Mn,reflink:Ce,reflinkSearch:Qn,tag:Zn,text:Dn,url:H},Xn={...he,link:k(/^!?\[(label)\]\((.*?)\)/).replace("label",Y).getRegex(),reflink:k(/^!?\[(label)\]\s*\[([^\]]*)\]/).replace("label",Y).getRegex()},ee={...he,emStrongRDelimAst:Wn,emStrongLDelim:Bn,url:k(/^((?:ftp|https?):\/\/|www\.)(?:[a-zA-Z0-9\-]+\.?)+[^\s<]*|^email/,"i").replace("email",/[A-Za-z0-9._+-]+(@)[a-zA-Z0-9-_]+(?:\.[a-zA-Z0-9-_]*[a-zA-Z0-9])+(?![-_])/).getRegex(),_backpedal:/(?:[^?!.,:;*_'"~()&]+|\([^)]*\)|&(?![a-zA-Z0-9]+;$)|[?!.,:;*_'"~)]+(?!$))+/,del:/^(~~?)(?=[^\s~])((?:\\.|[^\\])*?(?:\\.|[^\s~\\]))\1(?=[^~]|$)/,text:/^([`~]+|[^`~])(?:(?= {2,}\n)|(?=[a-zA-Z0-9.!#$%&'*+\/=?_`{\|}~-]+@)|[\s\S]*?(?:(?=[\\<!\[`*~_]|\b_|https?:\/\/|ftp:\/\/|www\.|$)|[^ ](?= {2,}\n)|[^a-zA-Z0-9.!#$%&'*+\/=?_`{\|}~-](?=[a-zA-Z0-9.!#$%&'*+\/=?_`{\|}~-]+@)))/},Jn={...ee,br:k(Ae).replace("{2,}","*").getRegex(),text:k(ee.text).replace("\\b_","\\b_| {2,}\\n").replace(/\{2,\}/g,"*").getRegex()},G={normal:le,gfm:Un,pedantic:Nn},j={normal:he,gfm:ee,breaks:Jn,pedantic:Xn},et={"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"},me=n=>et[n];function R(n,e){if(e){if(x.escapeTest.test(n))return n.replace(x.escapeReplace,me)}else if(x.escapeTestNoEncode.test(n))return n.replace(x.escapeReplaceNoEncode,me);return n}function fe(n){try{n=encodeURI(n).replace(x.percentDecode,"%")}catch{return null}return n}function ye(n,e){var i;const t=n.replace(x.findPipe,(c,o,h)=>{let r=!1,l=o;for(;--l>=0&&h[l]==="\\";)r=!r;return r?"|":" |"}),s=t.split(x.splitPipe);let a=0;if(s[0].trim()||s.shift(),s.length>0&&!((i=s.at(-1))!=null&&i.trim())&&s.pop(),e)if(s.length>e)s.splice(e);else for(;s.length<e;)s.push("");for(;a<s.length;a++)s[a]=s[a].trim().replace(x.slashPipe,"|");return s}function B(n,e,t){const s=n.length;if(s===0)return"";let a=0;for(;a<s&&n.charAt(s-a-1)===e;)a++;return n.slice(0,s-a)}function nt(n,e){if(n.indexOf(e[1])===-1)return-1;let t=0;for(let s=0;s<n.length;s++)if(n[s]==="\\")s++;else if(n[s]===e[0])t++;else if(n[s]===e[1]&&(t--,t<0))return s;return t>0?-2:-1}function ke(n,e,t,s,a){const i=e.href,c=e.title||null,o=n[1].replace(a.other.outputLinkReplace,"$1");s.state.inLink=!0;const h={type:n[0].charAt(0)==="!"?"image":"link",raw:t,href:i,title:c,text:o,tokens:s.inlineTokens(o)};return s.state.inLink=!1,h}function tt(n,e,t){const s=n.match(t.other.indentCodeCompensation);if(s===null)return e;const a=s[1];return e.split(`
`).map(i=>{const c=i.match(t.other.beginningSpace);if(c===null)return i;const[o]=c;return o.length>=a.length?i.slice(a.length):i}).join(`
`)}var Z=class{constructor(n){b(this,"options");b(this,"rules");b(this,"lexer");this.options=n||L}space(n){const e=this.rules.block.newline.exec(n);if(e&&e[0].length>0)return{type:"space",raw:e[0]}}code(n){const e=this.rules.block.code.exec(n);if(e){const t=e[0].replace(this.rules.other.codeRemoveIndent,"");return{type:"code",raw:e[0],codeBlockStyle:"indented",text:this.options.pedantic?t:B(t,`
`)}}}fences(n){const e=this.rules.block.fences.exec(n);if(e){const t=e[0],s=tt(t,e[3]||"",this.rules);return{type:"code",raw:t,lang:e[2]?e[2].trim().replace(this.rules.inline.anyPunctuation,"$1"):e[2],text:s}}}heading(n){const e=this.rules.block.heading.exec(n);if(e){let t=e[2].trim();if(this.rules.other.endingHash.test(t)){const s=B(t,"#");(this.options.pedantic||!s||this.rules.other.endingSpaceChar.test(s))&&(t=s.trim())}return{type:"heading",raw:e[0],depth:e[1].length,text:t,tokens:this.lexer.inline(t)}}}hr(n){const e=this.rules.block.hr.exec(n);if(e)return{type:"hr",raw:B(e[0],`
`)}}blockquote(n){const e=this.rules.block.blockquote.exec(n);if(e){let t=B(e[0],`
`).split(`
`),s="",a="";const i=[];for(;t.length>0;){let c=!1;const o=[];let h;for(h=0;h<t.length;h++)if(this.rules.other.blockquoteStart.test(t[h]))o.push(t[h]),c=!0;else if(!c)o.push(t[h]);else break;t=t.slice(h);const r=o.join(`
`),l=r.replace(this.rules.other.blockquoteSetextReplace,`
    $1`).replace(this.rules.other.blockquoteSetextReplace2,"");s=s?`${s}
${r}`:r,a=a?`${a}
${l}`:l;const f=this.lexer.state.top;if(this.lexer.state.top=!0,this.lexer.blockTokens(l,i,!0),this.lexer.state.top=f,t.length===0)break;const p=i.at(-1);if((p==null?void 0:p.type)==="code")break;if((p==null?void 0:p.type)==="blockquote"){const u=p,g=u.raw+`
`+t.join(`
`),_=this.blockquote(g);i[i.length-1]=_,s=s.substring(0,s.length-u.raw.length)+_.raw,a=a.substring(0,a.length-u.text.length)+_.text;break}else if((p==null?void 0:p.type)==="list"){const u=p,g=u.raw+`
`+t.join(`
`),_=this.list(g);i[i.length-1]=_,s=s.substring(0,s.length-p.raw.length)+_.raw,a=a.substring(0,a.length-u.raw.length)+_.raw,t=g.substring(i.at(-1).raw.length).split(`
`);continue}}return{type:"blockquote",raw:s,tokens:i,text:a}}}list(n){let e=this.rules.block.list.exec(n);if(e){let t=e[1].trim();const s=t.length>1,a={type:"list",raw:"",ordered:s,start:s?+t.slice(0,-1):"",loose:!1,items:[]};t=s?`\\d{1,9}\\${t.slice(-1)}`:`\\${t}`,this.options.pedantic&&(t=s?t:"[*+-]");const i=this.rules.other.listItemRegex(t);let c=!1;for(;n;){let h=!1,r="",l="";if(!(e=i.exec(n))||this.rules.block.hr.test(n))break;r=e[0],n=n.substring(r.length);let f=e[2].split(`
`,1)[0].replace(this.rules.other.listReplaceTabs,C=>" ".repeat(3*C.length)),p=n.split(`
`,1)[0],u=!f.trim(),g=0;if(this.options.pedantic?(g=2,l=f.trimStart()):u?g=e[1].length+1:(g=e[2].search(this.rules.other.nonSpaceChar),g=g>4?1:g,l=f.slice(g),g+=e[1].length),u&&this.rules.other.blankLine.test(p)&&(r+=p+`
`,n=n.substring(p.length+1),h=!0),!h){const C=this.rules.other.nextBulletRegex(g),F=this.rules.other.hrRegex(g),D=this.rules.other.fencesBeginRegex(g),M=this.rules.other.headingBeginRegex(g),q=this.rules.other.htmlBeginRegex(g);for(;n;){const $=n.split(`
`,1)[0];let P;if(p=$,this.options.pedantic?(p=p.replace(this.rules.other.listReplaceNesting,"  "),P=p):P=p.replace(this.rules.other.tabCharGlobal,"    "),D.test(p)||M.test(p)||q.test(p)||C.test(p)||F.test(p))break;if(P.search(this.rules.other.nonSpaceChar)>=g||!p.trim())l+=`
`+P.slice(g);else{if(u||f.replace(this.rules.other.tabCharGlobal,"    ").search(this.rules.other.nonSpaceChar)>=4||D.test(f)||M.test(f)||F.test(f))break;l+=`
`+p}!u&&!p.trim()&&(u=!0),r+=$+`
`,n=n.substring($.length+1),f=P.slice(g)}}a.loose||(c?a.loose=!0:this.rules.other.doubleBlankLine.test(r)&&(c=!0));let _=null,O;this.options.gfm&&(_=this.rules.other.listIsTask.exec(l),_&&(O=_[0]!=="[ ] ",l=l.replace(this.rules.other.listReplaceTask,""))),a.items.push({type:"list_item",raw:r,task:!!_,checked:O,loose:!1,text:l,tokens:[]}),a.raw+=r}const o=a.items.at(-1);if(o)o.raw=o.raw.trimEnd(),o.text=o.text.trimEnd();else return;a.raw=a.raw.trimEnd();for(let h=0;h<a.items.length;h++)if(this.lexer.state.top=!1,a.items[h].tokens=this.lexer.blockTokens(a.items[h].text,[]),!a.loose){const r=a.items[h].tokens.filter(f=>f.type==="space"),l=r.length>0&&r.some(f=>this.rules.other.anyLine.test(f.raw));a.loose=l}if(a.loose)for(let h=0;h<a.items.length;h++)a.items[h].loose=!0;return a}}html(n){const e=this.rules.block.html.exec(n);if(e)return{type:"html",block:!0,raw:e[0],pre:e[1]==="pre"||e[1]==="script"||e[1]==="style",text:e[0]}}def(n){const e=this.rules.block.def.exec(n);if(e){const t=e[1].toLowerCase().replace(this.rules.other.multipleSpaceGlobal," "),s=e[2]?e[2].replace(this.rules.other.hrefBrackets,"$1").replace(this.rules.inline.anyPunctuation,"$1"):"",a=e[3]?e[3].substring(1,e[3].length-1).replace(this.rules.inline.anyPunctuation,"$1"):e[3];return{type:"def",tag:t,raw:e[0],href:s,title:a}}}table(n){var c;const e=this.rules.block.table.exec(n);if(!e||!this.rules.other.tableDelimiter.test(e[2]))return;const t=ye(e[1]),s=e[2].replace(this.rules.other.tableAlignChars,"").split("|"),a=(c=e[3])!=null&&c.trim()?e[3].replace(this.rules.other.tableRowBlankLine,"").split(`
`):[],i={type:"table",raw:e[0],header:[],align:[],rows:[]};if(t.length===s.length){for(const o of s)this.rules.other.tableAlignRight.test(o)?i.align.push("right"):this.rules.other.tableAlignCenter.test(o)?i.align.push("center"):this.rules.other.tableAlignLeft.test(o)?i.align.push("left"):i.align.push(null);for(let o=0;o<t.length;o++)i.header.push({text:t[o],tokens:this.lexer.inline(t[o]),header:!0,align:i.align[o]});for(const o of a)i.rows.push(ye(o,i.header.length).map((h,r)=>({text:h,tokens:this.lexer.inline(h),header:!1,align:i.align[r]})));return i}}lheading(n){const e=this.rules.block.lheading.exec(n);if(e)return{type:"heading",raw:e[0],depth:e[2].charAt(0)==="="?1:2,text:e[1],tokens:this.lexer.inline(e[1])}}paragraph(n){const e=this.rules.block.paragraph.exec(n);if(e){const t=e[1].charAt(e[1].length-1)===`
`?e[1].slice(0,-1):e[1];return{type:"paragraph",raw:e[0],text:t,tokens:this.lexer.inline(t)}}}text(n){const e=this.rules.block.text.exec(n);if(e)return{type:"text",raw:e[0],text:e[0],tokens:this.lexer.inline(e[0])}}escape(n){const e=this.rules.inline.escape.exec(n);if(e)return{type:"escape",raw:e[0],text:e[1]}}tag(n){const e=this.rules.inline.tag.exec(n);if(e)return!this.lexer.state.inLink&&this.rules.other.startATag.test(e[0])?this.lexer.state.inLink=!0:this.lexer.state.inLink&&this.rules.other.endATag.test(e[0])&&(this.lexer.state.inLink=!1),!this.lexer.state.inRawBlock&&this.rules.other.startPreScriptTag.test(e[0])?this.lexer.state.inRawBlock=!0:this.lexer.state.inRawBlock&&this.rules.other.endPreScriptTag.test(e[0])&&(this.lexer.state.inRawBlock=!1),{type:"html",raw:e[0],inLink:this.lexer.state.inLink,inRawBlock:this.lexer.state.inRawBlock,block:!1,text:e[0]}}link(n){const e=this.rules.inline.link.exec(n);if(e){const t=e[2].trim();if(!this.options.pedantic&&this.rules.other.startAngleBracket.test(t)){if(!this.rules.other.endAngleBracket.test(t))return;const i=B(t.slice(0,-1),"\\");if((t.length-i.length)%2===0)return}else{const i=nt(e[2],"()");if(i===-2)return;if(i>-1){const o=(e[0].indexOf("!")===0?5:4)+e[1].length+i;e[2]=e[2].substring(0,i),e[0]=e[0].substring(0,o).trim(),e[3]=""}}let s=e[2],a="";if(this.options.pedantic){const i=this.rules.other.pedanticHrefTitle.exec(s);i&&(s=i[1],a=i[3])}else a=e[3]?e[3].slice(1,-1):"";return s=s.trim(),this.rules.other.startAngleBracket.test(s)&&(this.options.pedantic&&!this.rules.other.endAngleBracket.test(t)?s=s.slice(1):s=s.slice(1,-1)),ke(e,{href:s&&s.replace(this.rules.inline.anyPunctuation,"$1"),title:a&&a.replace(this.rules.inline.anyPunctuation,"$1")},e[0],this.lexer,this.rules)}}reflink(n,e){let t;if((t=this.rules.inline.reflink.exec(n))||(t=this.rules.inline.nolink.exec(n))){const s=(t[2]||t[1]).replace(this.rules.other.multipleSpaceGlobal," "),a=e[s.toLowerCase()];if(!a){const i=t[0].charAt(0);return{type:"text",raw:i,text:i}}return ke(t,a,t[0],this.lexer,this.rules)}}emStrong(n,e,t=""){let s=this.rules.inline.emStrongLDelim.exec(n);if(!s||s[3]&&t.match(this.rules.other.unicodeAlphaNumeric))return;if(!(s[1]||s[2]||"")||!t||this.rules.inline.punctuation.exec(t)){const i=[...s[0]].length-1;let c,o,h=i,r=0;const l=s[0][0]==="*"?this.rules.inline.emStrongRDelimAst:this.rules.inline.emStrongRDelimUnd;for(l.lastIndex=0,e=e.slice(-1*n.length+i);(s=l.exec(e))!=null;){if(c=s[1]||s[2]||s[3]||s[4]||s[5]||s[6],!c)continue;if(o=[...c].length,s[3]||s[4]){h+=o;continue}else if((s[5]||s[6])&&i%3&&!((i+o)%3)){r+=o;continue}if(h-=o,h>0)continue;o=Math.min(o,o+h+r);const f=[...s[0]][0].length,p=n.slice(0,i+s.index+f+o);if(Math.min(i,o)%2){const g=p.slice(1,-1);return{type:"em",raw:p,text:g,tokens:this.lexer.inlineTokens(g)}}const u=p.slice(2,-2);return{type:"strong",raw:p,text:u,tokens:this.lexer.inlineTokens(u)}}}}codespan(n){const e=this.rules.inline.code.exec(n);if(e){let t=e[2].replace(this.rules.other.newLineCharGlobal," ");const s=this.rules.other.nonSpaceChar.test(t),a=this.rules.other.startingSpaceChar.test(t)&&this.rules.other.endingSpaceChar.test(t);return s&&a&&(t=t.substring(1,t.length-1)),{type:"codespan",raw:e[0],text:t}}}br(n){const e=this.rules.inline.br.exec(n);if(e)return{type:"br",raw:e[0]}}del(n){const e=this.rules.inline.del.exec(n);if(e)return{type:"del",raw:e[0],text:e[2],tokens:this.lexer.inlineTokens(e[2])}}autolink(n){const e=this.rules.inline.autolink.exec(n);if(e){let t,s;return e[2]==="@"?(t=e[1],s="mailto:"+t):(t=e[1],s=t),{type:"link",raw:e[0],text:t,href:s,tokens:[{type:"text",raw:t,text:t}]}}}url(n){var t;let e;if(e=this.rules.inline.url.exec(n)){let s,a;if(e[2]==="@")s=e[0],a="mailto:"+s;else{let i;do i=e[0],e[0]=((t=this.rules.inline._backpedal.exec(e[0]))==null?void 0:t[0])??"";while(i!==e[0]);s=e[0],e[1]==="www."?a="http://"+e[0]:a=e[0]}return{type:"link",raw:e[0],text:s,href:a,tokens:[{type:"text",raw:s,text:s}]}}}inlineText(n){const e=this.rules.inline.text.exec(n);if(e){const t=this.lexer.state.inRawBlock;return{type:"text",raw:e[0],text:e[0],escaped:t}}}},E=class ne{constructor(e){b(this,"tokens");b(this,"options");b(this,"state");b(this,"tokenizer");b(this,"inlineQueue");this.tokens=[],this.tokens.links=Object.create(null),this.options=e||L,this.options.tokenizer=this.options.tokenizer||new Z,this.tokenizer=this.options.tokenizer,this.tokenizer.options=this.options,this.tokenizer.lexer=this,this.inlineQueue=[],this.state={inLink:!1,inRawBlock:!1,top:!0};const t={other:x,block:G.normal,inline:j.normal};this.options.pedantic?(t.block=G.pedantic,t.inline=j.pedantic):this.options.gfm&&(t.block=G.gfm,this.options.breaks?t.inline=j.breaks:t.inline=j.gfm),this.tokenizer.rules=t}static get rules(){return{block:G,inline:j}}static lex(e,t){return new ne(t).lex(e)}static lexInline(e,t){return new ne(t).inlineTokens(e)}lex(e){e=e.replace(x.carriageReturn,`
`),this.blockTokens(e,this.tokens);for(let t=0;t<this.inlineQueue.length;t++){const s=this.inlineQueue[t];this.inlineTokens(s.src,s.tokens)}return this.inlineQueue=[],this.tokens}blockTokens(e,t=[],s=!1){var a,i,c;for(this.options.pedantic&&(e=e.replace(x.tabCharGlobal,"    ").replace(x.spaceLine,""));e;){let o;if((i=(a=this.options.extensions)==null?void 0:a.block)!=null&&i.some(r=>(o=r.call({lexer:this},e,t))?(e=e.substring(o.raw.length),t.push(o),!0):!1))continue;if(o=this.tokenizer.space(e)){e=e.substring(o.raw.length);const r=t.at(-1);o.raw.length===1&&r!==void 0?r.raw+=`
`:t.push(o);continue}if(o=this.tokenizer.code(e)){e=e.substring(o.raw.length);const r=t.at(-1);(r==null?void 0:r.type)==="paragraph"||(r==null?void 0:r.type)==="text"?(r.raw+=`
`+o.raw,r.text+=`
`+o.text,this.inlineQueue.at(-1).src=r.text):t.push(o);continue}if(o=this.tokenizer.fences(e)){e=e.substring(o.raw.length),t.push(o);continue}if(o=this.tokenizer.heading(e)){e=e.substring(o.raw.length),t.push(o);continue}if(o=this.tokenizer.hr(e)){e=e.substring(o.raw.length),t.push(o);continue}if(o=this.tokenizer.blockquote(e)){e=e.substring(o.raw.length),t.push(o);continue}if(o=this.tokenizer.list(e)){e=e.substring(o.raw.length),t.push(o);continue}if(o=this.tokenizer.html(e)){e=e.substring(o.raw.length),t.push(o);continue}if(o=this.tokenizer.def(e)){e=e.substring(o.raw.length);const r=t.at(-1);(r==null?void 0:r.type)==="paragraph"||(r==null?void 0:r.type)==="text"?(r.raw+=`
`+o.raw,r.text+=`
`+o.raw,this.inlineQueue.at(-1).src=r.text):this.tokens.links[o.tag]||(this.tokens.links[o.tag]={href:o.href,title:o.title});continue}if(o=this.tokenizer.table(e)){e=e.substring(o.raw.length),t.push(o);continue}if(o=this.tokenizer.lheading(e)){e=e.substring(o.raw.length),t.push(o);continue}let h=e;if((c=this.options.extensions)!=null&&c.startBlock){let r=1/0;const l=e.slice(1);let f;this.options.extensions.startBlock.forEach(p=>{f=p.call({lexer:this},l),typeof f=="number"&&f>=0&&(r=Math.min(r,f))}),r<1/0&&r>=0&&(h=e.substring(0,r+1))}if(this.state.top&&(o=this.tokenizer.paragraph(h))){const r=t.at(-1);s&&(r==null?void 0:r.type)==="paragraph"?(r.raw+=`
`+o.raw,r.text+=`
`+o.text,this.inlineQueue.pop(),this.inlineQueue.at(-1).src=r.text):t.push(o),s=h.length!==e.length,e=e.substring(o.raw.length);continue}if(o=this.tokenizer.text(e)){e=e.substring(o.raw.length);const r=t.at(-1);(r==null?void 0:r.type)==="text"?(r.raw+=`
`+o.raw,r.text+=`
`+o.text,this.inlineQueue.pop(),this.inlineQueue.at(-1).src=r.text):t.push(o);continue}if(e){const r="Infinite loop on byte: "+e.charCodeAt(0);if(this.options.silent){console.error(r);break}else throw new Error(r)}}return this.state.top=!0,t}inline(e,t=[]){return this.inlineQueue.push({src:e,tokens:t}),t}inlineTokens(e,t=[]){var o,h,r;let s=e,a=null;if(this.tokens.links){const l=Object.keys(this.tokens.links);if(l.length>0)for(;(a=this.tokenizer.rules.inline.reflinkSearch.exec(s))!=null;)l.includes(a[0].slice(a[0].lastIndexOf("[")+1,-1))&&(s=s.slice(0,a.index)+"["+"a".repeat(a[0].length-2)+"]"+s.slice(this.tokenizer.rules.inline.reflinkSearch.lastIndex))}for(;(a=this.tokenizer.rules.inline.anyPunctuation.exec(s))!=null;)s=s.slice(0,a.index)+"++"+s.slice(this.tokenizer.rules.inline.anyPunctuation.lastIndex);for(;(a=this.tokenizer.rules.inline.blockSkip.exec(s))!=null;)s=s.slice(0,a.index)+"["+"a".repeat(a[0].length-2)+"]"+s.slice(this.tokenizer.rules.inline.blockSkip.lastIndex);let i=!1,c="";for(;e;){i||(c=""),i=!1;let l;if((h=(o=this.options.extensions)==null?void 0:o.inline)!=null&&h.some(p=>(l=p.call({lexer:this},e,t))?(e=e.substring(l.raw.length),t.push(l),!0):!1))continue;if(l=this.tokenizer.escape(e)){e=e.substring(l.raw.length),t.push(l);continue}if(l=this.tokenizer.tag(e)){e=e.substring(l.raw.length),t.push(l);continue}if(l=this.tokenizer.link(e)){e=e.substring(l.raw.length),t.push(l);continue}if(l=this.tokenizer.reflink(e,this.tokens.links)){e=e.substring(l.raw.length);const p=t.at(-1);l.type==="text"&&(p==null?void 0:p.type)==="text"?(p.raw+=l.raw,p.text+=l.text):t.push(l);continue}if(l=this.tokenizer.emStrong(e,s,c)){e=e.substring(l.raw.length),t.push(l);continue}if(l=this.tokenizer.codespan(e)){e=e.substring(l.raw.length),t.push(l);continue}if(l=this.tokenizer.br(e)){e=e.substring(l.raw.length),t.push(l);continue}if(l=this.tokenizer.del(e)){e=e.substring(l.raw.length),t.push(l);continue}if(l=this.tokenizer.autolink(e)){e=e.substring(l.raw.length),t.push(l);continue}if(!this.state.inLink&&(l=this.tokenizer.url(e))){e=e.substring(l.raw.length),t.push(l);continue}let f=e;if((r=this.options.extensions)!=null&&r.startInline){let p=1/0;const u=e.slice(1);let g;this.options.extensions.startInline.forEach(_=>{g=_.call({lexer:this},u),typeof g=="number"&&g>=0&&(p=Math.min(p,g))}),p<1/0&&p>=0&&(f=e.substring(0,p+1))}if(l=this.tokenizer.inlineText(f)){e=e.substring(l.raw.length),l.raw.slice(-1)!=="_"&&(c=l.raw.slice(-1)),i=!0;const p=t.at(-1);(p==null?void 0:p.type)==="text"?(p.raw+=l.raw,p.text+=l.text):t.push(l);continue}if(e){const p="Infinite loop on byte: "+e.charCodeAt(0);if(this.options.silent){console.error(p);break}else throw new Error(p)}}return t}},V=class{constructor(n){b(this,"options");b(this,"parser");this.options=n||L}space(n){return""}code({text:n,lang:e,escaped:t}){var i;const s=(i=(e||"").match(x.notSpaceStart))==null?void 0:i[0],a=n.replace(x.endingNewline,"")+`
`;return s?'<pre><code class="language-'+R(s)+'">'+(t?a:R(a,!0))+`</code></pre>
`:"<pre><code>"+(t?a:R(a,!0))+`</code></pre>
`}blockquote({tokens:n}){return`<blockquote>
${this.parser.parse(n)}</blockquote>
`}html({text:n}){return n}heading({tokens:n,depth:e}){return`<h${e}>${this.parser.parseInline(n)}</h${e}>
`}hr(n){return`<hr>
`}list(n){const e=n.ordered,t=n.start;let s="";for(let c=0;c<n.items.length;c++){const o=n.items[c];s+=this.listitem(o)}const a=e?"ol":"ul",i=e&&t!==1?' start="'+t+'"':"";return"<"+a+i+`>
`+s+"</"+a+`>
`}listitem(n){var t;let e="";if(n.task){const s=this.checkbox({checked:!!n.checked});n.loose?((t=n.tokens[0])==null?void 0:t.type)==="paragraph"?(n.tokens[0].text=s+" "+n.tokens[0].text,n.tokens[0].tokens&&n.tokens[0].tokens.length>0&&n.tokens[0].tokens[0].type==="text"&&(n.tokens[0].tokens[0].text=s+" "+R(n.tokens[0].tokens[0].text),n.tokens[0].tokens[0].escaped=!0)):n.tokens.unshift({type:"text",raw:s+" ",text:s+" ",escaped:!0}):e+=s+" "}return e+=this.parser.parse(n.tokens,!!n.loose),`<li>${e}</li>
`}checkbox({checked:n}){return"<input "+(n?'checked="" ':"")+'disabled="" type="checkbox">'}paragraph({tokens:n}){return`<p>${this.parser.parseInline(n)}</p>
`}table(n){let e="",t="";for(let a=0;a<n.header.length;a++)t+=this.tablecell(n.header[a]);e+=this.tablerow({text:t});let s="";for(let a=0;a<n.rows.length;a++){const i=n.rows[a];t="";for(let c=0;c<i.length;c++)t+=this.tablecell(i[c]);s+=this.tablerow({text:t})}return s&&(s=`<tbody>${s}</tbody>`),`<table>
<thead>
`+e+`</thead>
`+s+`</table>
`}tablerow({text:n}){return`<tr>
${n}</tr>
`}tablecell(n){const e=this.parser.parseInline(n.tokens),t=n.header?"th":"td";return(n.align?`<${t} align="${n.align}">`:`<${t}>`)+e+`</${t}>
`}strong({tokens:n}){return`<strong>${this.parser.parseInline(n)}</strong>`}em({tokens:n}){return`<em>${this.parser.parseInline(n)}</em>`}codespan({text:n}){return`<code>${R(n,!0)}</code>`}br(n){return"<br>"}del({tokens:n}){return`<del>${this.parser.parseInline(n)}</del>`}link({href:n,title:e,tokens:t}){const s=this.parser.parseInline(t),a=fe(n);if(a===null)return s;n=a;let i='<a href="'+n+'"';return e&&(i+=' title="'+R(e)+'"'),i+=">"+s+"</a>",i}image({href:n,title:e,text:t,tokens:s}){s&&(t=this.parser.parseInline(s,this.parser.textRenderer));const a=fe(n);if(a===null)return R(t);n=a;let i=`<img src="${n}" alt="${t}"`;return e&&(i+=` title="${R(e)}"`),i+=">",i}text(n){return"tokens"in n&&n.tokens?this.parser.parseInline(n.tokens):"escaped"in n&&n.escaped?n.text:R(n.text)}},de=class{strong({text:n}){return n}em({text:n}){return n}codespan({text:n}){return n}del({text:n}){return n}html({text:n}){return n}text({text:n}){return n}link({text:n}){return""+n}image({text:n}){return""+n}br(){return""}},I=class te{constructor(e){b(this,"options");b(this,"renderer");b(this,"textRenderer");this.options=e||L,this.options.renderer=this.options.renderer||new V,this.renderer=this.options.renderer,this.renderer.options=this.options,this.renderer.parser=this,this.textRenderer=new de}static parse(e,t){return new te(t).parse(e)}static parseInline(e,t){return new te(t).parseInline(e)}parse(e,t=!0){var a,i;let s="";for(let c=0;c<e.length;c++){const o=e[c];if((i=(a=this.options.extensions)==null?void 0:a.renderers)!=null&&i[o.type]){const r=o,l=this.options.extensions.renderers[r.type].call({parser:this},r);if(l!==!1||!["space","hr","heading","code","table","blockquote","list","html","paragraph","text"].includes(r.type)){s+=l||"";continue}}const h=o;switch(h.type){case"space":{s+=this.renderer.space(h);continue}case"hr":{s+=this.renderer.hr(h);continue}case"heading":{s+=this.renderer.heading(h);continue}case"code":{s+=this.renderer.code(h);continue}case"table":{s+=this.renderer.table(h);continue}case"blockquote":{s+=this.renderer.blockquote(h);continue}case"list":{s+=this.renderer.list(h);continue}case"html":{s+=this.renderer.html(h);continue}case"paragraph":{s+=this.renderer.paragraph(h);continue}case"text":{let r=h,l=this.renderer.text(r);for(;c+1<e.length&&e[c+1].type==="text";)r=e[++c],l+=`
`+this.renderer.text(r);t?s+=this.renderer.paragraph({type:"paragraph",raw:l,text:l,tokens:[{type:"text",raw:l,text:l,escaped:!0}]}):s+=l;continue}default:{const r='Token with "'+h.type+'" type was not found.';if(this.options.silent)return console.error(r),"";throw new Error(r)}}}return s}parseInline(e,t=this.renderer){var a,i;let s="";for(let c=0;c<e.length;c++){const o=e[c];if((i=(a=this.options.extensions)==null?void 0:a.renderers)!=null&&i[o.type]){const r=this.options.extensions.renderers[o.type].call({parser:this},o);if(r!==!1||!["escape","html","link","image","strong","em","codespan","br","del","text"].includes(o.type)){s+=r||"";continue}}const h=o;switch(h.type){case"escape":{s+=t.text(h);break}case"html":{s+=t.html(h);break}case"link":{s+=t.link(h);break}case"image":{s+=t.image(h);break}case"strong":{s+=t.strong(h);break}case"em":{s+=t.em(h);break}case"codespan":{s+=t.codespan(h);break}case"br":{s+=t.br(h);break}case"del":{s+=t.del(h);break}case"text":{s+=t.text(h);break}default:{const r='Token with "'+h.type+'" type was not found.';if(this.options.silent)return console.error(r),"";throw new Error(r)}}}return s}},J,K=(J=class{constructor(n){b(this,"options");b(this,"block");this.options=n||L}preprocess(n){return n}postprocess(n){return n}processAllTokens(n){return n}provideLexer(){return this.block?E.lex:E.lexInline}provideParser(){return this.block?I.parse:I.parseInline}},b(J,"passThroughHooks",new Set(["preprocess","postprocess","processAllTokens"])),J),st=class{constructor(...n){b(this,"defaults",se());b(this,"options",this.setOptions);b(this,"parse",this.parseMarkdown(!0));b(this,"parseInline",this.parseMarkdown(!1));b(this,"Parser",I);b(this,"Renderer",V);b(this,"TextRenderer",de);b(this,"Lexer",E);b(this,"Tokenizer",Z);b(this,"Hooks",K);this.use(...n)}walkTokens(n,e){var s,a;let t=[];for(const i of n)switch(t=t.concat(e.call(this,i)),i.type){case"table":{const c=i;for(const o of c.header)t=t.concat(this.walkTokens(o.tokens,e));for(const o of c.rows)for(const h of o)t=t.concat(this.walkTokens(h.tokens,e));break}case"list":{const c=i;t=t.concat(this.walkTokens(c.items,e));break}default:{const c=i;(a=(s=this.defaults.extensions)==null?void 0:s.childTokens)!=null&&a[c.type]?this.defaults.extensions.childTokens[c.type].forEach(o=>{const h=c[o].flat(1/0);t=t.concat(this.walkTokens(h,e))}):c.tokens&&(t=t.concat(this.walkTokens(c.tokens,e)))}}return t}use(...n){const e=this.defaults.extensions||{renderers:{},childTokens:{}};return n.forEach(t=>{const s={...t};if(s.async=this.defaults.async||s.async||!1,t.extensions&&(t.extensions.forEach(a=>{if(!a.name)throw new Error("extension name required");if("renderer"in a){const i=e.renderers[a.name];i?e.renderers[a.name]=function(...c){let o=a.renderer.apply(this,c);return o===!1&&(o=i.apply(this,c)),o}:e.renderers[a.name]=a.renderer}if("tokenizer"in a){if(!a.level||a.level!=="block"&&a.level!=="inline")throw new Error("extension level must be 'block' or 'inline'");const i=e[a.level];i?i.unshift(a.tokenizer):e[a.level]=[a.tokenizer],a.start&&(a.level==="block"?e.startBlock?e.startBlock.push(a.start):e.startBlock=[a.start]:a.level==="inline"&&(e.startInline?e.startInline.push(a.start):e.startInline=[a.start]))}"childTokens"in a&&a.childTokens&&(e.childTokens[a.name]=a.childTokens)}),s.extensions=e),t.renderer){const a=this.defaults.renderer||new V(this.defaults);for(const i in t.renderer){if(!(i in a))throw new Error(`renderer '${i}' does not exist`);if(["options","parser"].includes(i))continue;const c=i,o=t.renderer[c],h=a[c];a[c]=(...r)=>{let l=o.apply(a,r);return l===!1&&(l=h.apply(a,r)),l||""}}s.renderer=a}if(t.tokenizer){const a=this.defaults.tokenizer||new Z(this.defaults);for(const i in t.tokenizer){if(!(i in a))throw new Error(`tokenizer '${i}' does not exist`);if(["options","rules","lexer"].includes(i))continue;const c=i,o=t.tokenizer[c],h=a[c];a[c]=(...r)=>{let l=o.apply(a,r);return l===!1&&(l=h.apply(a,r)),l}}s.tokenizer=a}if(t.hooks){const a=this.defaults.hooks||new K;for(const i in t.hooks){if(!(i in a))throw new Error(`hook '${i}' does not exist`);if(["options","block"].includes(i))continue;const c=i,o=t.hooks[c],h=a[c];K.passThroughHooks.has(i)?a[c]=r=>{if(this.defaults.async)return Promise.resolve(o.call(a,r)).then(f=>h.call(a,f));const l=o.call(a,r);return h.call(a,l)}:a[c]=(...r)=>{let l=o.apply(a,r);return l===!1&&(l=h.apply(a,r)),l}}s.hooks=a}if(t.walkTokens){const a=this.defaults.walkTokens,i=t.walkTokens;s.walkTokens=function(c){let o=[];return o.push(i.call(this,c)),a&&(o=o.concat(a.call(this,c))),o}}this.defaults={...this.defaults,...s}}),this}setOptions(n){return this.defaults={...this.defaults,...n},this}lexer(n,e){return E.lex(n,e??this.defaults)}parser(n,e){return I.parse(n,e??this.defaults)}parseMarkdown(n){return(t,s)=>{const a={...s},i={...this.defaults,...a},c=this.onError(!!i.silent,!!i.async);if(this.defaults.async===!0&&a.async===!1)return c(new Error("marked(): The async option was set to true by an extension. Remove async: false from the parse options object to return a Promise."));if(typeof t>"u"||t===null)return c(new Error("marked(): input parameter is undefined or null"));if(typeof t!="string")return c(new Error("marked(): input parameter is of type "+Object.prototype.toString.call(t)+", string expected"));i.hooks&&(i.hooks.options=i,i.hooks.block=n);const o=i.hooks?i.hooks.provideLexer():n?E.lex:E.lexInline,h=i.hooks?i.hooks.provideParser():n?I.parse:I.parseInline;if(i.async)return Promise.resolve(i.hooks?i.hooks.preprocess(t):t).then(r=>o(r,i)).then(r=>i.hooks?i.hooks.processAllTokens(r):r).then(r=>i.walkTokens?Promise.all(this.walkTokens(r,i.walkTokens)).then(()=>r):r).then(r=>h(r,i)).then(r=>i.hooks?i.hooks.postprocess(r):r).catch(c);try{i.hooks&&(t=i.hooks.preprocess(t));let r=o(t,i);i.hooks&&(r=i.hooks.processAllTokens(r)),i.walkTokens&&this.walkTokens(r,i.walkTokens);let l=h(r,i);return i.hooks&&(l=i.hooks.postprocess(l)),l}catch(r){return c(r)}}}onError(n,e){return t=>{if(t.message+=`
Please report this to https://github.com/markedjs/marked.`,n){const s="<p>An error occurred:</p><pre>"+R(t.message+"",!0)+"</pre>";return e?Promise.resolve(s):s}if(e)return Promise.reject(t);throw t}}},N=new st;function y(n,e){return N.parse(n,e)}y.options=y.setOptions=function(n){return N.setOptions(n),y.defaults=N.defaults,we(y.defaults),y};y.getDefaults=se;y.defaults=L;y.use=function(...n){return N.use(...n),y.defaults=N.defaults,we(y.defaults),y};y.walkTokens=function(n,e){return N.walkTokens(n,e)};y.parseInline=N.parseInline;y.Parser=I;y.parser=I.parse;y.Renderer=V;y.TextRenderer=de;y.Lexer=E;y.lexer=E.lex;y.Tokenizer=Z;y.Hooks=K;y.parse=y;y.options;y.setOptions;y.use;y.walkTokens;y.parseInline;I.parse;E.lex;const Ue=[{zh:"开始",en:"Get started",pages:[["overview","概览","Overview"],["quickstart","快速开始","Quick start"],["install","安装","Installation"],["setup","首次配置","First-run setup"]]},{zh:"使用",en:"Using TRUSTA",pages:[["chat-cards","对话与结果卡片","Conversations and cards"],["authorization","授权等级","Authorisation tiers"],["feishu","飞书接入","Feishu"],["mobile","手机访问","Phone access"]]},{zh:"可信机制",en:"Trust",pages:[["agent-communication","多 Agent 签名通信","Signed agent messaging"],["evidence","证据包离线验证","Verifying evidence offline"],["cross-institution","跨机构协作","Cross-institution collaboration"],["benchmarks","安全基准","Security benchmarks"]]},{zh:"参考",en:"Reference",pages:[["configuration","配置项","Configuration"],["cli","命令行","Command line"],["api","HTTP API","HTTP API"],["faq","常见问题","FAQ"],["changelog","更新日志","Changelog"]]}],z=Ue.flatMap(n=>n.pages.map(([e,t,s])=>({slug:e,zh:t,en:s,group:n}))),be=Object.assign({"./content/en/agent-communication.md":je,"./content/en/api.md":Be,"./content/en/authorization.md":He,"./content/en/benchmarks.md":We,"./content/en/changelog.md":qe,"./content/en/chat-cards.md":Ge,"./content/en/cli.md":Ke,"./content/en/configuration.md":Ye,"./content/en/cross-institution.md":Ze,"./content/en/evidence.md":Ve,"./content/en/faq.md":Qe,"./content/en/feishu.md":Xe,"./content/en/install.md":Je,"./content/en/mobile.md":en,"./content/en/overview.md":nn,"./content/en/quickstart.md":tn,"./content/en/setup.md":sn,"./content/zh/agent-communication.md":an,"./content/zh/api.md":on,"./content/zh/authorization.md":rn,"./content/zh/benchmarks.md":ln,"./content/zh/changelog.md":cn,"./content/zh/chat-cards.md":hn,"./content/zh/cli.md":dn,"./content/zh/configuration.md":pn,"./content/zh/cross-institution.md":un,"./content/zh/evidence.md":gn,"./content/zh/faq.md":mn,"./content/zh/feishu.md":fn,"./content/zh/install.md":yn,"./content/zh/mobile.md":kn,"./content/zh/overview.md":bn,"./content/zh/quickstart.md":Tn,"./content/zh/setup.md":wn}),Ne=(n,e)=>be[`./content/${n}/${e}.md`]??be[`./content/zh/${e}.md`]??"";y.setOptions({gfm:!0,breaks:!1});const at={zh:{docs:"文档",search:"搜索文档",searchHint:"输入关键词",noResults:"没有找到相关内容",onPage:"本页目录",prev:"上一页",next:"下一页",edit:"在 GitHub 上编辑此页",menu:"目录",home:"首页",lang:"EN",langLabel:"Switch to English",theme:"切换深浅色",copy:"复制",copied:"已复制",top:"回到顶部"},en:{docs:"Docs",search:"Search docs",searchHint:"Type to search",noResults:"Nothing found",onPage:"On this page",prev:"Previous",next:"Next",edit:"Edit this page on GitHub",menu:"Menu",home:"Home",lang:"中文",langLabel:"切换到中文",theme:"Toggle theme",copy:"Copy",copied:"Copied",top:"Back to top"}};function Te(){const n=location.hash.match(/^#\/([a-z0-9-]+)/);return n&&z.some(e=>e.slug===n[1])?n[1]:"overview"}function it(){try{const n=localStorage.getItem("trusta-docs-theme");if(n==="light"||n==="dark")return n}catch{}return window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}function ot(n){return n.replace(/```[\s\S]*?```/g," ").replace(/`([^`]*)`/g,"$1").replace(/\[([^\]]*)\]\([^)]*\)/g,"$1").replace(/[#>*_|-]+/g," ").replace(/\s+/g," ").trim()}function rt(n){const e=[];for(const t of z)Ne(n,t.slug).split(/^(?=#{2,3} )/m).forEach((i,c)=>{var h;const o=c===0?null:(h=i.match(/^#{2,3} (.+)$/m))==null?void 0:h[1];e.push({slug:t.slug,page:t[n],head:o,n:c,text:ot(c===0?i.replace(/^# .+$/m,""):i.replace(/^#{2,3} .+$/m,""))})});return e}function lt(n,e){const t=e.toLowerCase().split(/\s+/).filter(Boolean);if(!t.length)return[];const s=[];for(const a of n){const i=`${a.page} ${a.head||""} ${a.text}`.toLowerCase();if(!t.every(r=>i.includes(r)))continue;let c=0;for(const r of t)a.page.toLowerCase().includes(r)&&(c+=6),(a.head||"").toLowerCase().includes(r)&&(c+=4);const o=a.text.toLowerCase().indexOf(t[0]),h=o<0?a.text.slice(0,90):(o>30?"…":"")+a.text.slice(Math.max(0,o-30),o+70)+"…";s.push({...a,score:c,snippet:h})}return s.sort((a,i)=>i.score-a.score).slice(0,12)}function ct({ui:n,lang:e,onClose:t,go:s}){const[a,i]=T.useState(""),[c,o]=T.useState(0),h=T.useMemo(()=>rt(e),[e]),r=T.useMemo(()=>lt(h,a),[h,a]),l=T.useRef(null);T.useEffect(()=>{var u;(u=l.current)==null||u.focus()},[]),T.useEffect(()=>o(0),[a]);const f=u=>{s(u.slug,u.n),t()},p=u=>{u.key==="Escape"&&t(),u.key==="ArrowDown"&&(u.preventDefault(),o(g=>Math.min(r.length-1,g+1))),u.key==="ArrowUp"&&(u.preventDefault(),o(g=>Math.max(0,g-1))),u.key==="Enter"&&r[c]&&f(r[c])};return d.jsx("div",{className:"search-backdrop",onMouseDown:u=>{u.target===u.currentTarget&&t()},children:d.jsxs("div",{className:"search",role:"dialog","aria-modal":"true","aria-label":n.search,children:[d.jsx("input",{ref:l,value:a,onChange:u=>i(u.target.value),onKeyDown:p,placeholder:n.searchHint,"aria-label":n.search,"aria-controls":"search-results","aria-activedescendant":r[c]?`sr-${c}`:void 0}),d.jsxs("ul",{id:"search-results",role:"listbox",children:[a&&!r.length&&d.jsx("li",{className:"empty",children:n.noResults}),r.map((u,g)=>d.jsxs("li",{id:`sr-${g}`,role:"option","aria-selected":g===c,onMouseEnter:()=>o(g),onClick:()=>f(u),children:[d.jsxs("b",{children:[u.page,u.head?d.jsxs("span",{children:[" · ",u.head]}):null]}),d.jsx("small",{children:u.snippet})]},`${u.slug}-${u.n}`))]})]})})}function ht(){const[n,e]=Fe(),t=at[n],[s,a]=T.useState(Te),[i,c]=T.useState(it),[o,h]=T.useState([]),[r,l]=T.useState(null),[f,p]=T.useState(!1),[u,g]=T.useState(!1),_=T.useRef(null),O=T.useRef(null),C=z.find(m=>m.slug===s),F=z.indexOf(C),D=z[F-1],M=z[F+1],q=T.useMemo(()=>y.parse(Ne(n,s)),[n,s]);T.useEffect(()=>{const m=()=>{a(Te()),g(!1)};return window.addEventListener("hashchange",m),()=>window.removeEventListener("hashchange",m)},[]),T.useEffect(()=>{var m;document.documentElement.dataset.theme=i,(m=document.querySelector('meta[name="theme-color"]'))==null||m.setAttribute("content",i==="dark"?"#111110":"#edece8")},[i]),T.useEffect(()=>{document.title=`${C[n]} · TRUSTA ${t.docs}`},[C,n,t.docs]),T.useEffect(()=>{const m=w=>{var v;(w.key==="k"&&(w.metaKey||w.ctrlKey)||w.key==="/"&&!/input|textarea/i.test(((v=document.activeElement)==null?void 0:v.tagName)||""))&&(w.preventDefault(),p(!0))};return window.addEventListener("keydown",m),()=>window.removeEventListener("keydown",m)},[]),T.useLayoutEffect(()=>{var U;const m=_.current;if(!m)return;const w=[...m.querySelectorAll("h2, h3")];w.forEach((S,A)=>{S.id=`s-${A+1}`}),h(w.map(S=>({id:S.id,text:S.textContent,level:S.tagName==="H2"?2:3}))),m.querySelectorAll("pre").forEach(S=>{if(S.querySelector(".copy"))return;const A=document.createElement("button");A.type="button",A.className="copy",A.textContent=t.copy,A.addEventListener("click",async()=>{var pe;const Oe=((pe=S.querySelector("code"))==null?void 0:pe.innerText)??S.innerText;try{await navigator.clipboard.writeText(Oe)}catch{}A.textContent=t.copied,setTimeout(()=>{A.textContent=t.copy},1400)}),S.append(A)}),m.querySelectorAll("table").forEach(S=>{if(S.parentElement.classList.contains("table-wrap"))return;const A=document.createElement("div");A.className="table-wrap",S.replaceWith(A),A.append(S)}),m.querySelectorAll("a[href^='http']").forEach(S=>{S.target="_blank",S.rel="noopener noreferrer"});const v=O.current;O.current=null,v?(U=document.getElementById(`s-${v}`))==null||U.scrollIntoView({block:"start"}):window.scrollTo(0,0)},[q,t.copy,t.copied]),T.useEffect(()=>{if(!o.length)return;const m=o.map(v=>document.getElementById(v.id)).filter(Boolean),w=new IntersectionObserver(v=>{const U=v.filter(S=>S.isIntersecting).sort((S,A)=>S.boundingClientRect.top-A.boundingClientRect.top);U[0]&&l(U[0].target.id)},{rootMargin:"-72px 0px -70% 0px"});return m.forEach(v=>w.observe(v)),()=>w.disconnect()},[o]);const $=(m,w)=>{var v;if(O.current=w||null,m===s){w&&((v=document.getElementById(`s-${w}`))==null||v.scrollIntoView({behavior:"smooth",block:"start"}));return}location.hash=`#/${m}`},P=()=>{const m=i==="dark"?"light":"dark";c(m);try{localStorage.setItem("trusta-docs-theme",m)}catch{}},Le=`${ue}/edit/${$e}/frontend-app/src/docs/content/${n}/${s}.md`;return d.jsxs("div",{className:"docs",children:[d.jsxs("header",{className:"topbar",children:[d.jsxs("button",{type:"button",className:"menu-btn",onClick:()=>g(m=>!m),"aria-expanded":u,"aria-controls":"sidebar","aria-label":t.menu,children:[d.jsx("span",{}),d.jsx("span",{}),d.jsx("span",{})]}),d.jsxs("a",{className:"brand",href:"/intro.html",children:[d.jsx("img",{src:"/mark.png",alt:"",width:"24",height:"24"}),d.jsx("span",{children:"TRUSTA"})]}),d.jsx("span",{className:"crumb",children:t.docs}),d.jsxs("button",{type:"button",className:"search-btn",onClick:()=>p(!0),children:[d.jsx("svg",{viewBox:"0 0 20 20",width:"15",height:"15","aria-hidden":"true",children:d.jsx("path",{fill:"none",stroke:"currentColor",strokeWidth:"1.8",d:"M8.5 15a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13Zm9 3-4.5-4.5"})}),d.jsx("span",{children:t.search}),d.jsx("kbd",{children:/mac|iphone|ipad/i.test(navigator.platform||navigator.userAgent)?"⌘K":"Ctrl K"})]}),d.jsxs("nav",{className:"top-links",children:[d.jsx("a",{className:"wide",href:"/intro.html",children:t.home}),d.jsx("a",{className:"icon",href:ue,"aria-label":"GitHub",children:d.jsx("svg",{viewBox:"0 0 16 16",width:"18",height:"18","aria-hidden":"true",children:d.jsx("path",{fill:"currentColor",d:"M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"})})}),d.jsx("button",{type:"button",className:"lang",onClick:()=>e(n==="zh"?"en":"zh"),"aria-label":t.langLabel,children:t.lang}),d.jsx("button",{type:"button",className:"theme",onClick:P,"aria-label":t.theme,children:i==="dark"?d.jsxs("svg",{viewBox:"0 0 20 20",width:"17",height:"17","aria-hidden":"true",children:[d.jsx("circle",{cx:"10",cy:"10",r:"4",fill:"currentColor"}),d.jsx("path",{stroke:"currentColor",strokeWidth:"1.6",strokeLinecap:"round",d:"M10 1.5v2M10 16.5v2M1.5 10h2M16.5 10h2M4 4l1.4 1.4M14.6 14.6 16 16M4 16l1.4-1.4M14.6 5.4 16 4"})]}):d.jsx("svg",{viewBox:"0 0 20 20",width:"17",height:"17","aria-hidden":"true",children:d.jsx("path",{fill:"currentColor",d:"M16.5 12.6A7 7 0 0 1 7.4 3.5a7 7 0 1 0 9.1 9.1Z"})})})]})]}),d.jsxs("div",{className:"layout",children:[d.jsx("aside",{id:"sidebar",className:`sidebar ${u?"open":""}`,children:Ue.map(m=>d.jsxs("div",{className:"group",children:[d.jsx("p",{children:m[n]}),d.jsx("ul",{children:m.pages.map(([w,v,U])=>d.jsx("li",{children:d.jsx("a",{href:`#/${w}`,"aria-current":w===s?"page":void 0,children:n==="zh"?v:U})},w))})]},m.en))}),u&&d.jsx("div",{className:"scrim",onClick:()=>g(!1),"aria-hidden":"true"}),d.jsxs("main",{className:"content",children:[d.jsx("p",{className:"eyebrow",children:C.group[n]}),d.jsx("article",{ref:_,className:"prose",dangerouslySetInnerHTML:{__html:q}}),d.jsxs("div",{className:"page-foot",children:[d.jsxs("a",{className:"edit",href:Le,target:"_blank",rel:"noopener noreferrer",children:[t.edit," ↗"]}),d.jsxs("nav",{className:"pager",children:[D?d.jsxs("a",{href:`#/${D.slug}`,className:"prev",children:[d.jsx("small",{children:t.prev}),d.jsx("b",{children:D[n]})]}):d.jsx("span",{}),M?d.jsxs("a",{href:`#/${M.slug}`,className:"next",children:[d.jsx("small",{children:t.next}),d.jsx("b",{children:M[n]})]}):d.jsx("span",{})]})]})]}),d.jsx("aside",{className:"toc","aria-label":t.onPage,children:o.length>1&&d.jsxs(d.Fragment,{children:[d.jsx("p",{children:t.onPage}),d.jsx("ul",{children:o.map(m=>d.jsx("li",{className:`l${m.level} ${r===m.id?"on":""}`,children:d.jsx("a",{href:`#/${s}`,onClick:w=>{var v;w.preventDefault(),(v=document.getElementById(m.id))==null||v.scrollIntoView({behavior:"smooth",block:"start"})},children:m.text})},m.id))}),d.jsxs("button",{type:"button",className:"to-top",onClick:()=>window.scrollTo({top:0,behavior:"smooth"}),children:[t.top," ↑"]})]})})]}),f&&d.jsx(ct,{ui:t,lang:n,onClose:()=>p(!1),go:$})]})}try{const n=localStorage.getItem("trusta-docs-theme");document.documentElement.dataset.theme=n==="light"||n==="dark"?n:matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}catch{}ze(document.getElementById("root")).render(d.jsx(T.StrictMode,{children:d.jsx(ht,{})}));
