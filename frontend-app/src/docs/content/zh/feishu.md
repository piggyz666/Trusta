# 飞书接入

把 TRUSTA 接成飞书机器人后，可以在飞书里和它对话。默认使用**长连接**：TRUSTA 主动连到飞书，不需要公网地址。

## 1. 在飞书开放平台创建应用

1. 创建企业自建应用，开启**机器人**能力。
2. “事件与回调”选择**使用长连接接收事件**。
3. 添加事件 `im.message.receive_v1`，以及卡片回调。
4. 发布版本。
5. 在“凭证与基础信息”里找到 **App ID**（以 `cli_` 开头）和 **App Secret**。

## 2. 填进 TRUSTA

```bash
pip install -r requirements-channels.txt   # 长连接需要 lark-oapi
python -m scripts.setup
```

在“接入飞书机器人？”一步选 `y`，填 App ID、App Secret，连接方式选 `longconn`。然后重启 TRUSTA。启动时窗口里会显示“飞书长连接已启动”，日志在 `data/feishu-worker.log`。

## 3. 绑定你的账户

飞书用户必须先绑定，才能以你的身份使用 TRUSTA：

```bash
python -m scripts.pair --feishu
```

窗口会显示一条“绑定 123456”。把这条消息私聊发给机器人即可。绑定码 5 分钟内有效，只能用一次。

## 在飞书里能做什么

- 所有查询和对话都可以直接在飞书完成。
- **黄色**操作：在飞书卡片上点确认。
- **红色**操作：飞书里不能输入验证码，机器人会给一个链接，在 TRUSTA 网页里打开同一个会话完成验证。这个链接使用你在[访问方式](#/setup)里设置的地址（局域网或隧道），所以手机也能打开。

## Webhook 模式

如果你已经有公网地址，也可以选 `webhook` 模式：飞书把事件推送到 `POST /api/channels/feishu/webhook`。需要额外填写 Verification Token，以及可选的 Encrypt Key。
