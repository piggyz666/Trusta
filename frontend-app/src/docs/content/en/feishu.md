# Feishu

Connect TRUSTA as a Feishu (Lark) bot and you can chat with it in Feishu. The default is a **long connection**: TRUSTA connects out to Feishu, so no public address is needed.

## 1. Create an app on the Feishu Open Platform

1. Create a **custom app** and add the **Bot** capability.
2. **Permissions**: grant the two scopes in the table below.
3. **Events & Callbacks → Event configuration**: choose **receive events through a persistent connection** and add the `im.message.receive_v1` event.
4. **Events & Callbacks → Callback configuration**: choose **receive callbacks through a persistent connection** and add the card callback `card.action.trigger`.
5. **Version management & release**: make sure the availability range includes the people who will use the bot, then publish a version. Scopes and events only take effect **after publishing**; publish again whenever you change them.
6. **Credentials & Basic Info**: copy the **App ID** (starts with `cli_`) and the **App Secret**.

| Scope | Name | Used for |
| --- | --- | --- |
| `im:message.p2p_msg:readonly` | Read private messages sent to the bot | receiving messages |
| `im:message:send_as_bot` | Send messages as the app | replies and confirmation cards |

> A missing scope is the most common problem: the bot connects but receives nothing, or cannot reply.

## 2. Add it to TRUSTA

Either way gives the same result; the credentials are stored only in the local `.env` file:

- **Web**: open **Settings → Feishu**, fill in the App ID and App Secret, choose “long connection” and click “save and check”. TRUSTA immediately asks Feishu for an access token with these credentials and tells you whether they work.
- **Setup wizard**: run `python -m scripts.setup` and answer `y` to “connect a Feishu bot”.

The long connection needs the official Feishu SDK (already included in the Windows portable package):

```bash
pip install -r requirements-channels.txt
```

Then **restart TRUSTA**. The Feishu connection is opened at startup; the window shows that the long connection has started, and logs go to `data/feishu-worker.log`. Credentials can only be changed on the computer running TRUSTA; a paired phone can only view them.

## 3. Bind your account

A Feishu user must be bound before acting as you in TRUSTA:

Click “generate binding code” in **Settings → Feishu**, or run:

```bash
python -m scripts.pair --feishu
```

You get a message like “绑定 123456”. Send that message to the bot in a private chat. The code is valid for 5 minutes and works once.

## What you can do in Feishu

- All queries and conversation.
- **Yellow** operations: confirm on the Feishu card.
- **Red** operations: Feishu can't take codes, so the bot sends a link that opens the same conversation in the TRUSTA web app to finish verification. The link uses the address from your [access setting](#/setup) (local network or tunnel), so it opens on your phone too.

## Webhook mode

If you already have a public address, choose `webhook`: Feishu pushes events to `POST /api/channels/feishu/webhook`. You'll also need the Verification Token and, optionally, the Encrypt Key.
