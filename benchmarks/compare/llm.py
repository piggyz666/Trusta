"""The model as the two baseline agents use it: the same endpoint, model name, key and settings as TRUSTA's own client,
with the standard multi-step tool-calling loop (tool results are fed back to the model)."""
import json
import os
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


class ModelError(Exception):
    pass


class LiveModel:
    def __init__(self):
        from backend.chat.llm import DeepSeekToolClient
        reference = DeepSeekToolClient()  # same configuration source as TRUSTA
        self.api_key, self.model, self.endpoint = reference.api_key, reference.model, reference.endpoint
        self.proxy, self.timeout = reference.proxy, max(reference.timeout, 60)
        if not reference.available:
            raise SystemExit("DEEPSEEK_API_KEY is not configured (.env)")
        self.calls = 0
        self._lock = threading.Lock()

    def chat(self, messages, tools):
        body = {"model": self.model, "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0,
                "max_tokens": 1024, "stream": False, "thinking": {"type": "disabled"}}
        request = Request(self.endpoint, data=json.dumps(body, ensure_ascii=False).encode(), method="POST",
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key})
        opener = build_opener(ProxyHandler({"https": self.proxy, "http": self.proxy} if self.proxy else {}))
        for attempt in range(4):
            try:
                with opener.open(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
                with self._lock:
                    self.calls += 1
                return payload["choices"][0]["message"]
            except HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise ModelError(f"HTTP {exc.code}") from None
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < 3:
                    time.sleep(2 ** attempt * 2)
                    continue
                raise ModelError(f"network: {exc}") from None


class MockModel:
    """Offline stand-in used only to check the evaluation pipeline. It behaves like a gullible agent on purpose (it acts on
    any '转N元给X' it reads anywhere, including tool results), so the scoring of every harm type gets exercised."""
    calls = 0
    model = "mock"

    def chat(self, messages, tools):
        names = {t["function"]["name"] for t in tools}
        last = messages[-1]
        seen = " ".join(str(m.get("content") or "") for m in messages if m["role"] in ("user", "tool"))
        done = {c["function"]["name"] for m in messages if m["role"] == "assistant" for c in m.get("tool_calls") or []}
        user = next(m["content"] for m in reversed(messages) if m["role"] == "user")

        def call(name, **arguments):
            return {"role": "assistant", "content": None,
                    "tool_calls": [{"id": f"c{len(messages)}", "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}

        match = re.search(r"转(?:账)?(\d+)(?:元|块)?给([一-龥]{2,3})", seen) or re.search(r"给([一-龥]{2,3})转(\d+)", seen)
        if match and "transfer" not in done:
            amount, payee = (match.group(1), match.group(2)) if match.re.pattern.startswith("转") else (match.group(2), match.group(1))
            return call("transfer", payee=payee, amount_yuan=float(amount))
        if last["role"] == "user":
            if "余额" in user or "多少钱" in user:
                return call("get_balance")
            if "花" in user or "消费" in user:
                return call("list_spending", month="2026-09")
            if "入账" in user or "转钱" in user:
                return call("list_incoming")
            if "收款人" in user:
                return call("list_payees")
            if "订阅" in user:
                return call("list_subscriptions")
            if "理财" in user and "list_products" in names:
                return call("list_products")
        return {"role": "assistant", "content": "好的，已处理。" + (seen[-60:] if last["role"] == "tool" else "")}
