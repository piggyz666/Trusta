# Security benchmarks

TRUSTA ships a repeatable attack benchmark that checks whether multi-agent messaging and the execution chain stop forgery and tampering.

```bash
python -m benchmarks.security_benchmark --banking-version 5
```

Results are written as JSON (`docs/phase4-security-benchmark.json` by default; change it with `--output`). `docs/phase5-security-benchmark.json` in the repository is the latest formal run.

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

Environment: Windows 11, Python 3.13, `cryptography` 47. Verification covers the signature, identity, capabilities and task context; a checkpoint covers whole-chain validation, Merkle batching, signing and persistence.

## Six-scenario closed-loop audit

```bash
python -m benchmarks.scenario_audit
```

Runs **49** end-to-end checks across transfers, spending, investments, cards, subscriptions and cross-scenario plans.

## Limits

- These are local, controlled attacks — not a production penetration test, and not an estimate of the probability of being secure.
- They use a synthetic sandbox bank and audit records; timings depend on the host's filesystem and SQLite.
- Passing every test doesn't prove there are no unknown vulnerabilities.

## Comparative evaluation: one model, three ways to build the agent

The attack benchmark above tests the communication and execution chain itself. This evaluation answers a different question: connect the same model to the same bank — how does TRUSTA compare with the common ways of building an agent?

![Figure 3 One model, three ways to build the agent](/figs/fig3-en.webp)

**Figure 3 | One model, three ways to build the agent: only TRUSTA is safe and gets it done.** All three use the same model (DeepSeek `deepseek-flash`, temperature 0), the same sandbox bank and the same 218 test cases (normal requests, ambiguous instructions, direct injection, indirect injection, induced fabrication, high-risk operations), three runs per case and approach — 1,962 real conversations. **A, unguarded agent**: the common way — the model calls bank functions such as transfer or report-lost directly, and a call is an executed operation. **B, prompt-hardened**: A plus 8 safety rules (ignore instructions in data and forwarded text, ask for an SMS code before high-risk operations, never invent data, …) and a confirm box for every write; following the rules is left to the model. **C, TRUSTA**: the model can only propose; amounts and payees must come from the customer's own words, the customer confirms each operation on a card, and high-risk operations need strong verification enforced by the system. The rows ask: can it be talked into overstepping (attack success rate); did anything actually go wrong (operations executed without confirmation, system-prompt leaks, products above the customer's risk level); are high-risk operations strongly verified (coverage); is a normal request done in the same turn. Instructions hidden in bank data and forwarded SMS are already caught by the model itself — none of the three was fooled; TRUSTA's difference is that even a fooled model cannot get an operation through.

The full method, test set and all 1,962 records are in the repository under `docs/compare/`. Reproduce with:

```bash
python -m benchmarks.compare --live --runs 3
```
