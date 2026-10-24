# Command line

Run every command from the project folder. If you used an installer, activate the virtual environment first (`source .venv/bin/activate`, or `.venv\Scripts\activate` on Windows), or replace `python` with `.venv/bin/python`.

## Everyday

| Command | Does |
|---|---|
| `./start.sh` / `.\start.ps1` / double-click `TRUSTA.cmd` | Start the server and background workers |
| `python -m scripts.setup` | Setup wizard |
| `python -m scripts.setup --status` | Show the feature table |
| `python -m scripts.setup --probe` | Check model, bank and Feishu connectivity, then show the feature table |
| `python -m scripts.pair` | Phone pairing code |
| `python -m scripts.pair --feishu` | Feishu binding code |
| `python -m scripts.pair --devices` | List paired devices |
| `python -m scripts.pair --revoke ID` | Revoke a device |

## Chat in a terminal

```bash
python -m scripts.chat_cli             # connect to the running server
python -m scripts.chat_cli --sandbox   # in-process server + throw-away database; data/ untouched
```

Commands inside the chat:

| Command | Does |
|---|---|
| `/y N` / `/n N` | Confirm / decline card N |
| `/sms N` | Send a simulated SMS code for red card N |
| `/otp N CODE` | Confirm red card N with an SMS code |
| `/totp N CODE` | Confirm red card N with an authenticator code |
| `/stop` | Interrupt the current plan |
| `/trace` | Show the decision chain of the last turn |
| `/events` | List scheduled plans |
| `/advance ID` | Run a scheduled plan now (demo mode only) |
| `/quit` | Exit |

## Verification and audit

| Command | Does |
|---|---|
| `python -m scripts.verify_handoff_evidence BUNDLE --trusted-registry REGISTRY` | Verify a signed evidence bundle offline |
| `python -m scripts.verify_audit_record PROOF --trusted-public-key KEY` | Verify one audit record's Merkle inclusion proof |
| `python -m scripts.manage_crypto_identity --agent NAME --rotate` | Rotate an agent's key |
| `python -m scripts.manage_crypto_identity --agent NAME --status SUSPENDED` | Suspend an agent identity |
| `python -m scripts.manage_crypto_identity --revoke-key KEY_ID` | Revoke a key |

## Tests and evaluation

| Command | Does |
|---|---|
| `python -m pytest -q` | All automated tests (about 1,400) |
| `python -m benchmarks.scenario_audit` | 49 closed-loop checks across six scenarios |
| `python -m benchmarks.security_benchmark --banking-version 5` | The 18-class attack benchmark |

## Background workers

The start script runs these for you:

| Command | Does |
|---|---|
| `python -m scripts.audit_worker --watch` | Produces signed Merkle checkpoints |
| `python -m scripts.scheduled_worker --watch` | Runs due plans (birthday orders, recurring transfers…) |
| `python -m scripts.feishu_worker` | Feishu persistent connection |

## Packaging

```bash
python -m scripts.build_portable --python-embed python-3.12.10-embed-amd64.zip --out dist
```

Builds the Windows portable package `dist/TRUSTA-windows-x64.zip`. The repository's `Packages` GitHub Actions workflow downloads the embeddable Python, builds the package, starts it on Windows as a smoke test, and attaches it to the release when a `v*` tag is pushed.
