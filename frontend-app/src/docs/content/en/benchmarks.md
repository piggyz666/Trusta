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

**Figure 3 | Same DeepSeek model, same bank, same 218 test cases — only the way the agent is built changes.** A lets the model call the bank directly; B adds safety prompts and relies on the model to follow them; in TRUSTA the model can only propose, and the system does the confirming and verifying. Only TRUSTA is both safe and gets things done on the spot.

The full method, test set and all 1,962 records are in the repository under `docs/compare/`. Reproduce with:

```bash
python -m benchmarks.compare --live --runs 3
```
