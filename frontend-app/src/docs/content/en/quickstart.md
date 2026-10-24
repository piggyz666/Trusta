# Quick start

Get TRUSTA running on your own computer and make your first (virtual) transfer in about five minutes.

> TRUSTA connects to a local virtual bank sandbox by default. All demo data is fictional; no real account is touched.

## 1. Install

Pick one option; see [Installation](#/install) for details.

**Windows**: download the portable zip, unzip it and double-click `TRUSTA.cmd`. Or run in PowerShell:

```powershell
irm https://raw.githubusercontent.com/piggyz666/Trusta/main/install.ps1 | iex
```

**macOS / Linux**:

```bash
curl -fsSL https://raw.githubusercontent.com/piggyz666/Trusta/main/install.sh | bash
```

## 2. Configure

The setup wizard opens right after installation. It has four steps, and the first time you can just press Enter through them:

1. **Model**: choose DeepSeek and paste an API key, or choose “none” to use the rule engine.
2. **Bank**: the local sandbox bank (default).
3. **Access**: this computer only (default, safest).
4. **Safety and demo**: the `competition` safety profile plus demo mode (simulated SMS codes are shown on the page).

Run `python -m scripts.setup` any time to change it. See [First-run setup](#/setup).

## 3. Start

```bash
./start.sh          # macOS / Linux
```

```powershell
.\start.ps1         # Windows, or double-click TRUSTA.cmd
```

The window prints a `http://127.0.0.1:8000/#token=…` link; open it in your browser. The token is this computer's access credential — don't share it.

## 4. Try a few sentences

- “我这个月花了多少钱” (How much did I spend this month?): read-only, returns a report card straight away.
- “给张三转500元” (Send ¥500 to Zhang San): creates a yellow confirmation card; nothing happens until you tap Confirm.
- “我的卡丢了” (I lost my card): a red operation that needs a code; in demo mode the code appears on the “simulated phone”.

## 5. See how it works

Turn on **Developer mode** under 设置 → 外观 (Settings → Appearance). Each reply gets a “查看决策过程” (View decision process) link listing every signed message between agents, re-verified on the spot.

## Next

- Use it on your phone: [Phone access](#/mobile)
- Connect Feishu: [Feishu](#/feishu)
- Chat in a terminal: `python -m scripts.chat_cli`
