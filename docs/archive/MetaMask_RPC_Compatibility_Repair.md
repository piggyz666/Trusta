# Isolated MetaMask RPC compatibility repair — 2026-10-11

## Findings and scope

Real HTTP probes reproduced two laboratory-server defects: valid parameterless `eth_chainId` requests without `params` returned `RPC_REQUEST_INVALID`; browser OPTIONS preflight returned 501 and responses had no CORS headers. Explicit-parameter requests already returned chain ID `0xaa36a7` / 11155111 correctly. These findings establish server defects; they do not by themselves prove which browser failure occurred before diagnostic logging existed.

Only `scripts/metamask_anchor_lab.py`, its additional protocol tests, and laboratory documentation changed in this repair. The production broadcaster, exact transaction validation, nonce/outbox safety, frozen Agents, banking core, DID, ZKP and Merkle algorithms were not modified.

## Repair and running session

- Exact loopback Host, ingress/bypass capability path and pinned Origin remain mandatory.
- OPTIONS supports POST/Content-Type and conditional Chromium Private Network Access after the same authorization checks. There is no wildcard Origin, arbitrary-extension allowlist, or public RPC fallback.
- Omitted parameters are accepted only for explicitly parameterless reads. Parameterless writes, batches and unauthorized Origins remain rejected.
- Rejection diagnostics omit capability paths, bodies and raw signed transactions. Unused-epoch restart preserves existing diagnostics, wallet and capability.
- The service was restarted safely without reservations or execution attempts. The existing `lab2/operator-session.json` RPC URLs and wallet `0x17a6B8baAFD002A67a59993a96B9f025bAA4C010` were preserved; page JavaScript and RPC share the same capability.

Use **ingress_rpc** in that file, Chain ID **11155111**, currency **ETH**. The page is `http://127.0.0.1:18765/`. This is a local PyEVM simulator reporting the Sepolia ID for isolation tests, not public Sepolia. Do not use bypass_rpc for normal connection testing.

## Actual evidence

Both endpoints passed real HTTP reads of eth_chainId, net_version, eth_blockNumber, eth_getBlockByNumber and web3_clientVersion. CORS and PNA preflight passed; unauthorized Origin still returned 403. Chrome 155 traffic with the pinned MetaMask extension Origin reached ingress and eth_chainId completed successfully. These headers are observational evidence, not cryptographic client attestation. No claim is made that wallet UI network saving or production signing-route compatibility has been verified.

The window automation helper failed its initial observation and recovery with `window id 103879090 no longer belongs to Chrome; current owner is Chrome`; therefore wallet UI success could not be inspected automatically. One retry of the already-filled network form was requested; URL replacement is unnecessary. The operator's wallet UI outcome remains unconfirmed.

- Protocol, ingress attack, concurrency and local EVM tests: **21 passed**.
- Full regression: **60 modules, 805 tests, 0 skipped, all passed**, 353.917 seconds; original baseline tests preserved.
- Bandit on the laboratory and capture gate: **0 findings**; Python compilation passed.
- Real wallet signature requests, public-chain broadcasts and test ETH expenditure: **0**.
- `production_compatible` remains **false**. No external blockchain verification flag was set.

## Evidence locations

- `data/metamask-validation/rpc-compatibility-live.json`
- `data/metamask-validation/browser-rpc-observations.json`
- `data/metamask-validation/lab2/rpc-events.json` (local restricted diagnostics)
- `data/metamask-validation/ingress-tests.txt`
- `data/metamask-validation/static-analysis.json`
- `data/zero-defects/metamask-rpc-compatibility-result.json`
- `data/zero-defects/metamask-rpc-compatibility-tests.txt`

The server remains running with the preserved session. If wallet validation still fails, use its new transport diagnostics to investigate the next concrete request; do not broaden permissions or switch to public Sepolia.
