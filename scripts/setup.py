"""TRUSTA 配置向导：先选环境，再看哪些功能可用（OpenClaw 风格）。

    python -m scripts.setup            交互式配置（模型 → 银行 → 渠道 → 安全），写入 .env
    python -m scripts.setup --status   只看当前配置下的功能表（不发网络请求）
    python -m scripts.setup --probe    实际探测模型 / 银行 / 飞书是否连得通，再给出功能表

Secrets are typed without echo and stored only in the local .env (git-ignored, permissions tightened). Existing lines
that the wizard does not manage are kept verbatim.
"""
import argparse
import getpass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
STATUS_ZH = {"AVAILABLE": "可用", "DEGRADED": "降级", "UNAVAILABLE": "不可用"}
MARK = {"AVAILABLE": "✔", "DEGRADED": "△", "UNAVAILABLE": "✘"}


def read_env():
    values, lines = {}, []
    if ENV.exists():
        lines = ENV.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values, lines


def write_env(updates):
    values, lines = read_env()
    out, seen = [], set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in updates:
            seen.add(key)
            if updates[key] is not None:
                out.append(f"{key}={updates[key]}")
            continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen and value is not None:
            out.append(f"{key}={value}")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        from backend.security.local_files import protect_secret
        protect_secret(ENV)
    except Exception:
        pass


def ask(prompt, default="", secret=False, choices=None, check=None, hint=""):
    """check(value) -> bool rejects malformed input (e.g. a pasted ^V instead of a key) and asks again."""
    while True:
        if secret:
            suffix = f" [已设置 {mask(default)}，回车保留]" if default and (check is None or check(default)) else ""
        else:
            suffix = f" [{default}]" if default else ""
        raw = (getpass.getpass if secret else input)(f"{prompt}{suffix}: ")
        raw = "".join(ch for ch in raw if ch.isprintable()).strip()  # drop control characters such as a literal Ctrl+V
        value = raw or default
        if choices is not None and value not in choices:
            print("  请输入：" + " / ".join(choices))
            continue
        if check is not None and value and not check(value):
            print("  格式不对" + (f"（{hint}）" if hint else "") + "。Windows 窗口里粘贴请用鼠标右键，或在 Windows Terminal 里用 Ctrl+Shift+V。")
            continue
        if secret and raw:
            print(f"  已收到 {mask(value)}")
        return value


def mask(value):
    return f"{value[:4]}…{value[-2:]}（{len(value)} 位）" if value and len(value) > 8 else "（过短）"


DEEPSEEK_KEY = lambda v: v.startswith("sk-") and len(v) >= 20
FEISHU_ID = lambda v: v.startswith("cli_") and len(v) >= 10
FEISHU_SECRET = lambda v: len(v) >= 20 and " " not in v
HOSTNAME = lambda v: "://" not in v and "/" not in v and "." in v and " " not in v


