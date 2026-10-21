from backend.domain import Rejected


def money(value):
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}{value // 100}.{value % 100:02}"


def finance_presentation(report, candidate):
    currency = report['summary']['currency']
    unit = '元' if currency == 'CNY' else currency
    text = f"{report['month']}已结算记录 {report['summary']['settled_count']} 条，净支出 {money(report['summary']['net_spend'])} {unit}。"
    flags = report["anomalies"]["flags"]
    large = sum("LARGE_PURCHASE" in f["rule_ids"] for f in flags)
    duplicate = sum("POSSIBLE_DUPLICATE" in f["rule_ids"] for f in flags)
    if candidate["focus"] == "anomaly_summary" or candidate["style"] == "detailed":
        text += f"大额核对提示 {large} 条，可能重复提示 {duplicate} 条；规则提示不等于已确认欺诈。"
    return {"text": text, "evidence_refs": list(report["evidence_refs"]), "source": "LLM_ASSISTED_TEMPLATE"}


def wealth_presentation(report, candidate):
    bank = {p["product_id"]: p for p in report["recommendations"]}
    parts = []
    if {p["product_id"] for p in candidate["products"]} != set(bank):
        raise Rejected("EXPLANATION_NOT_GROUNDED")
    for selected in candidate["products"]:
        p = bank[selected["product_id"]]
        reasons = {"RISK_MATCH": f"风险等级 R{p['risk_level']} 不高于生成推荐时的银行档案 R{report['customer_risk_level']}",
                   "KNOWN_TERM": f"产品库持有期为 {p['term_days']} 天",
                   "KNOWN_MINIMUM": f"产品库最低金额为 {money(p['minimum_amount'])} 元"}
        codes = list(selected["reason_codes"])
        if candidate["style"] == "detailed" and "KNOWN_MINIMUM" not in codes:
            codes.append("KNOWN_MINIMUM")
        parts.append(p["name"] + "：" + "；".join(reasons[code] for code in codes) + "。")
    return {"text": "".join(parts) if parts else "产品库中没有匹配当前银行风险等级的在架产品。",
            "evidence_refs": list(report["evidence_refs"]), "source": "LLM_ASSISTED_TEMPLATE"}
