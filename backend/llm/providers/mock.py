from backend.domain import canonical
from backend.llm.contracts import ProviderResponse


class MockProvider:
    """Explicit offline fixture. Does not assert real model understanding or quality."""
    def __init__(self, config):
        self.config = config

    def describe_capabilities(self):
        return {"protocol": "offline_fixture", "structured_outputs": True, "streaming": False}

    def health(self):
        return {"status": "configured", "network_checked": False}

    def infer(self, request):
        properties = request.output_schema["properties"]
        if "recipient_text" in properties:
            from backend.conversation.schemas import extract
            from backend.llm.validation import strict_json
            payload = strict_json(request.messages[-1]["content"])
            fields = extract(payload["text"], payload["slots"])
            output = {k: fields.get(k, "") for k in properties}
        elif "focus" in properties:
            output = {"focus": "monthly_summary", "style": "brief", "evidence_refs": ["monthly_summary"]}
        elif "products" in properties:
            from backend.llm.validation import strict_json
            payload = strict_json(request.messages[-1]["content"])
            output = {"products": [{"product_id": p["product_id"], "reason_codes": ["RISK_MATCH", "KNOWN_TERM"]}
                                    for p in payload["products"]], "style": "brief", "evidence_refs": payload["evidence_refs"]}
        elif "needs_clarification" in properties:
            output = {"task_type": "UNKNOWN", "needs_clarification": True}
        elif "template" in properties:
            from backend.llm.validation import strict_json
            payload = strict_json(request.messages[-1]["content"])
            output = {"task_type": payload["task_type"], "template": payload["task_type"]}
        else:
            output = {"summary": "离线测试候选解释，不参与业务决策。", "evidence_refs": []}
        return ProviderResponse(canonical(output), self.config.model, {"input_tokens": 0, "output_tokens": 0})
