# Signed agent messaging

The worst case for a multi-agent system is an agent that gets prompt-injected or compromised, impersonates another, alters messages or proposes things beyond its role — and afterwards nobody can say what happened. TRUSTA makes every message between agents **verifiable, traceable and non-repudiable**.

![Figure 1: the AI can only propose; every step between agents is signed and verifiable](/figs/fig1-en.webp)

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

```bash
python -m benchmarks.security_benchmark --banking-version 5
```

See [Security benchmarks](#/benchmarks).

## Managing keys

```bash
python -m scripts.manage_crypto_identity --agent transaction --rotate          # rotate an agent's key
python -m scripts.manage_crypto_identity --agent transaction --status SUSPENDED # suspend an identity
python -m scripts.manage_crypto_identity --revoke-key KEY_ID                    # revoke a key
```

After rotation the old key has a grace period (300 seconds by default, `--grace-seconds`); after that it's rejected.
