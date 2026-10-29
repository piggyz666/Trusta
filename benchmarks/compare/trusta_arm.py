"""Arm C: the shipped TRUSTA chat service. Its confirmation cards are read back as the effects it proposed; the bank ledger is
fingerprinted before and after to prove nothing was executed without a decision (no case approves anything)."""
from benchmarks.compare.world import CUSTOMER, ledger_fingerprint


class RecordingClient:
    """Wraps TRUSTA's model client to keep the system prompts it was given (for the prompt-leak check) and count calls."""

    def __init__(self, inner):
        self.inner, self.prompts, self.calls = inner, [], 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def complete(self, system_prompt, history, tools=None):
        self.prompts.append(system_prompt)
        self.calls += 1
        return self.inner.complete(system_prompt, history, tools) if tools is not None else self.inner.complete(system_prompt, history)


def card_effects(card, products):
    fields = card.get("fields") or []
    value = lambda label: next((f.get("value") for f in fields if f.get("label") == label), None)
    amount = next((f["amount_minor"] for f in fields if "amount_minor" in f), None)
    kind = card.get("action_type")
    effect = {"type": kind, "state": "proposed", "verified": card.get("risk_level") == "RED", "risk_level": card.get("risk_level")}
    if kind == "TRANSFER":
        effect |= {"payee": value("收款人"), "amount": amount}
    elif kind == "ADD_PAYEE":
        effect |= {"payee": fields[0]["value"] if fields else None}
    elif kind == "SUBSCRIPTION_CANCEL":
        effect |= {"target": (fields[0]["value"].split(" ")[0] if fields else None)}
    elif kind == "INVESTMENT_PURCHASE":
        name = next((f["value"].split("（")[0] for f in fields if "（" in str(f.get("value"))), None)
        effect |= {"target": name, "amount": amount, "product_risk": products.get(name)}
    return effect


def visible(cards):
    """User-facing strings and bank amounts on the cards (ids, hashes and timestamps are not shown to the customer)."""
    texts, numbers = [], []
    skip = {"card_id", "task_id", "action_id", "action_hash", "transaction_id", "account_id", "expires_at", "ref", "id",
            "evidence", "sources", "action_type", "report_type", "type", "risk_level", "status", "kind", "category_code"}

    def walk(node, key=None):
        if key in skip:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            texts.append(node)
        elif isinstance(node, (int, float)) and not isinstance(node, bool) and key and key.endswith("_minor"):
            numbers.append(round(node / 100, 2))

    walk(cards)
    return "\n".join(texts), numbers


def run(service, model_client, turns, tag, follow_up=None):
    """follow_up(reply, effects) -> one more customer message or None (the same rule is applied to every arm)."""
    from backend.chat.service import ChatService
    client = RecordingClient(model_client)
    chat = ChatService(service, client)
    products = {r["name"]: r["risk_level"] for r in service.db.rows("SELECT name, risk_level FROM wealth_products")}
    before = ledger_fingerprint(service)
    session = chat.create_session(CUSTOMER, "test")
    state = {"revision": session["revision"], "replies": [], "cards": [], "modes": [], "errors": []}

    def say(text, i):
        turn = chat.send(session["session_id"], CUSTOMER, f"{tag}-{i}", state["revision"], text)
        state["revision"] = turn["revision"]
        state["replies"].append(turn["reply"]["text"])
        state["cards"].extend(turn["reply"]["cards"])
        state["modes"].append(turn.get("understanding"))
        if turn.get("error"):
            state["errors"].append(str(turn["error"]))

    effects_of = lambda: [card_effects(c, products) for c in state["cards"] if c.get("type") == "confirm"]
    for i, text in enumerate(turns):
        say(text, i)
    primary_reply, primary_effects = "\n".join(state["replies"]), len(effects_of())
    extra = follow_up(primary_reply, effects_of()) if follow_up else None
    if extra:
        say(extra, len(turns))
    effects = effects_of()
    if ledger_fingerprint(service) != before:
        effects.append({"type": "UNKNOWN_WRITE", "state": "executed", "verified": False})
    card_text, card_numbers = visible(state["cards"])
    return {"reply": "\n".join(state["replies"]), "primary_reply": primary_reply, "primary_effects": primary_effects,
            "follow_up": [extra] if extra else [], "card_text": card_text, "card_numbers": card_numbers, "effects": effects,
            "card_types": sorted({c.get("type") for c in state["cards"]}), "system_prompts": client.prompts,
            "model_calls": client.calls, "understanding": state["modes"], "errors": state["errors"]}
