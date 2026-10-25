# Installation

There are four ways to install TRUSTA; they end up the same, so pick whichever suits you. Every option installs into a single folder and leaves your system untouched.

| Option | Best for | Needs |
|---|---|---|
| Portable package | Just using it, without installing Python (Windows) | Nothing |
| One-liner | Most people | Python 3.11+ |
| From source | Changing code, running tests, wiring in your own bank or model | Python 3.11+, git |

## Portable package (Windows)

1. Download `TRUSTA-windows-x64.zip` from the [releases page](https://github.com/piggyz666/Trusta/releases).
2. Unzip it anywhere, for example `D:\TRUSTA`.
3. Double-click `TRUSTA.cmd`. The first run opens the [setup wizard](#/setup); after that it starts straight away.

The zip carries its own Python runtime (under `runtime\`) and every dependency, so nothing else needs installing. Data lives in the `data\` folder next to it; deleting the folder uninstalls TRUSTA completely.

On macOS and Linux, use the one-liner (next section).

## One-liner

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/piggyz666/Trusta/main/install.ps1 | iex
```

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/piggyz666/Trusta/main/install.sh | bash
```

The installer:

1. Checks for Python 3.11+. On Windows, if it's missing, it offers to install Python 3.12 for the current user with `winget`; on macOS / Linux it explains how to install it and stops.
2. Downloads the code to `~/TRUSTA` (`%USERPROFILE%\TRUSTA` on Windows), with `git clone` when git is available and a source archive otherwise.
3. Creates a private virtual environment `.venv` in that folder and installs dependencies.
4. Opens the setup wizard.

Environment variables you can set first:

| Variable | Effect | Default |
|---|---|---|
| `TRUSTA_HOME` | Install folder | `~/TRUSTA` |
| `TRUSTA_BRANCH` | Which branch to download | `main` |
| `TRUSTA_NO_SETUP` | `1` skips the setup wizard | — |

Running the same command again updates the code (`git pull` for git installs) and keeps your configuration and data.

## From source

```bash
git clone https://github.com/piggyz666/Trusta.git && cd Trusta
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-channels.txt
python -m scripts.setup
./start.sh                           # Windows: .\start.ps1
```

- `requirements.txt` is what TRUSTA needs to run.
- `requirements-channels.txt` is optional: Feishu long connection and terminal QR codes.
- `requirements-llm.txt`, `requirements-phase6.txt` and `requirements-evaluation.txt` are for evaluation and external audit integrations; everyday use doesn't need them.

After changing the web app, rebuild it (needs Node.js):

```bash
cd frontend-app
npm install
npm run build
```

The build lands in `frontend/dist` and is served by the backend.

## Starting and stopping

| System | Start |
|---|---|
| Windows | Double-click `TRUSTA.cmd`, or `.\start.ps1` in PowerShell |
| macOS / Linux | `./start.sh` |

The start script runs the API server, the audit checkpoint worker and the scheduled-plan worker (birthday orders, recurring transfers and so on), plus the Feishu worker when a long connection is configured. Worker logs go to `data/*.log`. Closing the window or pressing `Ctrl+C` stops everything.

The start script picks Python in this order: the `TRUSTA_PYTHON` environment variable → the portable package's `runtime/python` → the installer's `.venv` → `python` on your PATH.

## Uninstalling

TRUSTA only writes to its own folder. Delete the folder; back up `data/` (database and keys) and `.env` (your configuration) first if you need them.
