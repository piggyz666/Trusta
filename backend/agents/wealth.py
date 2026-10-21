import time
from backend.domain import Rejected


class WealthAgent:
    """Grounded recommendations and Phase5 proposals; never executes bank operations."""
    def __init__(self, gateway):
        self.gateway = gateway

    def compare(self, user_id, product_ids):
        products = self.products(user_id)
        selected = [next((p for p in products if p["id"]==key), None) for key in product_ids]
        if any(product is None for product in selected):
            raise Rejected("PRODUCT_NOT_FOUND")
        return {"products": selected, "guaranteed_return": False, "source":"BANK_SANDBOX"}

    def propose(self, task, kind, product_id, values, account_id, ttl):
        from backend.banking.actions import snapshot_from_gateway, build_action
        self.gateway.capabilities.check("wealth", "wealth.propose")
        snapshot = snapshot_from_gateway(self.gateway,"wealth",task["user_id"],account_id,kind,product_id)
        return build_action(task,kind,product_id,values,snapshot,ttl)

    def risk_profile(self, customer_id):
        profile = self.gateway.read("wealth", customer_id, "customer.get_risk_profile")
        if not profile:
            raise Rejected("RISK_PROFILE_REQUIRED", "缺少有效风险档案，请先完成风险评估。")
        if (type(profile["risk_level"]) is not int or not 1 <= profile["risk_level"] <= 5
                or profile["customer_id"] != customer_id):
            raise Rejected("INVALID_RISK_PROFILE")
        if not profile["assessed_at"] <= time.time() < profile["expires_at"]:
            raise Rejected("RISK_PROFILE_EXPIRED", "风险档案已过期或尚未生效，请重新完成风险评估。")
        return profile

    def products(self, customer_id):
        return self.gateway.read("wealth", customer_id, "wealth.list_products")

    def present(self, report, candidate):
        from backend.llm.explanations import wealth_presentation
        return wealth_presentation(report, candidate)

    def match(self, profile, products):
        suitable = []
        for product in products:
            if (type(product["risk_level"]) is not int or not 1 <= product["risk_level"] <= 5
                    or type(product["minimum_amount"]) is not int or product["minimum_amount"] <= 0
                    or type(product["term_days"]) is not int or product["term_days"] < 0):
                raise Rejected("INVALID_PRODUCT_DATA")
            if product["active"] == 1 and product["currency"] == "CNY" and product["risk_level"] <= profile["risk_level"]:
                suitable.append(product)
        return sorted(suitable, key=lambda p: (p["risk_level"], p["term_days"], p["id"]))[:3]

    def explain(self, profile, matched):
        results = []
        for product in matched:
            results.append({"product_id": product["id"], "name": product["name"], "risk_level": product["risk_level"],
                            "currency": product["currency"], "minimum_amount": product["minimum_amount"], "term_days": product["term_days"],
                            "reason_codes": ["RISK_WITHIN_PROFILE", "ACTIVE_CATALOG_PRODUCT"],
                            "reasons": [f"产品风险等级 R{product['risk_level']} 不高于你的 R{profile['risk_level']}。",
                                        f"产品库标记为在架；最低金额 {product['minimum_amount']/100:.2f} 元，持有期字段为 {product['term_days']} 天。"],
                            "risk_note": product["risk_note"],
                            "evidence_refs": [f"bank:risk_profiles:{profile['customer_id']}", f"bank:wealth_products:{product['id']}"]})
        return {"customer_risk_level": profile["risk_level"], "recommendations": results,
                "selection_basis": "按有效风险等级筛选在架CNY产品，按风险等级、持有期和产品ID排序；最多3项。",
                "limitations": "仅为虚构产品库的风险匹配演示，未评估预算与流动性需求，不保证收益。",
                "message": "已生成只读推荐；未发起任何购买或赎回。" if results else "没有匹配当前风险等级的在架产品。",
                "evidence_refs": [f"bank:risk_profiles:{profile['customer_id']}"] + [f"bank:wealth_products:{p['id']}" for p in matched]}