def wizard():
    current, _ = read_env()
    updates = {}
    print("== 1/4 大模型 ==")
    print("  1) DeepSeek（推荐）  2) 其他兼容 OpenAI 接口的模型  3) 不接入（规则引擎，只识别标准说法）")
    choice = ask("选择", "1" if current.get("DEEPSEEK_API_KEY") else "3", choices={"1", "2", "3"})
    if choice == "3":
        updates["DEEPSEEK_API_KEY"] = None
    else:
        updates["DEEPSEEK_API_KEY"] = ask("API Key（输入时不显示）", current.get("DEEPSEEK_API_KEY", ""), secret=True,
                                          check=DEEPSEEK_KEY if choice == "1" else (lambda v: len(v) >= 16), hint="DeepSeek 的 Key 以 sk- 开头") or None
        if choice == "2":
            updates["TRUSTA_CHAT_LLM_ENDPOINT"] = ask("接口地址（https://…/chat/completions）", current.get("TRUSTA_CHAT_LLM_ENDPOINT", ""))
            updates["TRUSTA_CHAT_LLM_MODEL"] = ask("模型名", current.get("TRUSTA_CHAT_LLM_MODEL", ""))
        else:
            updates["TRUSTA_CHAT_LLM_ENDPOINT"] = None
            updates["TRUSTA_CHAT_LLM_MODEL"] = ask("模型名", current.get("TRUSTA_CHAT_LLM_MODEL", "deepseek-flash"))
        updates["TRUSTA_CHAT_LLM_PROXY"] = ask("HTTPS 代理（没有就回车）", current.get("TRUSTA_CHAT_LLM_PROXY", "")) or None
    print("== 2/4 银行 ==")
    print("  1) 本地沙箱银行（六大场景全部可用，含入账推送，演示推荐）  2) 外部银行沙箱（Revolut / Starling，需要配置文件）")
    bank = ask("选择", "2" if current.get("TRUSTA_BANK_CONFIG") else "1", choices={"1", "2"})
    updates["TRUSTA_BANK_CONFIG"] = ask("银行配置文件路径（config/bank.*.local.toml）", current.get("TRUSTA_BANK_CONFIG", "")) if bank == "2" else None
    print("== 3/4 访问方式与渠道 ==")
    print("  网页 / 手机 H5 是同一套页面，按屏幕自动适配。")
    print("  1) 仅本机（默认，最安全）  2) 局域网：手机连同一 Wi-Fi / 热点，扫码配对一次  3) 隧道：已有 Cloudflare Tunnel 等公网地址")
    modes = {"1": "local", "2": "lan", "3": "tunnel"}
    current_mode = {v: k for k, v in modes.items()}.get(current.get("TRUSTA_ACCESS_MODE", "local"), "1")
    access = modes[ask("选择", current_mode, choices=set(modes))]
    updates["TRUSTA_ACCESS_MODE"] = access
    updates["TRUSTA_PUBLIC_HOST"] = None
    if access == "tunnel":
        host = ask("隧道的公网域名（如 xxx.trycloudflare.com，不带 https://；还没有就直接回车改用局域网）",
                   current.get("TRUSTA_PUBLIC_HOST", ""), check=HOSTNAME, hint="只填域名")
        if host:
            updates["TRUSTA_PUBLIC_HOST"] = host
        else:
            updates["TRUSTA_ACCESS_MODE"] = "lan"
            print("  没有隧道域名，已改为局域网模式。")
    if ask("接入飞书机器人？(y/n)", "y" if current.get("FEISHU_APP_ID") else "n", choices={"y", "n"}) == "y":
        print("  飞书开放平台：创建应用 → 开启机器人 → 事件与回调选“使用长连接接收事件” → 添加 im.message.receive_v1 和卡片回调 → 发布版本")
        updates["FEISHU_APP_ID"] = ask("App ID", current.get("FEISHU_APP_ID", ""), check=FEISHU_ID, hint="以 cli_ 开头")
        updates["FEISHU_APP_SECRET"] = ask("App Secret（输入时不显示）", current.get("FEISHU_APP_SECRET", ""), secret=True,
                                           check=FEISHU_SECRET, hint="开发者后台“凭证与基础信息”里的 App Secret")
        mode = ask("连接方式 longconn（长连接，推荐，无需公网）/ webhook（需要公网地址）",
                   current.get("FEISHU_MODE", "longconn"), choices={"longconn", "webhook"})
        updates["FEISHU_MODE"] = mode
        if mode == "webhook":
            updates["FEISHU_VERIFICATION_TOKEN"] = ask("Verification Token", current.get("FEISHU_VERIFICATION_TOKEN", ""), secret=True)
            updates["FEISHU_ENCRYPT_KEY"] = ask("Encrypt Key（没有就回车）", current.get("FEISHU_ENCRYPT_KEY", ""), secret=True) or None
        else:
            updates["FEISHU_VERIFICATION_TOKEN"] = updates["FEISHU_ENCRYPT_KEY"] = None
        print("  账户绑定：启动后运行 python -m scripts.pair --feishu，把“绑定 123456”私聊发给机器人。")
    else:
        for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_VERIFICATION_TOKEN", "FEISHU_ENCRYPT_KEY", "FEISHU_MODE"):
            updates[key] = None
    print("== 4/4 安全与演示 ==")
    updates["TRUSTA_SAFETY_PROFILE"] = ask("安全档位 competition（强验证）/ legacy", current.get("TRUSTA_SAFETY_PROFILE", "competition"),
                                           choices={"competition", "legacy"})
    updates["TRUSTA_DEMO_MODE"] = ask("演示模式（短信验证码回显、可快进计划）true/false", current.get("TRUSTA_DEMO_MODE", "true"),
                                      choices={"true", "false"})
    write_env(updates)
    print(f"已写入 {ENV}（不会提交到 git）。")


def report(probe):
    for key, value in read_env()[0].items():
        os.environ.setdefault(key, value)
    from backend.chat.service import ChatService
    from backend.config import Config
    from backend.service import TrustaService
    service = TrustaService(Config.local(), recover_workflows=False)
    chat = getattr(service, "chat", None) or ChatService(service)
    if probe:
        for name in ("llm", "bank", "feishu"):
            result = getattr(chat.capabilities, "probe_" + name)()
            extra = result.get("code") or (f"{result['latency_ms']} ms" if "latency_ms" in result else "")
            print(f"探测 {name:<7} {result['status']:<15} {extra}")
    data = chat.capabilities.public()
    print(f"\n模型：{data['llm']['model'] or '未接入'}（{data['llm']['state']}）  银行：{data['bank']['provider']}/{data['bank']['environment']}"
          f"  安全档位：{data['safety_profile']}  快照：{data['snapshot_hash'][:12]}")
    group = None
    for f in data["features"]:
        if f["group"] != group:
            group = f["group"]
            print(f"\n【{group}】")
        print(f"  {MARK[f['status']]} {STATUS_ZH[f['status']]:<3} {f['title']}" + (f"  —— {f['reason']}" if f["reason"] else ""))
        for tip in f["how_to_enable"]:
            print(f"        开通方法：{tip}")
    s = data["summary"]
    print(f"\n合计：可用 {s['AVAILABLE']}，降级 {s['DEGRADED']}，不可用 {s['UNAVAILABLE']}")


def main():
    parser = argparse.ArgumentParser(description="TRUSTA 配置向导")
    parser.add_argument("--status", action="store_true", help="只显示功能表")
    parser.add_argument("--probe", action="store_true", help="探测连通性后显示功能表（会调用一次模型）")
    args = parser.parse_args()
    if not args.status and not args.probe:
        wizard()
        args.probe = ask("现在探测连通性并显示功能表？(y/n)", "y", choices={"y", "n"}) == "y"
        args.status = True
    report(args.probe)


if __name__ == "__main__":
    main()
