"""Terminal client for the unified chat API — an end-to-end test bench before the frontend exists.

    # against the running server (start.ps1), real database:
    python -m scripts.chat_cli
    # self-contained: in-process server + throw-away database (your data/ is untouched):
    python -m scripts.chat_cli --sandbox

Commands inside the chat:
    /y N            confirm card N (YELLOW)          /n N        reject card N
    /sms N          send an SMS code for RED card N   /otp N CODE confirm RED card N with an SMS code
    /totp N CODE    confirm RED card N with an authenticator code
    /stop           interrupt the current plan       /trace      show the decision chain of the last turn
    /events         list scheduled plans             /advance ID run a scheduled plan now (TRUSTA_DEMO_MODE=true)
    /quit
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
RISK = {"GREEN": "绿", "YELLOW": "黄 · 需确认", "RED": "红 · 需验证"}


class Client:
    def __init__(self, base, token):
        self.base, self.token = base.rstrip("/"), token

    def call(self, path, body=None):
        request = Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                          method="GET" if body is None else "POST",
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json",
                                   "Accept": "application/json"})
        try:
            with urlopen(request, timeout=90) as response:
                return json.loads(response.read())
        except HTTPError as error:
            with error:
                payload = json.loads(error.read() or b"{}")
            raise SystemExit(f"[{error.code}] {payload.get('error')}: {payload.get('message', '')}") if error.code in (401, 403) \
                else RuntimeError(f"{error.code} {payload.get('error')}")


def show(turn, cards_by_index):
    reply = turn.get("reply") or {}
    mode = turn.get("understanding")
    if mode:
        print(f"  [理解: {mode}{' / ' + turn['fallback_reason'] if turn.get('fallback_reason') else ''}]")
    print("助手：" + reply.get("text", ""))
    for card in reply.get("cards", []):
        render(card, cards_by_index)
    plan = turn.get("plan")
    if plan:
        print("  计划：" + " → ".join(f"{n['title']}[{n['status']}]" for n in plan["nodes"]))


def render(card, cards_by_index):
    kind = card["type"]
    if kind == "confirm":
        index = len(cards_by_index) + 1
        cards_by_index[index] = card
        print(f"  ┌ 确认卡 #{index} {card['title']}  （{RISK.get(card['risk_level'], card['risk_level'])}）")
        for field in card["fields"]:
            print(f"  │ {field['label']}：{field['value']}")
        if card.get("risk_reasons"):
            print("  │ 原因：" + "；".join(card["risk_reasons"]))
        hint = f"/sms {index} 然后 /otp {index} 验证码，或 /totp {index} 动态码" if card["risk_level"] == "RED" else f"/y {index} 确认，/n {index} 拒绝"
        print(f"  └ {hint}")
    elif kind == "report":
        print(f"  ▣ 报告 {card.get('report_type')}")
    elif kind == "clarify":
        options = [o["label"] for o in card.get("options", [])]
        print("  ? " + card["question"] + (f"  可选：{' / '.join(options)}" if options else ""))
    elif kind == "risk_alert":
        print(f"  ⚠ 风险提示 {card['category']}：{card['message']}")
    elif kind == "result":
        print(f"  ✓ 结果 {card['status']}：{card['message']}" + (f"（回执 {card['receipt_id'][:8]}）" if card.get("receipt_id") else ""))
    elif kind == "questionnaire":
        print("  ▣ 风险测评问卷（请在前端完成，或调用 /risk-assessment 接口）")
    elif kind == "handoff":
        print(f"  ☎ 已转人工，工单 {card['handoff_id']}")


def sandbox():
    sys.path.insert(0, str(ROOT))
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    os.environ.setdefault("TRUSTA_DEMO_MODE", "true")
    from backend.config import Config
    from backend.server import create_server
    from backend.service import TrustaService
    temp = tempfile.mkdtemp(prefix="trusta-sandbox-")
    service = TrustaService(Config(db_path=str(Path(temp) / "sandbox.db"), signing_key=os.urandom(32), communication_version=3,
                                   banking_version=5, safety_profile="competition"))
    token = uuid.uuid4().hex
    server = create_server(service, token, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"沙箱已启动：临时数据库 {temp}（关闭后丢弃，不影响 data/）")
    return Client(f"http://127.0.0.1:{server.server_port}", token)


def main():
    parser = argparse.ArgumentParser(description="TRUSTA 对话命令行")
    parser.add_argument("--sandbox", action="store_true", help="进程内启动服务 + 临时数据库")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    if args.sandbox:
        client = sandbox()
    else:
        token = args.token or os.environ.get("TRUSTA_API_TOKEN") or (ROOT / "data" / "api.token").read_text(encoding="utf-8").strip()
        client = Client(args.base, token)
    session = client.call("/api/chat/sessions", {"channel": "app"})
    sid, revision = session["session_id"], session["revision"]
    print(f"会话 {sid}  理解模式：{session['llm_mode']}（deepseek = 真实模型；rules = 规则兜底）")
    for message in session["messages"]:
        print("助手：" + message["text"])
    cards, last_message = {}, None
    while True:
        try:
            text = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        try:
            if text == "/quit":
                break
            parts = text.split()
            if parts[0] in {"/y", "/n", "/otp", "/totp"}:
                card = cards[int(parts[1])]
                verification = None
                if parts[0] == "/otp":
                    verification = {"method": "SMS", "code": parts[2]}
                elif parts[0] == "/totp":
                    verification = {"method": "TOTP", "code": parts[2]}
                result = client.call(f"/api/chat/sessions/{sid}/actions/{card['action_id']}/decision",
                                     {"action_hash": card["action_hash"], "approve": parts[0] != "/n", "verification": verification}
                                     if verification else {"action_hash": card["action_hash"], "approve": parts[0] != "/n"})
                revision = result["revision"]
                show(result, cards)
            elif parts[0] == "/sms":
                card = cards[int(parts[1])]
                sms = client.call(f"/api/chat/sessions/{sid}/actions/{card['action_id']}/sms", {})
                print(f"  短信已发送至 {sms['sent_to']}" + (f"（演示模式验证码：{sms['dev_code']}）" if sms.get("dev_code") else ""))
            elif parts[0] == "/stop":
                result = client.call(f"/api/chat/sessions/{sid}/interrupt", {"revision": revision})
                revision = result["revision"]
                show(result, cards)
            elif parts[0] == "/trace":
                trace = client.call(f"/api/chat/sessions/{sid}/trace/{last_message}")
                print(json.dumps(trace, ensure_ascii=False, indent=1)[:4000])
            elif parts[0] == "/events":
                for event in client.call("/api/events")["events"]:
                    print(f"  {event['id']}  {event['intent']['event_type']}  {event['status']}")
            elif parts[0] == "/advance":
                view = client.call(f"/api/chat/sessions/{sid}/demo/advance", {"event_id": parts[1]})
                revision = view["revision"]
                for message in view["messages"][-2:]:
                    print("助手：" + message["text"])
                for card in view["pending"]:
                    render(card, cards)
            else:
                last_message = "cli-" + uuid.uuid4().hex[:12]
                turn = client.call(f"/api/chat/sessions/{sid}/messages", {"message_id": last_message, "revision": revision, "text": text})
                revision = turn["revision"]
                show(turn, cards)
        except (KeyError, IndexError, ValueError):
            print("  命令格式不对，见文件开头的说明。")
        except RuntimeError as error:
            print(f"  请求失败：{error}")
            revision = client.call(f"/api/chat/sessions/{sid}")["revision"]


if __name__ == "__main__":
    main()
