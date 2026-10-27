"""Reduce model context without introducing any new authority or tools."""
import re
from backend.chat.tools import TOOLS

def relevant_tools(text):
    common={'ask_user','out_of_scope','smalltalk','flag_risk','cancel_pending','navigate','knowledge','request_human','get_balance','list_cards','undo_last'}
    selected=set()
    for pattern,names in [
        (r"存款|存单|续存|支取|基金|定投|外币|美元|欧元|日元|港币|换汇|信用|溢缴|待办",{"get_extended","propose_extension","start_risk_assessment"}),
        (r'贷款|借款|分期|还款|结清|黄金|买金|卖金',{'get_finance','propose_finance','start_risk_assessment'}),
        (r'转账|转\d|转[一二两三四五六七八九十百千万]|转给|给.{1,12}转',{'propose_transfer','propose_add_payee'}),
        (r'机票|火车|酒店|旅游|旅行|演唱会|电影|缴费|水费|电费|社保|医保',{'life_service','propose_trip_plan'}),
        (r'账单|消费|花了',{'analyze_spending','query_spending'}),
        (r'理财|申购|赎回',{'get_portfolio','recommend_products','compare_products','propose_investment','start_risk_assessment'}),
        (r'挂失|冻结|限额|银行卡',{'propose_card_operation','enable_protection'}),
        (r'订阅|代扣|续费',{'list_subscriptions','list_direct_debits','renewal_reminders','review_subscriptions','propose_subscription_cancel','propose_direct_debit_disable'}),
        (r'心愿|存钱|储蓄|释放|生日|纪念日',{'list_wish_funds','propose_wish_plan','propose_fund_release'}),
        (r'自动|计划|每月|每周|工资',{'list_income','propose_automation','list_automations','cancel_automation','propose_wish_plan','propose_transfer'}),
        (r'AA|平摊|分摊',{'propose_split'})]:
        if re.search(pattern,text):selected|=names
    return (common|selected)&set(TOOLS) if selected else set(TOOLS)
