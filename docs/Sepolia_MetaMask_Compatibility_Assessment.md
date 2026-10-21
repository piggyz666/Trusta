# Sepolia MetaMask compatibility assessment

Scope: existing wallet `0xF84C2E84F43460A94bb24fFEb6d4a61B75De86bE`, Ethereum Sepolia (11155111). This is an intermediate signing-path assessment, not deployment or external verification acceptance.

## Findings

- Chrome's installed extension manifest reports MetaMask **13.50.0.0**, Manifest V3. Only immutable extension program files and its manifest were inspected; wallet storage, vaults, private keys and recovery phrases were not read.
- TRUSTA currently has controlled local-keystore signing, exact-plan approval, raw-transaction verification, nonce reservation and durable persistence before broadcast. It has no existing MetaMask transaction-signing adapter.
- MetaMask's documented `eth_sendTransaction` signs and sends, then returns a transaction hash. Calling it through an ordinary dapp would give TRUSTA the hash after the wallet's broadcast, bypassing the existing persistence-before-broadcast boundary.
- Current official extension controller source supplies `processTransaction`, but does not supply `processSignTransaction`. The official RPC middleware rejects signing-only requests when that handler is absent. Installed extension bundles likewise contain the signing-only middleware option but no separate supplied `processSignTransaction` option. This supports an incompatibility finding for the ordinary extension API; it is not a live wallet execution test.
- `personal_sign` can establish control of this exact address through a clearly labelled, non-transaction EIP-191 challenge. It does not create an EIP-1559 transaction signature or grant transaction approval. Typed-data signing also cannot substitute for signing the unchanged deployment transaction.
- No account connection, wallet message signature, transaction signature or broadcast was requested during this assessment. No test ETH was consumed.

## Non-export path

A possible Anchor-only adaptation is a loopback RPC ingress for MetaMask. It would accept only the exact approved signed transaction, validate chain/sender/nonce/calldata/fees/value, reserve the nonce, durably persist its raw bytes and hash, then hand off to the existing controlled broadcaster. This is a proposed transport adapter, **not an implemented or accepted capability**.

Before accepting this route, test the actual extension's RPC routing and fee/nonce behavior, including smart-transaction routing, timeouts, response loss, restart, concurrent requests, duplicate signatures and rejection of changed fields. The bridge must not forward arbitrary wallet writes. It must fail closed if the installed wallet bypasses the configured ingress or changes approved fields. A wallet control proof alone is insufficient.

The keystore option remains available, but the operator has chosen not to export the private key. No enrollment is required or attempted under that choice. The currently unresolved dependency is a proven compatible non-export wallet signing path. Deployment and commit each still require their own fresh exact-plan approval after that dependency is resolved.

## Existing business evidence checked

The previously sealed Virtual Bank workflow checkpoint (not a remote-bank payment) has root `47442c59df0f67236a658d2aacf0a502a85e302023f0b3994e7c3b0fbc7ef44f`, checkpoint ID `cb85c6e4-7a7e-4ead-80c4-93bbb16fe15d`, and 122 leaves. The existing public-only record verifier and independently pinned registry handoff verifier both returned success again. This is local cryptographic/business evidence, not blockchain acceptance. The root has not been submitted. `external_blockchain_verified` remains false.

## Primary sources inspected

- [MetaMask eth_sendTransaction documentation](https://docs.metamask.io/metamask-connect/evm/reference/json-rpc-api/eth_sendTransaction/)
- [MetaMask signing-data documentation](https://docs.metamask.io/metamask-connect/evm/guides/sign-data/)
- [Official extension controller, pinned revision](https://github.com/MetaMask/metamask-extension/blob/95553d8431511c2686c9c7ae3b4fa759a0cdaaf2/app/scripts/metamask-controller.js)
- [Official RPC middleware, pinned revision](https://github.com/MetaMask/eth-json-rpc-middleware/blob/48b7aea73d8a73abe49f376e41bdab20ba08e887/src/wallet.ts)

No frozen Agent, bank execution, ECDSA, DID, ZKP or Merkle module was modified by this assessment. No chain-success receipt or proof was generated.
