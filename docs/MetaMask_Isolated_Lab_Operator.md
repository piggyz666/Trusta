# MetaMask actual-extension laboratory — operator setup

This setup is exclusively for a **disposable wallet on a local PyEVM simulator**. No public Sepolia/Mainnet RPC is configured in the service. Do not use the existing funded wallet, import its seed/key, send its password, or fund the test wallet.

The local service is already running at `http://127.0.0.1:18765/`. The simulated chain reports 11155111 solely to test the original Sepolia-only protections. This is not real Sepolia acceptance.

## One operator handoff

Launch this separate Chrome for Testing profile from your own PowerShell:

```powershell
Set-Location -LiteralPath 'D:\TRUSTA金融科技'
& '.\data\metamask-validation\chrome-win64\chrome.exe' '--user-data-dir=D:\TRUSTA金融科技\data\metamask-validation\isolated-profile' '--load-extension=C:\Users\35111\AppData\Local\Google\Chrome\User Data\Default\Extensions\nkbihfbeogaeaoehlefnkodbefgpgknn\13.50.0.0_0' '--no-first-run' '--no-default-browser-check' 'http://127.0.0.1:18765/'
```

This uses the already installed MetaMask extension program files, not the existing Chrome wallet profile. The test browser was downloaded from Google's official Chrome for Testing distribution. Its version is 155.0.8059.39. Do not use a shortcut that opens the usual Chrome profile.

In the new profile, open MetaMask and complete its onboarding yourself. Import only the **DISPOSABLE MNEMONIC** from `D:\TRUSTA金融科技\data\metamask-validation\lab2\DISPOSABLE_TEST_WALLET.txt`, and choose a test-browser password yourself. Do not send the phrase, password or any key to chat. This fixture is generated only for the local simulation.

Configure a separate **TRUSTA Local Simulation** network with Chain ID **11155111**, currency **ETH**, and the `ingress_rpc` URL from `D:\TRUSTA金融科技\data\metamask-validation\lab2\operator-session.json`. Do not enter Infura, Pocket, or another public URL. Enable test networks if MetaMask requires it; do not alter the original profile. Select this network and the test address shown in `lab-public.json`.

No transaction signature is required during setup. If MetaMask will not load, shows the existing funded wallet, or demands a public RPC, stop and report that visible result. Do not work around a browser security warning.

The Windows window tool currently fails to bind this Chrome for Testing window (two observations returned `window id 103879090 no longer belongs to Chrome; current owner is Chrome`). Therefore the following actual-extension input must also be performed by the operator, while TRUSTA records the local RPC requests and validates the received signatures:

1. Return to the laboratory page. Click **连接隔离测试钱包**, connect only the disposable address, then click **生成新的本地测试部署计划**. Check the displayed wallet and plan hash.
2. Click **提交已显示的测试计划给 MetaMask**. Confirm only in this isolated test wallet and only on TRUSTA Local Simulation. Do not change the displayed gas, nonce or fees. This executes solely on local PyEVM, never public Sepolia. The plan expires after 120 seconds; if it expires, cancel and explicitly generate a new test plan rather than changing the old one.
3. Report the visible page result (hash or error text; no keys/passwords). Do not assume a wallet success toast proves ingress capture: Codex will independently check the outbox and RPC trace.
4. The strengthened page guard now rejects bypass during its read-only identity challenge (`LAB_IDENTITY_INGRESS_REQUIRED`). Do not request a transaction signature to test this rejection. The deliberately unsafe local bypass endpoint remains isolated and covered by automated negative routing tests; it cannot satisfy the page's ingress identity check. Actual signed-transaction routing acceptance remains a separate, unfinished experiment.

Reply with both page results after these steps, or the exact visible blocker. The service rejects the real wallet at the page and simulator layers. These operations do not approve any real deployment or commit.

## Subsequent test operations

The laboratory page rejects any wallet except the disposable address and verifies a fresh read-only eth_call challenge against the local session and ingress route before requesting a test transaction. It does not use web3_clientVersion as node identity: MetaMask answers that method internally. The raw-transaction gate rejects changed fields, expired approvals and nonce conflicts. It persists approved raw bytes and a nonce reservation before executing on the local simulator.

The second `bypass_rpc` URL in the same session file is an intentionally unsafe **local** route for the routing test. It must never be used in a production configuration. Its purpose is to detect an actual MetaMask-signed transaction arriving outside the gate, not to authorize a safe production path.

The simulator's receipts are local-only. They must never be exported as real deployment/commit receipts or set `external_blockchain_verified=true`.

The current ingress is capture-only outside the lab. There is no production activation flag or new external broadcaster. Full production compatibility remains unaccepted until actual extension tests, including routing changes, have completed.

## RPC connection repair — 2026-10-11

The experimental server previously rejected JSON-RPC requests that omitted `params`, including valid parameterless `eth_chainId` requests. It also returned HTTP 501 for OPTIONS and omitted CORS responses. Both defects were reproduced with real HTTP requests and repaired only in the laboratory server.

Preflight now requires the exact loopback Host, ingress/bypass capability path, pinned MetaMask extension or same-page Origin, POST method, and an allowed header set. Private Network Access responses are granted only after these checks. Parameter defaults are restricted to parameterless read methods. Batches, unlocked sends, unauthorized Origins, changed plans, and incorrect wallet signatures remain rejected.

The existing `lab2/operator-session.json` addresses and disposable wallet were preserved during an unused-epoch restart. Continue using **ingress_rpc**, not bypass_rpc. No URL replacement or new wallet import is needed. Request diagnostics omit capability-bearing paths, request bodies, and signed bytes. Chrome-extension `eth_chainId` traffic was observed after repair; header evidence alone does not certify wallet UI completion or production signing-route safety.

Evidence: `data/metamask-validation/rpc-compatibility-live.json`, `browser-rpc-observations.json`, `ingress-tests.txt`, and `data/zero-defects/metamask-rpc-compatibility-tests.txt`. These are local simulation results, never external blockchain proofs.
