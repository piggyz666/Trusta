"""Feishu long-connection worker: no public URL, no tunnel (the way OpenClaw connects to Feishu).

    python -m scripts.feishu_worker

Needs FEISHU_APP_ID / FEISHU_APP_SECRET (FEISHU_MODE=longconn, the default when no verification token is set) and the
official SDK: pip install lark-oapi. In the Feishu open platform: enable the bot, choose “使用长连接接收事件”, add the
event im.message.receive_v1 and the card callback card.action.trigger, grant im:message + im:message:send_as_bot,
publish a version.

The SDK only carries bytes: every event is handed to the same FeishuChannel.process() the webhook uses (dedupe,
binding check, YELLOW-only buttons, RED → back to the APP), so both transports behave identically.
"""
import json
import sys
from backend.domain import Rejected


class RawEventHandler:
    """Duck-types the SDK's dispatcher: the WebSocket client calls _do_without_validation(payload_bytes) for every event
    and card callback; the return value becomes the callback response (toast / updated card)."""

    def __init__(self, channel):
        self.channel = channel

    def _do_without_validation(self, payload):
        try:
            data = json.loads(payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else payload)
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict) or (data.get("header") or {}).get("app_id") not in (None, self.channel.config.app_id):
            return None  # an event for another app is never processed
        try:
            return self.channel.process(data)
        except Rejected as exc:
            return {"toast": {"type": "error", "content": "处理失败：" + exc.code}}


def build(service=None):
    from backend.channels.feishu import FeishuChannel, FeishuConfig
    from backend.chat.service import ChatService
    from backend.config import Config
    from backend.service import TrustaService
    config = FeishuConfig.from_env()
    if config.mode != "longconn" or not config.enabled:
        raise SystemExit("飞书长连接未配置：需要 FEISHU_APP_ID 和 FEISHU_APP_SECRET（且 FEISHU_MODE 不是 webhook）。可运行 python -m scripts.setup")
    service = service or TrustaService(Config.local(), recover_workflows=False)
    chat = getattr(service, "chat", None) or ChatService(service)
    return FeishuChannel(chat, config), config


def main():
    channel, config = build()
    try:
        import lark_oapi as lark
        from lark_oapi.ws import Client
    except ImportError:
        raise SystemExit("缺少飞书官方 SDK：pip install lark-oapi")
    print("飞书长连接已启动（无需公网地址）。在飞书里私聊机器人发送“绑定 123456”完成账户绑定。Ctrl+C 停止。", flush=True)
    client = Client(config.app_id, config.app_secret, event_handler=RawEventHandler(channel), log_level=lark.LogLevel.INFO,
                    auto_reconnect=True)
    try:
        client.start()  # blocks; reconnects automatically
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
