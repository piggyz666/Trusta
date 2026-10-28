# First-run setup

The setup wizard writes your settings to `.env` in the project folder. The file stays on your computer and is ignored by git; the wizard tightens its permissions and doesn't echo secrets as you type.

```bash
python -m scripts.setup            # interactive setup
python -m scripts.setup --status   # show the feature table only
python -m scripts.setup --probe    # check connectivity, then show the feature table (makes one model call)
```

## 1/4 Model

| Option | Notes |
|---|---|
| DeepSeek (recommended) | Paste an API key (starts with `sk-`); the model name defaults to `deepseek-flash` |
| Any OpenAI-compatible model | Endpoint (`https://…/chat/completions`), model name and key |
| None | The rule engine, which understands standard phrasings such as “给张三转500元” |

You can add an HTTPS proxy if needed. Whichever model you connect, it can only propose actions and never sees balances, statements, card numbers or codes.

## 2/4 Bank

| Option | Notes |
|---|---|
| Local sandbox bank (default) | All six scenarios work, including incoming-payment notifications; all data is fictional |
| External bank sandbox | Revolut, Starling and similar; write a `config/bank.*.local.toml` based on `config/bank.*.example.toml` |

## 3/4 Access and channels

| Option | Notes |
|---|---|
| This computer only (default) | Opens only in this computer's browser; the safest choice |
| Local network | Phones on the same Wi-Fi or hotspot pair once by QR code; see [Phone access](#/mobile) |
| Tunnel | When you already have a public address such as a Cloudflare Tunnel; without a tunnel hostname it falls back to local network |

It then asks whether to connect a Feishu bot, listing the open-platform steps and the scopes the app needs; see [Feishu](#/feishu). You can skip this and fill it in later under **Settings → Feishu** on the web.

## 4/4 Safety and demo

| Setting | Notes |
|---|---|
| Safety profile `competition` (default) | Strong verification: daily totals, transfer frequency, new payees and investment trades are all enforced |
| Safety profile `legacy` | Earlier rules, kept for comparison |
| Demo mode `true` (default) | Simulated SMS codes appear on the page's “simulated phone”; scheduled plans can be fast-forwarded |
| Demo mode `false` | Codes are not shown |

## The feature table

The wizard ends with a feature table. TRUSTA enables features based on what is actually connected and passes its checks:

```
Model: deepseek-flash (VERIFIED)  Bank: virtual/local  Safety: competition

[Six scenarios]
  ✔ available  Transfers (name / phone / nickname / scheduled / recurring / memo)
  ✔ available  Spending analysis / queries / anomaly detection
  ...
[Channels]
  ✘ unavailable  Feishu — missing: Feishu bot
        To enable: run python -m scripts.setup and fill in FEISHU_APP_ID / FEISHU_APP_SECRET
```

(The real output is in Chinese.) Features that aren't enabled are invisible to the assistant and can't be called; the app's Features page also tells you what each one needs.

All settings are listed under [Configuration](#/configuration).
