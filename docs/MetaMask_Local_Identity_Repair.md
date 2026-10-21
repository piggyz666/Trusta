# MetaMask local simulator identity repair — 2026-10-11

## Confirmed cause

The page used `window.ethereum.isMetaMask`, account and chain checks, followed by an exact `web3_clientVersion` match against `TRUSTA-LOCAL-EVM-NO-EXTERNAL-RPC`. The installed MetaMask 13.50.0 middleware (`193.cbf405a481fc646e77ff.js`) answers this method internally with `MetaMask/v…`. It is a wallet version, not the selected node's version. The old browser trace contains ingress chain, block, balance and network reads but zero forwarded browser web3_clientVersion requests. The reported page error followed this incorrect comparison.

This evidence does not establish a public-Sepolia routing error. A shared chain ID alone cannot establish local identity, and header metadata is not cryptographic client attestation.

## Implementation and boundary

Only the isolated laboratory script, its tests and operator documentation changed. Production signing, nonce reservations, durable outbox, financial execution and frozen Agent/DID/ZKP/Merkle modules were not modified.

The page now obtains a random one-time challenge from the same-origin, token-protected laboratory control endpoint. It submits the challenge through the injected provider using standard read-only `eth_call`, targeting a random laboratory identity address. This is a lab-only RPC identity probe, not a deployed Solidity contract. The local handler computes a SHA-256 HMAC with an ephemeral laboratory identity secret, bound to the nonce, route and disposable wallet. This secret is not a wallet key and never enters browser responses or logs.

The control verifier requires a live challenge, an observed ingress probe and the matching proof. It consumes the challenge even on failure. Challenges expire after 30 seconds and at most 64 may be outstanding. Public-node empty responses, fake proofs, bypass proofs and replay cannot pass. The page checks wallet/chain again after verification. Every prepare/send guard repeats the challenge; failed connection clears the pending page proposal.

This proves the current probe reached the correct local ingress. It does not certify the routing of a future signed transaction or a malicious provider that selectively relays probes. The original broadcast-before-validation prohibition and raw transaction gate remain necessary; production signing compatibility remains unaccepted.

## Running environment

The prior tool-owned service was no longer listening during diagnosis. It was recovered in a hidden independent background process, with protected local stdout/stderr files. Resume first checks that the epoch has no nonce reservations or execution attempts. The original disposable wallet and operator-session capability URLs were retained; no new MetaMask network or wallet import was required. Identity secrets/challenges are reset on restart, so old challenges cannot survive it.

Direct ingress reads returned chain ID `0xaa36a7`, net_version `11155111`, the laboratory client marker and a local block at height `0x1`. Live identity verification returned HTTP 200 on ingress and 403 on bypass; replay was rejected. The simulator never connects to a public RPC.

## Actual extension acceptance

The operator refreshed the existing page and confirmed `connected` with the matching disposable address. The same run's Chrome 155 / pinned MetaMask extension HTTP request reached ingress as eth_call, produced `LOCAL_IDENTITY_PROBE`, and was followed by `LOCAL_IDENTITY_VERIFIED`. Evidence is saved in `data/metamask-validation/identity-extension-evidence.json`. No wallet configuration change was needed.

## Verification

- Actual page guard JavaScript was executed in a Node VM/provider fixture against the real loopback HTTP server. It passed with a MetaMask-style internally answered client version and rejected bypass, public-node-style responses, wrong chain and wrong wallet without requesting a signature. This fixture is separately labeled and does not substitute for the actual extension observation above.
- Final targeted suite: **24 tests passed**, including origin/token rejection, wrong proof sender, challenge expiry/capacity, replay, altered transactions, concurrency, restart, response-loss recovery and local EVM execution.
- Full regression: **60 modules / 807 tests / 0 skipped / all passed**, 353.543 seconds. Original baseline tests remain unchanged. That run contained 23 tests in the lab module; one additional identity-control boundary test was added afterward and the complete 24-test targeted suite was rerun successfully. We do not label this as an 808-test single full regression run.
- Bandit on the isolated service and ingress gate: **0 findings**. Python compilation passed.
- Real wallet signatures requested, public broadcasts and test ETH expenditure: **0**. Automated disposable local EVM signing tests are not public-chain transactions.
- `production_compatible` remains false; no external verification flag was set.

## Evidence

- `data/metamask-validation/identity-extension-evidence.json`
- `data/metamask-validation/identity-live-check.json`
- `data/metamask-validation/identity-tests.txt`
- `data/metamask-validation/identity-static-analysis.json`
- `data/metamask-validation/lab2/rpc-events.json`
- `data/zero-defects/metamask-identity-result.json`
- `data/zero-defects/metamask-identity-tests.txt`

The connection error is resolved and actual local identity verification has passed. The laboratory service remains running with the original session. No further operator action is required for this repair. Signed-transaction routing acceptance is a separate unfinished task.
