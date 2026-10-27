# Local EVM test artifact

`CollaborationAnchor.json` is public compiled bytecode and ABI, not a deployed contract or a wallet. It was compiled from `contracts/CollaborationAnchor.sol` with Solidity 0.4.25, optimization enabled, using `scripts/compile_collaboration_anchor.py`. The tests verify its `source_sha256` against the source before executing it in an isolated `EthereumTester` EVM. No RPC, gas purchase or live-chain transaction is used.

To rebuild, install the optional dependencies in `requirements-phase6.txt`, install the exact compiler with `solcx.install_solc('0.4.25')`, and run `python -m scripts.compile_collaboration_anchor`. The compiler preserves an existing artifact in `data/anchor-integration/weidentity-lab/conf`; inspect it before rebuilding, then review and copy the newly compiled public artifact here. Never substitute a fabricated result or change assertions merely to make tests pass.
