# Actual isolated MetaMask transaction acceptance — 2026-10-11

**Single transaction pre-execution safety: PASS. Production route qualification: FAIL / not accepted. Full live RPC compatibility: FAIL because the existing process still has a receipt serialization defect.**

These are separate conclusions. No public Sepolia deployment, public Root commit, or real-wallet signing occurred during this acceptance. All network requests made by the verifier are loopback reads. The experiment uses local PyEVM with the Sepolia chain ID, not public Sepolia.

## Identifiers

| Field | Actual value |
|---|---|
| Transaction | `0x4ffdef3d469ede267d238d7cfcf0e66f3b07d3ce545f648ad6a511c8827587c4` |
| Exact plan hash | `c65af640017a6fc11ccea17fa933dd52cdeb6ad33295060290e33629b522299a` |
| Recovered signer / Publisher | `0x17a6B8baAFD002A67a59993a96B9f025bAA4C010` |
| Contract | `0xD46122B7F9C906e65f79a78bDA5dB61710e607b1` |
| Local chain ID / nonce | 11155111 / 0 |
| Local block | 2 |
| Block hash | `0x90fa1da084ac23c66df43abe27409449c18677e00250a185d080beb2af4bccec` |
| Authenticated receipt status | 1 |
| Gas limit / actual Gas | 666,832 / 550,499 |
| Effective Gas price | 1,000,001 wei |
| Local execution fee | 550,499,550,499 wei (0.000000550499550499 simulated ETH) |
| Runtime code hash | `0x3398ec99d046fb1681a9a40de8e11257f3dc35dbe7c1eae72c80c1c31abab570` |

## Actual trace and gate checks

The existing SQLite outbox contains the original signed type-2 bytes, exact unsigned plan and hash. A matching shared nonce reservation binds chain/sender/nonce 0 to the transaction and `deploy:<plan_hash>`. The verifier independently recovered the signer and compared type, chain ID, nonce, value, calldata, gas limit, both EIP-1559 fee fields, empty destination/access list, low-s signature, pinned artifact and budget limits. All matched. The capture was inside the original 120-second quote lifetime. It did not renew an expired plan during acceptance.

The recorded sequence, as epoch seconds:

1. **1791715309.9860597:** MetaMask extension HTTP request `eth_sendRawTransaction` reached **ingress**.
2. **1791715310.0234783:** the durable `persisted_before_execution` / attempted marker was written for this hash.
3. **1791715310.0441234:** local EVM execution completed; `new_local_executions=1`, external broadcasts=0.

Source inspection confirms `SignedIngress.capture` performs signature recovery/exact-plan validation before its SQLite transaction commits the signed bytes and nonce together. Only after it returns does the lab persist the attempt marker and call the local EVM. The existing records and timestamps match that path. The log does not separately timestamp every validation substep; the conclusion combines actual signed-byte checks, durable state, observed order and the inspected execution path. It is not a cryptographic attestation against a malicious local operator.

No send event or execution marker for this hash occurred on bypass. Both routes read the same local block and deployed code. That corroborates this transaction's route; the two endpoints are not independent external RPC providers. A single successful transaction cannot establish that every future MetaMask/Smart Transaction/RPC-switch path is safe.

## Receipt, deployment and Publisher

The running old RPC fails when serializing eth-tester's byte-valued `state_root` receipt field, returning `RPC_REQUEST_INVALID` after the receipt read. This explains the wallet's repeated receipt polling. We did not reinterpret that error as a successful RPC receipt response.

Instead, the read-only verifier inspected the actual running EVM block containing exactly this one transaction. It authenticated the raw signed transaction against `transactionsRoot`. It constructed the typed consensus receipt encoding (status 1, actual cumulative Gas 550,499, empty logs/bloom) and verified its Merkle Patricia Trie root equals the actual block's `receiptsRoot`; its inclusion proof was also independently verified. No replay, re-mining, replacement transaction or private key was used. The JSON receipt in the evidence is explicitly labeled **consensus-reconstructed and trie-authenticated**, not an original RPC JSON response. Changed receipt bytes, wrong root and wrong transaction index failed the independent proof checks.

The contract address equals CREATE(sender, nonce 0). Live code is nonempty and equals the pinned artifact's runtime with its two immutable Publisher words filled with the recovered signer. A live read-only publisher() call returns the same experiment wallet. Runtime length is 2,305 bytes. The deployed code, actual receipt commitment and Gas match a successful constructor execution.

## Confirmed transport defect and repair

`rpc_object` now converts bytes/bytearray to RPC hex, recursively handles tuples, preserves booleans, and omits the legacy eth-tester byte status field when the real status is present. A new real-HTTP local EVM test confirms mined receipt serialization, deployed code and Publisher readback.

The repair is present in source and tested. It was **not hot-applied to the already-executed process**: restarting would discard this in-memory PyEVM, and its used-epoch restart guard correctly refuses to fabricate replacement state. The current process is intentionally retained for evidence reads. Thus the original live receipt JSON endpoint remains defective in this preserved epoch. This limitation is not hidden behind the independently authenticated receipt proof.

No changes were made to production financial execution, frozen Agents, DID/ZKP/Merkle or the production broadcaster. No safety check was relaxed.

## Fresh tests and evidence

- Targeted ingress/local EVM suite: **25 passed**, 8.666 seconds.
- Correctly resolved Anchor/signing/nonce/security regression: **60 passed**, 15.323 seconds.
- The initial regression invocation referred to a nonexistent `tests.test_anchor_operator` module and failed import. Its log is retained; the corrected run used `tests.test_sepolia_operator_control` and passed. The failed invocation is not counted as successful.
- Python compilation passed. The verifier refuses `python -O`; this refusal was executed.
- Bandit reported 32 low-severity B101 assert notices in the read-only verifier. These were reviewed: optimization is explicitly refused before verification, so checks cannot silently disappear. They are recorded, not reported as a zero-finding scan. No high/medium findings were reported.

Evidence:

- `data/metamask-validation/actual-transaction-acceptance.json` — public signed bytes, exact plan, nonce binding, trace, block and receipt proof; no wallet secrets or RPC capability URLs.
- `data/metamask-validation/actual-local-contract-readback.json`
- `data/metamask-validation/independent-local-receipt-proof-check.json`
- `data/metamask-validation/transaction-acceptance-tests.txt`
- `data/metamask-validation/transaction-security-regression-verified.txt`
- `data/metamask-validation/transaction-static-analysis.json`
- Read-only verifier: `python -m scripts.verify_metamask_lab_transaction` (run only while the original epoch is available).

## Final decision

**PASS:** this actual MetaMask-signed deployment traversed the controlled ingress and satisfied the current plan/nonce/persistence checks before one local EVM execution. No bypass was observed for this transaction.

**FAIL / NOT QUALIFIED:** declaring the complete MetaMask route production-compatible. The preserved process still has its old receipt transport defect; future routing, Smart Transactions, RPC switching and the production broadcaster integration are not certified by this sample. `production_compatible` and external blockchain verification remain false. No real deployment or commit is approved by this acceptance.
