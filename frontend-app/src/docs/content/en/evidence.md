# Verifying evidence offline

Every TRUSTA result card can export a **signed evidence bundle**. Whoever holds it can check — without trusting the server, and without contacting the bank or the model — that the hand-offs between agents for that request really happened, completely and in order.

## Export

1. Turn on Developer mode under 设置 → 外观 (Settings → Appearance).
2. On a result card, open “查看决策过程” (View decision process) and click **下载证据包** (Download evidence bundle) to get `trusta-evidence-xxxxxxxx.json`.

## Verify

```bash
python -m scripts.verify_handoff_evidence trusta-evidence-xxxxxxxx.json --trusted-registry registry.json
```

On success it prints:

```json
{"verified": true, "scope": "RUNTIME_CAUSAL_ATTESTATION_NOT_BANK_AUTHORIZATION"}
```

and exits with 0. On failure `verified` is `false` and the exit code is 1. Changing any byte of the bundle makes verification fail.

The repository includes a sample you can try right away:

```bash
python -m scripts.verify_handoff_evidence docs/agent-communication-demo.json --trusted-registry docs/agent-communication-trusted-registry.json
```

## What it checks

- The signature on every piece of hand-off evidence, and the signer's identity and key in the registry.
- The causal parent links between pieces of evidence: unknown or missing parents, duplicates, reordering and cross-task mixing are rejected.
- That identities, capability commitments and key ownership weren't swapped.
- The Merkle inclusion proofs of the audit records.

## What it doesn't check

- As `scope` says, this is a **runtime causal attestation**, not a bank authorisation. Authorisation for money operations comes from your decision on the confirmation card.
- Hidden payloads aren't exported; matching input and output hashes don't prove the content was semantically right.

## Where the registry comes from

`--trusted-registry` is the registry of agent public keys and capabilities. Verifiers should **obtain and pin it independently** (for example, fetch it once from a trusted channel and keep it), never read it from the bundle itself. A historical snapshot only describes that moment; it doesn't prove a key hasn't been revoked since.
