"""Settings page: one read model, and a few writes.

Sensitive writes (model / API key, access mode) are accepted only from this computer (loopback + master token): a
paired phone can look at settings but cannot repoint the bank assistant to another model or open the server to the LAN.
Secrets are never returned, only a masked form.
"""
import os
import time
from backend.domain import Rejected


def mask(value):
    return f"{value[:4]}…{value[-2:]}" if isinstance(value, str) and len(value) > 8 else ("已设置" if value else None)


class SettingsAPI:
    def __init__(self, chat):
        self.chat, self.s = chat, chat.s

    def read(self, owner, local, port):
        s, caps = self.s, self.chat.capabilities
        facts = caps.facts()
        access = getattr(s, "access", None)
        from backend.channels.feishu import FeishuConfig
        feishu = FeishuConfig.from_env()
        bound = s.db.one("SELECT COUNT(*) AS n FROM channel_bindings WHERE channel='feishu' AND owner=?", (owner,)) if access else None
        security = s.safety.status(owner)
        protection = s.safety.protection(owner)
        accounts = [{"id": r["id"], "name": "储蓄账户" if "saving" in r["id"] else "活期账户"}
                    for r in s.db.rows("SELECT id FROM accounts WHERE customer_id=? ORDER BY id", (owner,))]
        llm = self.chat.llm
        return {
            "editable": local,
            "account": {"owner": owner, "accounts": accounts, "default_account_id": accounts[0]["id"] if accounts else None},
            "llm": facts["llm"] | {"endpoint": getattr(llm, "endpoint", None), "key": mask(getattr(llm, "api_key", "")),
                                   "daily_call_limit": self.chat.LLM_DAILY_CALLS, "usage_today": self.chat.store.llm_usage(owner),
                                   "choices": ["deepseek-flash", "deepseek-v4-pro"]},
            "bank": facts["bank"] | {"config_file": bool(s.config.bank_config_path)},
            "access": {"mode": access.mode if access else "local", "urls": access.urls(port) if access else {},
                       "devices": access.devices(owner) if access else [], "public_host": getattr(access, "public_host", "")},
            "feishu": {"configured": feishu.enabled, "mode": feishu.mode if feishu.enabled else None, "app_id": feishu.app_id or None,
                       "available": facts["have"].get("channel.feishu", False), "bound": bool(bound and bound["n"])},
            "security": {"profile": security.get("profile"), "totp_enrolled": security.get("totp_enrolled"),
                         "locked": bool(security.get("locked")), "daily_limit_minor": security.get("daily_transfer_limit_minor"),
                         "protection": protection and {"until": protection["until"], "reason": protection["reason"]},
                         "sms_demo": os.environ.get("TRUSTA_DEMO_MODE", "false").lower() == "true"},
            "automation": {"active": len(s.automation.rules(owner)) if getattr(s, "automation", None) else 0},
            "privacy": {"model_sees": "你的原话、日期和资源名称（收款人、产品名）", "model_never_sees": "余额、流水、卡号、验证码",
                        "audit_chain_valid": s.audit.verify_chain()},
            "about": {"snapshot_hash": caps.snapshot()[0], "demo_mode": os.environ.get("TRUSTA_DEMO_MODE", "false").lower() == "true",
                      "generated_at": time.time()},
        }

    def update_llm(self, local, model=None, api_key=None):
        if not local:
            raise Rejected("SETTINGS_LOCAL_ONLY")
        from backend.chat.llm import DeepSeekToolClient
        from backend.envfile import write_env
        updates = {}
        if model is not None:
            if not isinstance(model, str) or not 1 <= len(model) <= 64 or any(c.isspace() for c in model):
                raise Rejected("INVALID_INPUT")
            updates["TRUSTA_CHAT_LLM_MODEL"] = model
        if api_key is not None:
            if not isinstance(api_key, str) or not 16 <= len(api_key) <= 200 or any(not c.isprintable() or c.isspace() for c in api_key):
                raise Rejected("INVALID_INPUT")
            updates["DEEPSEEK_API_KEY"] = api_key
        if not updates:
            raise Rejected("INVALID_INPUT")
        write_env(updates)
        os.environ.update(updates)
        self.chat.llm = DeepSeekToolClient()  # takes effect immediately; the next probe verifies it
        self.s.audit.append(None, None, "SETTINGS_LLM_CHANGED", "settings", {"model": os.environ.get("TRUSTA_CHAT_LLM_MODEL"),
                                                                             "key_changed": api_key is not None})
        return self.chat.capabilities.probe_llm()

    def update_access(self, local, mode, public_host=None):
        if not local:
            raise Rejected("SETTINGS_LOCAL_ONLY")
        if mode not in {"local", "lan", "tunnel"} or (mode == "tunnel" and (not isinstance(public_host, str) or "." not in public_host
                                                                              or "/" in public_host or " " in public_host)):
            raise Rejected("INVALID_INPUT")
        from backend.envfile import write_env
        write_env({"TRUSTA_ACCESS_MODE": mode, "TRUSTA_PUBLIC_HOST": public_host if mode == "tunnel" else None})
        self.s.audit.append(None, None, "SETTINGS_ACCESS_CHANGED", "settings", {"mode": mode})
        return {"mode": mode, "restart_required": True}
