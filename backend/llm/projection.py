import re
from backend.business import TASK_TYPES
from backend.domain import canonical
from backend.llm.contracts import InferenceError
from backend.llm.validation import validate


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


TEXT = {"type": "string", "maxLength": 2000}
REFERENCES = {"type": "array", "items": {"type": "string", "maxLength": 128}, "maxItems": 30}
TASK = {"type": "string", "enum": sorted(TASK_TYPES | {"UNKNOWN"})}
OUTPUT_SCHEMAS = {
    "intent_normalization": obj({"task_type": TASK, "needs_clarification": {"type": "boolean"}}),
    "plan_proposal": obj({"task_type": TASK, "template": {"type": "string", "enum": sorted(TASK_TYPES)}}),
    "finance_explanation": obj({"summary": TEXT, "evidence_refs": REFERENCES}),
    "wealth_explanation": obj({"summary": TEXT, "evidence_refs": REFERENCES}),
}
INTEGER = {"type": "integer"}
AMOUNT = {"type": "integer", "minimum": -10**12}
INPUT_SCHEMAS = {
    "intent_normalization": obj({"text": {"type": "string", "maxLength": 1000}}),
    "plan_proposal": obj({"task_type": {"type": "string", "enum": sorted(TASK_TYPES)}}),
    "finance_explanation": obj({"month": {"type": "string", "maxLength": 7},
        "net_spend_cents": AMOUNT, "settled_count": INTEGER, "evidence_refs": REFERENCES}),
    "wealth_explanation": obj({"risk_level": {"type": "integer", "minimum": 1, "maximum": 5},
        "products": {"type": "array", "maxItems": 3, "items": obj({
            "product_id": {"type": "string", "maxLength": 128},
            "risk_level": {"type": "integer", "minimum": 1, "maximum": 5},
            "term_days": INTEGER, "minimum_amount": INTEGER})}, "evidence_refs": REFERENCES}),
}

INTENT_FIELDS = obj({"task_type": TASK, "recipient_text": {"type": "string", "maxLength": 20},
                    "amount_text": {"type": "string", "maxLength": 64},
                    "month_text": {"type": "string", "maxLength": 20}, "needs_clarification": {"type": "boolean"}})
STYLE = {"type": "string", "enum": ["brief", "detailed"]}
OUTPUT_SCHEMAS.update({
    "conversation_intent": INTENT_FIELDS,
    "finance_presentation": obj({"focus": {"type": "string", "enum": ["monthly_summary", "anomaly_summary"]},
                                  "style": STYLE, "evidence_refs": REFERENCES}),
    "wealth_presentation": obj({"products": {"type": "array", "maxItems": 3, "items": obj({
        "product_id": {"type": "string", "maxLength": 128},
        "reason_codes": {"type": "array", "maxItems": 3, "items": {"type": "string", "enum": ["RISK_MATCH", "KNOWN_TERM", "KNOWN_MINIMUM"]}}})},
        "style": STYLE, "evidence_refs": REFERENCES}),
})
INPUT_SCHEMAS.update({
    "conversation_intent": obj({"text": {"type": "string", "maxLength": 1000}, "slots": INTENT_FIELDS}),
    "finance_presentation": obj(INPUT_SCHEMAS["finance_explanation"]["properties"] | {
        "large_count": INTEGER, "duplicate_count": INTEGER}),
    "wealth_presentation": INPUT_SCHEMAS["wealth_explanation"],
})


def project(purpose, payload):
    if purpose not in INPUT_SCHEMAS:
        raise InferenceError("LLM_UNSUPPORTED_PURPOSE")
    try:
        validate(payload, INPUT_SCHEMAS[purpose])
        # A canonical round trip isolates the snapshot from mutable caller-owned objects.
        from backend.llm.validation import strict_json
        projected = strict_json(canonical(payload))
    except (InferenceError, ValueError, TypeError, RecursionError):
        raise InferenceError("LLM_INVALID_INPUT") from None
    if purpose in {"intent_normalization", "conversation_intent"}:
        text = projected["text"]
        text = re.sub(r"(?i)Bearer\s+[^\s]+|sk-[A-Za-z0-9_-]+", "[REDACTED]", text)
        text = re.sub(r"(?<!\d)\d{16,}(?!\d)", "[REDACTED]", text)
        text = re.sub(r"(?i)(?:password|api[_ -]?key|口令|密码|验证码)\s*[:：=]\s*[^\s，,。]+", "[REDACTED]", text)
        projected["text"] = text
    return projected


def validate_output(purpose, output, projected):
    validate(output, OUTPUT_SCHEMAS[purpose])
    if purpose == "intent_normalization" and output["task_type"] == "UNKNOWN" and not output["needs_clarification"]:
        raise InferenceError("LLM_INVALID_OUTPUT")
    if purpose == "plan_proposal" and (output["task_type"] != projected["task_type"]
                                      or output["template"] != projected["task_type"]):
        raise InferenceError("LLM_INVALID_OUTPUT")
    if "evidence_refs" in output and not set(output["evidence_refs"]) <= set(projected["evidence_refs"]):
        raise InferenceError("LLM_UNGROUNDED_REFERENCE")
    if purpose == "conversation_intent":
        from backend.conversation.schemas import extract
        expected = extract(projected["text"], projected["slots"])
        if output["task_type"] == "UNKNOWN" and output["needs_clarification"] and not any(output[k] for k in ("recipient_text", "amount_text", "month_text")):
            return
        if (any(output[k] != expected.get(k) for k in ("task_type", "recipient_text", "amount_text", "month_text"))
                or expected.get("needs_clarification") and not output["needs_clarification"]):
            raise InferenceError("LLM_UNGROUNDED_INTENT")
    elif purpose == "finance_presentation":
        required = {"monthly_summary"}
        if output["focus"] == "anomaly_summary":
            if not projected["large_count"] and not projected["duplicate_count"]:
                raise InferenceError("LLM_INVALID_OUTPUT")
            required.add("anomaly_summary")
        if not required <= set(output["evidence_refs"]):
            raise InferenceError("LLM_UNGROUNDED_REFERENCE")
    elif purpose == "wealth_presentation":
        ids = [p["product_id"] for p in output["products"]]
        if len(ids) != len(set(ids)) or set(ids) != {p["product_id"] for p in projected["products"]}:
            raise InferenceError("LLM_INVALID_OUTPUT")
        if any(not p["reason_codes"] or len(p["reason_codes"]) != len(set(p["reason_codes"])) for p in output["products"]):
            raise InferenceError("LLM_INVALID_OUTPUT")
        if not ({"risk_profile"} | {"product:" + p for p in ids}) <= set(output["evidence_refs"]):
            raise InferenceError("LLM_UNGROUNDED_REFERENCE")
