# Cross-institution collaboration: WeID · ZK admission · on-chain commitments

When agents from several institutions need to work on something together — say, banks' agents cross-checking the same risk conclusion — local signatures aren't enough. How does the other side know who you are, whether you're qualified to take part, and whether the final conclusion was altered? On top of [signed agent messaging](#/agent-communication), TRUSTA adds a trust layer that can be checked publicly on a consortium chain.

![Figure 2: identity, qualification and conclusion, all checkable on chain](/figs/fig2-en.webp)

## The parts

### On-chain identity: WeID

Each collaborating agent registers a **WeID** (WeBank's open-source decentralised identity) on a **FISCO BCOS** consortium chain, and its P-256 message key is bound to that WeID. Identities can be registered, resolved, deactivated and restored. Collaboration rounds accept only on-chain WeIDs; if the chain is unavailable they refuse outright rather than falling back to a locally self-signed `did:key`.

### M1: zero-knowledge capability admission

An agent proves with **Groth16** that its score on a fixed evaluation set (an F1 computed from TP / FP / FN by a fixed formula) meets the policy threshold, **without revealing the evaluation details or its predictions**. The proof is bound to the policy, the epoch, a model commitment and the evaluation record; it can be revoked and it expires. Agents can't simply declare a score to get in.

### M2: binding collaboration messages

Beyond the V3 signature, collaboration messages are bound to the on-chain identity, the admission credential, the round and the epoch. Bad signatures, cross-round / cross-task / cross-epoch messages, revoked identities, wrong models and replays are all rejected.

### OTC: full-window collection

Collaboration is k-of-n: evidence is frozen only at the deadline, and the round is voided if fewer than k valid conclusions arrived. Late messages are kept for telemetry only.

### M3: on-chain decision commitment

The **Merkle root** of the conclusion and the member snapshot are written to the `CollaborationAnchor` contract and read back over two nodes' RPC endpoints. If a submission's outcome is unknown, it is recovered by looking up the original hash — never blindly resubmitted.

## Important limits

- On-chain identities and signatures grant **no bank write permission**; money operations still need the user's authorisation on a confirmation card.
- The current environment is a **single-host, 8-node test consortium chain** (chain ID 101); the two verification RPCs belong to two nodes on the same host and don't represent independent institutions.
- Groth16 uses a **development trusted setup** (single-host Phase-2), not a production MPC ceremony.
- The zero-knowledge circuit proves a restricted statistical relation; it doesn't run model inference inside the circuit.

All of this is recorded in `docs/ANCHOR_Integration_Final_Acceptance.md` in the repository.

## Registered agents

| Agent | WeID |
|---|---|
| Finance | `did:weid:101:0xa968103852c7eae1a245b77e98660730d8ccc3d3` |
| Wealth | `did:weid:101:0xa8fac68a926fb16debb085af40a1bb5d840df7de` |
| Transaction | `did:weid:101:0xf9fb420c820cf567b5250f816dd9a21c80f88c31` |

## Turning it on

This layer is off by default. Start the test consortium chain in WSL or Linux first:

```bash
bash scripts/anchor_network_lifecycle.sh run
```

Related settings: `TRUSTA_DID_ENABLED`, `TRUSTA_ZKP_ENABLED`, `TRUSTA_ZKP_ARTIFACT_DIR`, `TRUSTA_ZKP_MANIFEST_SHA256`; see [Configuration](#/configuration). Third parties can re-verify a public evidence package with `python -m scripts.verify_anchor_collaboration`.
