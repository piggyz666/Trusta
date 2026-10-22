"""Usage: python -m benchmarks.scenario_audit

Six-scenario closed-loop audit: chat words -> validated tool call -> six-agent proposal -> confirm (+2FA) ->
execute -> HardVerifier -> receipt, then the bank state is checked. One fresh bank per scenario."""
import json, os, sys, tempfile, time, traceback
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
os.environ["TRUSTA_DEMO_MODE"] = "true"
from backend.config import Config
from backend.service import TrustaService
from backend.chat.service import ChatService
from backend.chat.llm import ScriptedToolClient

def call(t, **a): return {"name": t, "arguments": a}
def S(v): return {"value": v, "span": v}
def R(i, sp): return {"id": i, "span": sp}

class Bank:
    def __init__(self):
        d = tempfile.mkdtemp()
        self.s = TrustaService(Config(db_path=d + "/a.db", signing_key=b"audit", communication_version=3, banking_version=5,
                                      strong_auth_secret="demo-secret", safety_profile="competition"))
        self.script = {}
        self.c = ChatService(self.s, ScriptedToolClient(self.script))
        x = self.c.create_session("demo", "test"); self.sid, self.rev, self.n = x["session_id"], x["revision"], 0
    def say(self, text, calls):
        self.script[text] = calls; self.n += 1
        t = self.c.send(self.sid, "demo", f"m{self.n}", self.rev, text); self.rev = t["revision"]; return t
    def cards(self, t, k): return [c for c in t["reply"]["cards"] if c["type"] == k]
    def ok(self, card):
        v = None
        if card["risk_level"] == "RED":  # competition profile: SMS one-time code bound to this action
            v = {"method": "SMS", "code": self.c.send_sms(self.sid, "demo", card["action_id"])["dev_code"]}
        r = self.c.decide(self.sid, "demo", card["action_id"], card["action_hash"], True, v); self.rev = r["revision"]
        res = [c for c in r["reply"]["cards"] if c["type"] == "result"]
        return (res[0]["status"] if res else "NO_RESULT"), card["risk_level"], r
    def one(self, sql, *p): return self.s.db.one(sql, p)
    def bal(self, a="acct-demo"): return self.one("SELECT balance FROM accounts WHERE id=?", a)["balance"]
    def total(self): return self.one("SELECT SUM(balance) t FROM accounts")["t"]
    def advance(self, et):
        e = next(e for e in self.s.events.list("demo") if e["status"] == "PENDING" and e["intent"]["event_type"] == et)
        v = self.c.demo_advance(self.sid, "demo", e["id"]); self.rev = v["revision"]; return v

rows = []
def check(scene, item, fn):
    try:
        detail = fn(Bank()); rows.append((scene, item, "PASS", detail))
    except Exception as e:
        rows.append((scene, item, "FAIL", f"{type(e).__name__}: {e}"[:160]))

def need(cond, msg):
    if not cond: raise AssertionError(msg)

def write(b, text, calls, expect_type, state=None):
    t = b.say(text, calls); cs = b.cards(t, "confirm")
    need(cs and cs[0]["action_type"] == expect_type, f"no {expect_type} card: {t['reply']['text'][:80]}")
    st, lvl, r = b.ok(cs[0]); need(st == "SUCCEEDED", f"status {st}: {r['reply']['text'][:80]}")
    if state: need(state(), "bank state not as expected")
    return f"{lvl} 确认→执行→回执"

def read(b, text, calls, kind, rtype=None):
    t = b.say(text, calls); cs = b.cards(t, kind)
    need(cs and (rtype is None or cs[0].get("report_type") == rtype), f"no {kind}/{rtype}: {t['reply']['text'][:80]}")
    return "GREEN 直接返回" + ("·附依据" if cs[0].get("sources") else "")

