import re
from datetime import datetime
from backend.business import CHINA, month_window
from backend.domain import Rejected, cents


class IntentNormalization:
    """Non-agent input layer. V1 intentionally accepts a small explicit grammar."""
    def normalize(self, text):
        if not isinstance(text, str) or len(text) > 1000:
            raise Rejected("INVALID_INPUT")
        clean = text.strip().rstrip("。.!！")
        annual = re.fullmatch(r"(?:分析|查看|统计)\s*([0-9]{4})年?\s*(?:年度)?(?:消费|账单)(?:报告|记录)?", clean)
        if annual:
            year = int(annual[1])
            if not 1900 <= year <= 9998:
                raise Rejected("INVALID_YEAR")
            return {"task_type":"BILL_ANALYSIS", "year":year, "needs_clarification":False}
        bill = re.fullmatch(r"(?:分析|查看)\s*([0-9]{4}-[0-9]{2}|本月)\s*(?:消费|账单)(?:记录)?", clean)
        if bill:
            month = datetime.now(CHINA).strftime("%Y-%m") if bill[1] == "本月" else bill[1]
            month_window(month)
            return {"task_type": "BILL_ANALYSIS", "month": month, "needs_clarification": False}
        if re.fullmatch(r"(?:按我的风险等级|根据我的风险等级)?推荐(?:适合我的)?理财产品", clean):
            return {"task_type": "WEALTH_RECOMMENDATION", "needs_clarification": False}
        if any(word in clean for word in ("购买", "赎回", "买入", "卖出")):
            return {"task_type": "WEALTH_RECOMMENDATION", "needs_clarification": True,
                    "clarification": "当前只支持产品推荐，禁止购买、赎回等资金操作。"}
        match = re.fullmatch(r"\s*给\s*(.+?)\s*转\s*([0-9]+(?:\.[0-9]+)?)\s*(?:元|人民币)\s*[。.!！]?\s*", text)
        if not match:
            return {"task_type": "TRANSFER", "needs_clarification": True,
                    "clarification": "请提供收款人和金额，例如：给张三转 2000 元。"}
        return {"task_type": "TRANSFER", "recipient_name": match[1], "amount": cents(match[2]),
                "currency": "CNY", "needs_clarification": False}
