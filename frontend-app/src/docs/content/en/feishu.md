# Feishu

Connect TRUSTA as a Feishu (Lark) bot and you can chat with it in Feishu. The default is a **long connection**: TRUSTA connects out to Feishu, so no public address is needed.

## 1. Create an app on the Feishu Open Platform

1. Create a custom app and enable the **Bot** capability.
2. Under Events & Callbacks, choose **receive events through a persistent connection**.
3. Add the `im.message.receive_v1` event and card callbacks.
4. Publish a version.
5. Find the **App ID** (starts with `cli_`) and **App Secret** under Credentials & Basic Info.

## 2. Add it to TRUSTA

```bash
pip install -r requirements-channels.txt   # the long connection needs lark-oapi
python -m scripts.setup
```

Answer `y` to “connect a Feishu bot”, fill in the App ID and App Secret, and pick `longconn`. Restart TRUSTA; the window shows that the Feishu long connection has started, and logs go to `data/feishu-worker.log`.

## 3. Bind your account

A Feishu user must be bound before acting as you in TRUSTA:

```bash
python -m scripts.pair --feishu
```

It prints a message like “绑定 123456”. Send that message to the bot in a private chat. The code is valid for 5 minutes and works once.

## What you can do in Feishu

- All queries and conversation.
- **Yellow** operations: confirm on the Feishu card.
- **Red** operations: Feishu can't take codes, so the bot sends a link that opens the same conversation in the TRUSTA web app to finish verification. The link uses the address from your [access setting](#/setup) (local network or tunnel), so it opens on your phone too.

## Webhook mode

If you already have a public address, choose `webhook`: Feishu pushes events to `POST /api/channels/feishu/webhook`. You'll also need the Verification Token and, optionally, the Encrypt Key.
