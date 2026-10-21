from collections import defaultdict
from statistics import median
from backend.business import month_window
from backend.domain import Rejected

CATEGORIES = {"FOOD": "餐饮", "GROCERIES": "日用采购", "TRANSPORT": "交通", "SHOPPING": "购物", "OTHER": "其他"}


class FinanceAgent:
    def recurring(self, user_id, account_id):
        from collections import defaultdict
        history = self.gateway.read("finance", user_id, "transaction.recurring_history", account_id=account_id)
        groups = defaultdict(list)
        for row in history:
            if row["status"] == "SETTLED":
                groups[(row["merchant"], row["amount"], row["currency"])].append(row)
        result = []
        for (merchant, amount, currency), rows in sorted(groups.items()):
            rows.sort(key=lambda row:row["occurred_at"])
            if len(rows)>=3 and all(25*86400 <= b["occurred_at"]-a["occurred_at"] <= 35*86400 for a,b in zip(rows,rows[1:])):
                result.append({"merchant":merchant,"amount":amount,"currency":currency,"account_id":account_id,
                               "authority":"ANALYSIS_CANDIDATE", "evidence_refs":["bank:recurring_charges:"+row["id"] for row in rows]})
        return result

    """Read-only account/transaction analyst, no bank write or database handle."""
    def __init__(self, gateway, config, currencies=frozenset({'CNY'}), default_currency='CNY'):
        self.gateway, self.config = gateway, config
        self.analysis_currencies = frozenset(currencies)
        self.default_currency = default_currency

    def account(self, customer_id, account_id):
        return self.gateway.read("finance", customer_id, "account.get", account_id=account_id)

    def present(self, report, candidate):
        from backend.llm.explanations import finance_presentation
        return finance_presentation(report, candidate)

    def records(self, customer_id, account_id, month):
        return self.gateway.read("finance", customer_id, "transaction.list", account_id=account_id, month=month)

    def annual_records(self, customer_id, account_id, year):
        if type(year) is not int or not 1900 <= year <= 9998:
            raise Rejected("INVALID_YEAR")
        return {f"{year}-{m:02}":self.records(customer_id,account_id,f"{year}-{m:02}") for m in range(1,13)}

    def annual(self, year, months, currency):
        expected = [f"{year}-{m:02}" for m in range(1,13)]
        if set(months) != set(expected):
            raise Rejected("INCOMPLETE_ANNUAL_EVIDENCE")
        # Each query has an independent month boundary. Reject duplicate evidence,
        # rather than accidentally counting the same bank record twice.
        rows = [row for month in expected for row in months[month]]
        if len({row['id'] for row in rows}) != len(rows):
            raise Rejected("DUPLICATE_ANNUAL_EVIDENCE")
        for month in expected:
            start,end,_ = month_window(month)
            if any(not start <= row['occurred_at'] < end for row in months[month]):
                raise Rejected("ANNUAL_EVIDENCE_OUTSIDE_WINDOW")
        report = self.analyze(expected[0],rows,[],currency)
        report.pop('month')
        report.pop('monthly_comparison')
        report.update(period='ANNUAL',year=year,
                      months=[{'month':m,**self.summarize(months[m],currency)} for m in expected],
                      coverage='AVAILABLE_SETTLED_RECORDS',
                      analysis_basis='按上海时区年度区间聚合已结算消费和退款；空月份不代表银行数据完整。')
        return report

    def _currency(self, records, currency=None):
        allowed=self.analysis_currencies
        observed={row['currency'] for row in records}
        if currency is None:
            if len(observed)==1: currency=next(iter(observed))
            elif observed: raise Rejected('ANALYSIS_MIXED_CURRENCY')
            elif self.default_currency is not None: currency=self.default_currency
            elif len(allowed)==1: currency=next(iter(allowed))
            else: raise Rejected('ANALYSIS_CURRENCY_REQUIRED')
        if currency not in allowed or observed-{currency}: raise Rejected('ANALYSIS_MIXED_CURRENCY')
        return currency

    def eligible(self, records, currency=None):
        currency=self._currency(records,currency)
        result = []
        for row in records:
            if row["status"] != "SETTLED":
                continue
            if row['kind']=='NON_CONSUMPTION':
                if type(row['amount']) is not int or row['amount']<0: raise Rejected('INVALID_CONSUMPTION_DATA')
                continue
            if (row["currency"] != currency or type(row["amount"]) is not int or row["amount"] <= 0
                    or row["kind"] not in {"PURCHASE", "REFUND"}):
                raise Rejected("INVALID_CONSUMPTION_DATA")
            result.append(row)
        return result

    def summarize(self, records, currency=None):
        currency=self._currency(records,currency)
        eligible = self.eligible(records,currency)
        gross = sum(r["amount"] for r in eligible if r["kind"] == "PURCHASE")
        refunds = sum(r["amount"] for r in eligible if r["kind"] == "REFUND")
        return {"currency": currency, "purchase_count": sum(r["kind"] == "PURCHASE" for r in eligible),
                "refund_count": sum(r["kind"] == "REFUND" for r in eligible), "settled_count": len(eligible),
                "excluded_count": len(records) - len(eligible), "gross_spend": gross, "refunds": refunds, "net_spend": gross-refunds}

    def analyze(self, month, records, previous_records, currency=None):
        currency=self._currency(records+previous_records,currency)
        categories = defaultdict(lambda: {"gross_spend": 0, "refunds": 0, "count": 0, "evidence_refs": []})
        summary, previous = self.summarize(records,currency), self.summarize(previous_records,currency)
        for row in self.eligible(records,currency):
            code = row["category"] if row["category"] in CATEGORIES else "UNCLASSIFIED"
            category = categories[code]
            category["gross_spend" if row["kind"] == "PURCHASE" else "refunds"] += row["amount"]
            category["count"] += 1
            category["evidence_refs"].append(f"bank:consumption_records:{row['id']}")
        breakdown = [{"category": code, "label": CATEGORIES.get(code, "未分类"), **value,
                      "net_spend": value["gross_spend"]-value["refunds"],
                      "gross_share_bps": (value["gross_spend"]*10000 // summary["gross_spend"]) if summary["gross_spend"] else 0}
                     for code, value in sorted(categories.items())]
        delta = summary["net_spend"]-previous["net_spend"]
        return {"month": month, "timezone": "Asia/Shanghai", "summary": summary, "categories": breakdown,
                "monthly_comparison": {"previous_month": month_window(month)[2], "previous_summary": previous,
                                       "net_change": delta, "net_change_bps": delta*10000 // previous["net_spend"] if previous["net_spend"] > 0 else None},
                "evidence_refs": [f"bank:consumption_records:{r['id']}" for r in self.eligible(records,currency) + self.eligible(previous_records,currency)],
                "analysis_basis": "仅统计已结算消费及退款；转账不计入消费；分类来源为银行记录。"}

    def anomalies(self, records, currency=None):
        currency=self._currency(records,currency)
        purchases = [r for r in self.eligible(records,currency) if r["kind"] == "PURCHASE"]
        # The existing absolute threshold is denominated in CNY. Do not apply it
        # to another currency without a separately configured banking threshold.
        large_amount=self.config.anomaly_large_amount if currency=='CNY' else None
        flagged = defaultdict(set)
        for row in purchases:
            if large_amount is not None and row["amount"] >= large_amount:
                flagged[row["id"]].add("LARGE_PURCHASE")
        if len(purchases) >= 5:
            typical = median(r["amount"] for r in purchases)
            for row in purchases:
                if row["amount"] > typical * 10:
                    flagged[row["id"]].add("ABOVE_10X_MONTH_MEDIAN")
        groups = defaultdict(list)
        for row in purchases:
            groups[(row["merchant"], row["amount"], row["currency"])].append(row)
        for group in groups.values():
            group.sort(key=lambda r: (r["occurred_at"], r["id"]))
            for left, right in zip(group, group[1:]):
                if 0 <= right["occurred_at"] - left["occurred_at"] <= self.config.anomaly_duplicate_seconds:
                    flagged[left["id"]].add("POSSIBLE_DUPLICATE")
                    flagged[right["id"]].add("POSSIBLE_DUPLICATE")
        descriptions = {"LARGE_PURCHASE": "单笔消费达到大额核对阈值", "ABOVE_10X_MONTH_MEDIAN": "单笔金额超过本月消费中位数的10倍",
                        "POSSIBLE_DUPLICATE": "相同商户、金额与币种在短时间内出现重复记录"}
        return {"rules": {"large_amount_cents": large_amount, "median_multiplier": 10,
                          "duplicate_window_seconds": self.config.anomaly_duplicate_seconds},
                "flags": [{"transaction_id": r["id"], "merchant": r["merchant"], "amount": r["amount"],
                           "rule_ids": sorted(flagged[r["id"]]), "reasons": [descriptions[c] for c in sorted(flagged[r["id"]])],
                           "evidence_refs": [f"bank:consumption_records:{r['id']}"]} for r in purchases if r["id"] in flagged],
                "interpretation": "规则提示需要核对，不代表已确认欺诈；不会冻结账户或修改交易。"}