# 1 智能转账
def t1(b): before=b.bal(); return write(b,"给张三转500",[call("propose_transfer",payee=S("张三"),amount=S("500"))],"TRANSFER",lambda:b.bal()==before-50000)
def t2(b): return write(b,"给13800000002转100",[call("propose_transfer",payee=S("13800000002"),amount=S("100"))],"TRANSFER")
def t3(b): return write(b,"给房东转3000",[call("propose_transfer",payee=S("房东"),amount=S("3000"))],"TRANSFER")
def t4(b):
    from datetime import datetime, timedelta; from backend.business import CHINA
    at=(datetime.now(CHINA)+timedelta(days=1)).replace(hour=9,minute=0,second=0,microsecond=0)
    t=b.say("明天上午9点给张三转100",[call("propose_transfer",payee=S("张三"),amount=S("100"),when={"iso":at.isoformat(),"span":"明天上午9点"})])
    cs=b.cards(t,"confirm"); need(cs,"no card "+t["reply"]["text"][:80]); st,l,_=b.ok(cs[0]); need(st in{"SUCCEEDED","SCHEDULED"},st); return f"{l} 授权后按时执行（{st}）"
def t5(b):
    from datetime import datetime; from backend.business import CHINA
    n=datetime.now(CHINA); first=n.replace(year=n.year+n.month//12,month=n.month%12+1,day=1,hour=9,minute=0,second=0,microsecond=0)
    t=b.say("每月1号9点给房东转3000",[call("propose_transfer",payee=S("房东"),amount=S("3000"),when={"iso":first.isoformat(),"span":"每月1号9点"},recurrence={"frequency":"MONTHLY","count":12,"span":"每月"})])
    need(b.cards(t,"report"),"no plan"); v=b.advance("RECURRING_TRANSFER"); c=v["pending"][0]; st,l,_=b.ok(c); need(st=="SUCCEEDED",st); return "每期到时生成提案→确认执行"
def t6(b): return write(b,"给张三转500备注生活费",[call("propose_transfer",payee=S("张三"),amount=S("500"),memo={"text":"生活费","span":"生活费"})],"TRANSFER")
def t7(b): return read(b,"和张三、李四AA 300块",[call("propose_split",total=S("300"),participants=[S("张三"),S("李四")])],"report","SPLIT_REQUEST")
def t8(b): return write(b,"加收款人王五13900001111",[call("propose_add_payee",name=S("王五"),phone=S("13900001111"))],"ADD_PAYEE")
def t9(b):
    write(b,"给张三转800",[call("propose_transfer",payee=S("张三"),amount=S("800"))],"TRANSFER")
    t=b.say("再给张三转500",[call("propose_transfer",payee=S("张三"),amount=S("500"))]); c=b.cards(t,"confirm")[0]; need(c["risk_level"]=="RED","not RED"); return "超当日累计¥1000→RED 强验证"
for n,f in [("按姓名转账",t1),("按手机号转账",t2),("按称呼（房东）转账",t3),("定时转账",t4),("周期转账（每月）",t5),("带备注转账",t6),("AA收款",t7),("新增收款人",t8),("当日累计超限升级",t9)]: check("1 智能转账",n,f)

# 2 账单分析
check("2 账单分析","月度账单分类",lambda b:read(b,"9月账单",[call("analyze_spending",month="2026-09")],"report","SPENDING_SUMMARY"))
check("2 账单分析","年度账单",lambda b:read(b,"今年账单",[call("analyze_spending",year=2026)],"report","ANNUAL_REPORT"))
check("2 账单分析","多月/分类/商户/环比查询",lambda b:read(b,"9月咖啡和8月比",[call("query_spending",months=["2026-09"],merchant={"text":"咖啡","span":"咖啡"},compare=True)],"report","SPENDING_QUERY"))
def bal(b): return read(b,"余额",[call("get_balance")],"report","BALANCE")
check("2 账单分析","余额查询",bal)
def anomaly(b):
    t=b.say("9月有没有异常交易",[call("analyze_spending",month="2026-09")]); c=b.cards(t,"report")[0]
    keys=[k for k in c if "anomal" in k.lower() or "alert" in k.lower()]; need(keys,"no anomaly field: "+",".join(c)); return f"异常字段 {keys[0]}={len(c[keys[0]])}条"
check("2 账单分析","异常交易识别",anomaly)

# 3 理财
check("3 理财操作","按风险等级推荐",lambda b:read(b,"推荐理财",[call("recommend_products")],"report","PRODUCT_LIST"))
check("3 理财操作","产品对比",lambda b:read(b,"对比低风险和稳健",[call("compare_products",products=[R("sandbox-r1","低风险"),R("sandbox-r2","稳健")])],"report","PRODUCT_COMPARE"))
check("3 理财操作","申购",lambda b:write(b,"买1000低风险",[call("propose_investment",op="PURCHASE",product=R("sandbox-r1","低风险"),amount=S("1000"))],"INVESTMENT_PURCHASE"))
def redeem(b):
    write(b,"买1000低风险",[call("propose_investment",op="PURCHASE",product=R("sandbox-r1","低风险"),amount=S("1000"))],"INVESTMENT_PURCHASE")
    return write(b,"赎回200低风险",[call("propose_investment",op="REDEEM",product=R("sandbox-r1","低风险"),amount=S("200"))],"INVESTMENT_REDEEM")
check("3 理财操作","赎回",redeem)
check("3 理财操作","持仓查询",lambda b:read(b,"我的理财",[call("get_portfolio")],"report","PORTFOLIO"))
def risk(b):
    t=b.say("风险测评",[call("start_risk_assessment")]); need(b.cards(t,"questionnaire"),"no questionnaire"); return "问卷→提交→RISK_PROFILE_UPDATE 确认（见 test_risk_questionnaire）"
check("3 理财操作","风险测评",risk)
def overrisk(b):
    t=b.say("买1000高风险",[call("propose_investment",op="PURCHASE",product=R("sandbox-r4","高风险"),amount=S("1000"))])
    need(not b.cards(t,"confirm"),"R4 card produced"); return "超出风险等级→拒绝："+t["reply"]["text"][:40]
check("3 理财操作","适当性拦截（买超风险产品）",overrisk)

# 4 卡片
def card_status(b): return b.s.gateway.read("service","demo","card.get",resource_id="card-demo")
for name,op,extra,typ,st in [("冻结","FREEZE",{},"CARD_FREEZE","FROZEN"),("挂失","REPORT_LOST",{},"CARD_REPORT_LOST","LOST"),
                              ("调整限额","LIMIT_CHANGE",{"limit":S("2000")},"CARD_LIMIT_CHANGE",None),
                              ("交易开关","CONTROLS",{"controls":{"international":False},"controls_span":"关掉境外"},"CARD_TRANSACTION_CONTROL",None),
                              ("申请虚拟卡","APPLY_VIRTUAL",{},"CARD_APPLY",None),("重置密码","PIN_RESET",{},"CARD_PIN_RESET",None)]:
    text={"CONTROLS":"关掉境外","LIMIT_CHANGE":"限额调到2000"}.get(op,name)
    check("4 卡片管理",name,lambda b,op=op,extra=extra,typ=typ,st=st,text=text:write(b,text,[call("propose_card_operation",op=op,**extra)],typ,(lambda:card_status(b)["status"]==st) if st else None))
def unfreeze(b):
    write(b,"冻结",[call("propose_card_operation",op="FREEZE")],"CARD_FREEZE")
    return write(b,"解冻",[call("propose_card_operation",op="UNFREEZE")],"CARD_UNFREEZE",lambda:card_status(b)["status"]=="ACTIVE")
check("4 卡片管理","解冻",unfreeze)
def found(b):
    write(b,"挂失",[call("propose_card_operation",op="REPORT_LOST")],"CARD_REPORT_LOST")
    return write(b,"找到了",[call("propose_card_operation",op="CANCEL_LOST")],"CARD_CANCEL_LOST",lambda:card_status(b)["status"]=="ACTIVE")
check("4 卡片管理","解除挂失",found)
def undo(b):
    write(b,"冻结",[call("propose_card_operation",op="FREEZE")],"CARD_FREEZE")
    return write(b,"撤销刚才的",[call("undo_last")],"CARD_UNFREEZE")
check("4 卡片管理","撤销（回退）",undo)

# 5 订阅/代扣
check("5 订阅/代扣","订阅列表+识别",lambda b:read(b,"我的订阅",[call("list_subscriptions",detect=True)],"report","SUBSCRIPTIONS"))
check("5 订阅/代扣","代扣列表",lambda b:read(b,"代扣",[call("list_direct_debits")],"report","SUBSCRIPTIONS"))
check("5 订阅/代扣","取消订阅",lambda b:write(b,"取消视频会员",[call("propose_subscription_cancel",subscriptions=[R("sub-stream","视频会员")])],"SUBSCRIPTION_CANCEL"))
check("5 订阅/代扣","关闭代扣",lambda b:write(b,"关电费代扣",[call("propose_direct_debit_disable",direct_debits=[R("dd-electric","电费")])],"DIRECT_DEBIT_DISABLE"))
check("5 订阅/代扣","续费提醒",lambda b:read(b,"要续费的",[call("renewal_reminders")],"report","RENEWAL_REMINDERS"))
def review(b):
    t=b.say("哪些订阅可以取消",[call("review_subscriptions")]); cs=b.cards(t,"confirm"); need(b.cards(t,"report") and cs,"no review cards "+t["reply"]["text"][:60])
    st,l,_=b.ok(cs[0]); need(st=="SUCCEEDED",st); return f"账单→识别→{len(cs)}张取消卡逐项确认"
check("5 订阅/代扣","账单识别→批量取消（联动）",review)

# 6 跨场景
def wish(b):
    money=b.total()
    t=b.say("爱人生日锁1000订花和蛋糕",[call("propose_wish_plan",name="爱人生日",lock_amount=S("1000"),event=R("le-spouse-birthday","爱人生日"),items=[R("m-flower","花"),R("m-cake","蛋糕")],order_days_before=2)])
    lock=b.cards(t,"confirm")[0]; need(len(t["plan"]["nodes"])==3,"plan"); st,_,_=b.ok(lock); need(st=="SUCCEEDED","lock "+st)
    o=b.advance("WISH_ORDER")["pending"][0]; need(b.ok(o)[0]=="SUCCEEDED","order")
    r=b.advance("WISH_RELEASE")["pending"][0]; need(b.ok(r)[0]=="SUCCEEDED","release")
    need(b.total()==money,"money created/destroyed"); return "锁定→到期下单→释放，三步各自确认，总额守恒"
check("6 跨场景联动","心愿金生日计划（题目示例）",wish)
def multi(b):
    t=b.say("查余额再给张三转500",[call("get_balance"),call("propose_transfer",payee=S("张三"),amount=S("500"))])
    need(t["plan"] and len(t["plan"]["nodes"])==2,"no plan"); return "一句多意图→DAG 两节点"
check("6 跨场景联动","多意图 DAG",multi)
def seq(b):
    t=b.say("先给张三转100再买200低风险",[call("propose_transfer",payee=S("张三"),amount=S("100")),call("propose_investment",op="PURCHASE",product=R("sandbox-r1","低风险"),amount=S("200"))])
    st,_,r=b.ok(b.cards(t,"confirm")[0]); nxt=[c for c in r["reply"]["cards"] if c["type"]=="confirm"]; need(nxt,"no follow-up")
    need(b.ok(nxt[0])[0]=="SUCCEEDED","second"); return "依赖步骤：前一步执行后才生成下一步"
check("6 跨场景联动","有依赖的资金步骤串行",seq)
def human(b):
    t=b.say("转人工",[call("request_human",reason="投诉")]); need(b.c.get(b.sid,"demo")["state"]=="HANDED_OFF" or "人工" in t["reply"]["text"],"no handoff"); return "人工接管"
check("6 跨场景联动","人工接管",human)
def interrupt(b):
    b.say("给张三转100",[call("propose_transfer",payee=S("张三"),amount=S("100"))]); r=b.c.interrupt(b.sid,"demo",b.rev); need("取消了 1" in r["reply"]["text"],r["reply"]["text"]); return "中断→取消未执行"
check("6 跨场景联动","中断",interrupt)

w=max(len(r[1]) for r in rows)
for r in rows: print(f"{r[0]:<10} {r[1]:<22} {r[2]:<5} {r[3]}")
print(sum(r[2]=="PASS" for r in rows),"/",len(rows))
out = __import__("pathlib").Path(__file__).resolve().parents[1] / "docs" / "scenario-audit.json"
out.write_text(json.dumps({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "passed": sum(r[2] == "PASS" for r in rows), "total": len(rows),
                           "rows": [dict(zip(("scenario", "capability", "result", "evidence"), r)) for r in rows]}, ensure_ascii=False, indent=1), encoding="utf-8")
print("written", out)
