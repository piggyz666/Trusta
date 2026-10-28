# FAQ

## Will it touch my real bank account?

Not by default. TRUSTA connects to a local virtual bank sandbox, and all demo data is fictional. Connecting a real bank needs an adapter configured under `config/` and authorisation on the bank's side.

## Does it work without a language model?

Yes. Without a model it uses the rule engine, which understands standard phrasings such as “给张三转500元” (send ¥500 to Zhang San) or “这个月花了多少钱” (how much did I spend this month). With a model it handles more casual wording and multi-step requests.

## Can the model see my balance?

No. The model sees your words, dates and resource names (such as payee or product names) — never balances, statements, card numbers or codes, and long digit strings are masked before sending. Numbers in replies are filled in from bank queries and checked against that turn's results.

## I typed “confirm” in chat. Why didn't anything happen?

By design. Authorisation only happens on a confirmation card and is bound to exactly what the card shows. That way, even a manipulated model or someone else at your chat window can't move money with a sentence.

## The confirmation card expired.

Cards are valid for 15 minutes. Ask again to get a fresh one.

## Where do I find the code?

In demo mode SMS is simulated: tap “发送验证码” (send code) on the confirmation card and the code appears on the page's simulated phone. An authenticator code works too.

## The web page says I don't have access.

Open the full link printed in the start-up window (the one with `#token=…`). Phones need to be [paired](#/mobile) first.

## My phone can't open it.

Check three things: access is set to local network and TRUSTA was restarted; the phone and computer are on the same Wi-Fi or hotspot; the phone has been paired with a code. Some public Wi-Fi networks isolate devices — try a phone hotspot.

## The Feishu bot doesn't reply.

Check in this order:

1. Does **Settings → Feishu** say “available”? “Save and check” tells you straight away whether the App ID / App Secret work.
2. Under **Permissions** on the Feishu Open Platform, are `im:message.p2p_msg:readonly` and `im:message:send_as_bot` granted? Without them the bot connects but cannot receive or send.
3. Is the persistent connection selected, `im.message.receive_v1` added, and a **new version published after the change**? Does the availability range include you?
4. Did you restart TRUSTA after changing the credentials? Does `data/feishu-worker.log` show a connection? Is `requirements-channels.txt` installed?
5. Did you bind your account with a code from Settings → Feishu, sent to the bot in a private chat?

## Windows says running scripts is disabled on this system.

Start TRUSTA by double-clicking `TRUSTA.cmd`, which runs the start script with `-ExecutionPolicy Bypass`. Or in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

## Installing dependencies is slow or fails.

From mainland China, a pip mirror helps, for example:

```bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

then run the install command again.

## Where is my data, and how do I uninstall?

Everything lives in the TRUSTA folder: `data/` holds the database, keys and logs; `.env` holds your configuration. Delete the folder to uninstall completely.

## Do I need the blockchain and zero-knowledge parts?

No. That layer is for agents from several institutions working together and is off by default. On a single machine, signed messaging, the evidence chain and the audit trail are already fully active. See [Cross-institution collaboration](#/cross-institution).
