"""Non-agent explicit banking grammar. Bank IDs are later resolved through Gateway."""
import re
import time
from datetime import datetime, timedelta
from backend.business import CHINA
from backend.domain import Rejected, cents


class BankingIntent:
    def __init__(self, legacy):
        self.legacy = legacy

    def normalize(self, text):
        if not isinstance(text,str) or not 0<len(text.strip())<=1000:
            raise Rejected("INVALID_INPUT")
        text = text.strip().rstrip("。.!！")
        gbp=re.fullmatch(r"从\s*([\w-]+)\s*给\s*(.+?)\s*转\s*([0-9]+(?:\.[0-9]+)?)\s*(GBP|EUR)",text)
        if gbp:
            return {"task_type":"TRANSFER","needs_clarification":False,"source_account_id":gbp[1],"recipient_name":gbp[2],"amount":cents(gbp[3]),"currency":gbp[4]}
        def result(kind, **values):
            return {"task_type":kind,"needs_clarification":False,**values}
        if any(word in text for word in ("绕过","跳过授权","自动确认","忽略规则")):
            return {"task_type":"CARD_MANAGEMENT","needs_clarification":True,"clarification":"请明确业务请求；审批与安全规则必须保留。"}
        match=re.fullmatch(r'申请(?:虚拟)?(?:借记卡|银行卡)\s*([\w-]+)',text)
        if match:
            return result('CARD_MANAGEMENT',operation='CARD_APPLY',target_id=match[1],source_account_id=match[1],parameters={})
        match=re.fullmatch(r'挂失(?:银行卡|卡片|卡)?\s*([\w-]+)',text)
        if match:
            return result('CARD_MANAGEMENT',operation='CARD_REPORT_LOST',target_id=match[1],parameters={})
        match = re.fullmatch(r"(?:从\s*([\w-]+)\s*)?(?:在\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\s+([0-9]{2}:[0-9]{2}(?::[0-9]{2})?)\s*|下周五\s*([0-9]{2}:[0-9]{2})\s*)给\s*(.+?)\s*转\s*([0-9]+(?:\.[0-9]+)?)\s*元",text)
        if match:
            account,date,clock,next_clock,name,amount = match.groups()
            try:
                if date:
                    execute_at = datetime.fromisoformat(date+"T"+clock).replace(tzinfo=CHINA).timestamp()
                else:
                    today = datetime.now(CHINA)
                    friday = (today + timedelta(days=7-today.weekday()+4)).date()
                    execute_at = datetime.fromisoformat(str(friday)+"T"+next_clock).replace(tzinfo=CHINA).timestamp()
            except ValueError:
                raise Rejected("INVALID_SCHEDULE") from None
            if not time.time()<execute_at<=time.time()+366*86400:
                raise Rejected("INVALID_SCHEDULE")
            return result("SCHEDULED_TRANSFER",recipient_name=name,amount=cents(amount),currency="CNY",execute_at=execute_at,
                          timezone="Asia/Shanghai",**({"source_account_id":account} if account else {}))
        if "下周五" in text or re.search(r"在\s*[0-9]{4}-[0-9]{2}-[0-9]{2}",text):
            return {"task_type":"SCHEDULED_TRANSFER","needs_clarification":True,"clarification":"请明确下周五的执行时间，例如下周五09:00给张三转1000元。"}
        match = re.fullmatch(r"从\s*([\w-]+)\s*给\s*(.+?)\s*转\s*([0-9]+(?:\.[0-9]+)?)\s*元",text)
        if match:
            return result("TRANSFER",source_account_id=match[1],recipient_name=match[2],amount=cents(match[3]),currency="CNY")
        match = re.fullmatch(r"(?:冻结|解冻)(?:银行卡|卡片|卡)?\s*([\w-]+)",text)
        if match:
            return result("CARD_MANAGEMENT",operation="CARD_FREEZE" if text.startswith("冻结") else "CARD_UNFREEZE",target_id=match[1],parameters={})
        match = re.fullmatch(r"(?:将|把)?\s*([\w-]+)\s*(?:的)?限额(?:设为|改为)\s*([0-9]+(?:\.[0-9]+)?)元",text)
        if match:
            limit=0 if re.fullmatch(r"0+(?:\.0+)?",match[2]) else cents(match[2])
            return result("CARD_MANAGEMENT",operation="CARD_LIMIT_CHANGE",target_id=match[1],parameters={"spending_limit":limit})
        match = re.fullmatch(r"设置\s*([\w-]+)\s*交易控制\s*线上(开启|关闭)\s*境外(开启|关闭)\s*非接触(开启|关闭)",text)
        if match:
            return result("CARD_MANAGEMENT",operation="CARD_TRANSACTION_CONTROL",target_id=match[1],parameters=dict(zip(
                ("online_enabled","international_enabled","contactless_enabled"),(value=="开启" for value in match.groups()[1:]))))
        match = re.fullmatch(r"取消订阅\s*([\w-]+)",text)
        if match:
            return result("SUBSCRIPTION_MANAGEMENT",operation="SUBSCRIPTION_CANCEL",target_id=match[1],parameters={})
        match = re.fullmatch(r"(?:关闭|停用)代扣\s*([\w-]+)",text)
        if match:
            return result("DIRECT_DEBIT_MANAGEMENT",operation="DIRECT_DEBIT_DISABLE",target_id=match[1],parameters={})
        for phrase,kind in (("查询银行卡","CARD_MANAGEMENT"),("查询订阅","SUBSCRIPTION_MANAGEMENT"),("检测订阅","SUBSCRIPTION_MANAGEMENT"),("查询代扣","DIRECT_DEBIT_MANAGEMENT")):
            if text==phrase:
                return result(kind,operation="DETECT" if phrase=="检测订阅" else "LIST")
        match = re.fullmatch(r"(?:购买|申购|赎回)\s*([\w-]+)\s*([0-9]+(?:\.[0-9]+)?)元",text)
        if match:
            kind="INVESTMENT_REDEEM" if text.startswith("赎回") else "INVESTMENT_PURCHASE"
            return result(kind,operation=kind,target_id=match[1],parameters={"amount":cents(match[2]),"currency":"CNY"})
        match = re.fullmatch(r"比较\s*([\w-]+)\s*(?:和|与)\s*([\w-]+)",text)
        if match:
            return result("PRODUCT_COMPARE",product_ids=[match[1],match[2]])
        match = re.fullmatch(r"(?:AA|aa|分摊)\s*([0-9]+(?:\.[0-9]+)?)元\s*(?:给|参与人)\s*(.+)",text)
        if match:
            names=[name.strip() for name in re.split(r"[、,，]",match[2])]
            if not 2<=len(names)<=20 or any(not name for name in names) or len(set(names))!=len(names) or cents(match[1])<len(names):
                raise Rejected("INVALID_SPLIT")
            return result("SPLIT_REQUEST",amount=cents(match[1]),participants=names,currency="CNY")
        if re.fullmatch(r"分析(?:我)?(?:最近支出|[0-9]{4}-[0-9]{2}消费)[，,]?\s*(?:把|找出|找出可能的)?(?:明显没必要的|可能的)?订阅(?:找出来)?[，,]?\s*我(?:逐项)?确认后取消",text):
            month = re.search(r"[0-9]{4}-[0-9]{2}",text)
            return result("CROSS_SCENARIO",month=month[0] if month else datetime.now(CHINA).strftime("%Y-%m"))
        if any(word in text for word in ("购买","申购","赎回","冻结","解冻","申请","挂失","订阅","代扣","限额","比较","分摊","AA")):
            kind = "INVESTMENT_PURCHASE" if re.search("购买|申购",text) else "INVESTMENT_REDEEM" if "赎回" in text else "CARD_MANAGEMENT"
            return {"task_type":kind,"needs_clarification":True,"clarification":"请明确资源与参数；可以补充产品/卡/订阅编号及金额。"}
        return self.legacy.normalize(text)
