"""Grounded candidate generation for extended virtual banking; never approval."""
import re
from decimal import Decimal,InvalidOperation
from backend.banking.extensions import GoldSavings
from backend.chat.amounts import format_cents

OPS=['DEPOSIT_OPEN','DEPOSIT_WITHDRAW','DEPOSIT_RENEW','FX_BUY','FX_SELL','FUND_SUBSCRIBE','FUND_CONFIRM','FUND_CANCEL','FUND_REDEEM','CARD_REPAY','CARD_REFUND','CARD_SURPLUS','GOLD_SAVINGS_BUY']
def resources(chat,owner):
 from backend.bank_sdk.discovery import supports
 if not supports(chat.s,'finance.list'):return []
 st=chat.s.gateway.read('service',owner,'finance.list')
 rows=[{'id':p['id'],'display':p['name'],'kind':'fund_product'} for p in st['fund_products']]
 for d in st['deposits']:
  if d['status']=='ACTIVE':rows.append({'id':d['id'],'display':'存单 '+d['id'],'kind':'deposit'})
 for v in st['credit_states']:
  for o in v['orders']:
   if o['status']=='PENDING':rows.append({'id':o['id'],'display':'基金订单 '+o['id'],'kind':'fund_order','card_id':v['card_id']})
  for e in v['entries']:
   if e['amount']>e['refunded']:rows.append({'id':e['id'],'display':e['merchant']+' '+e['id'],'kind':'credit_entry','card_id':v['card_id']})
 for p in GoldSavings(chat).view(owner)['plans']:
  due=next((r['due'] for r in p['runs'] if r['state']=='DUE'),None)
  if p['status']=='ACTIVE' and due:rows.append({'id':p['id'],'display':p['name']+' '+p['id'],'kind':'savings','due':due})
 return rows
def validate(v,a):
 from backend.chat.validator import Clarify,_norm
 op=a['operation'];latest=v.latest
 words={'DEPOSIT_OPEN':r'存入|存定期|开立|存款','DEPOSIT_WITHDRAW':r'支取|取出','DEPOSIT_RENEW':r'续存', 'FX_BUY':r'买入|买|购汇|兑换', 'FX_SELL':r'卖出|结汇|换回',
  'FUND_SUBSCRIBE':r'申购|买入','FUND_CONFIRM':r'确认份额','FUND_CANCEL':r'撤单|撤销申购|取消申购','FUND_REDEEM':r'赎回','CARD_REPAY':r'还款|还清|偿还','CARD_REFUND':r'退款','CARD_SURPLUS':r'退还.*溢缴|取回.*溢缴','GOLD_SAVINGS_BUY':r'执行|办理|本期|这一期'}
 if not re.search(words[op],latest):raise Clarify('OPERATION_UNGROUNDED','operation','请明确说明本次要办理的业务。')
 allowed={'operation','card','resource','amount','term','currency','quantity','repay_mode'}
 if set(a)-allowed:raise Clarify('INVALID_INPUT','other','请重新说明业务。')
 def unique(key,kind):
  if key not in a:raise Clarify('RESOURCE_REQUIRED','other','请选择具体存单、基金产品、订单或定投计划，并提供名称或完整编号。')
  ref=a[key];v.span(ref['span'],'other','业务对象');span=_norm(ref['span']);items=v.catalog.get(kind+'s',[])
  matches=[x for x in items if span in _norm(x['display']) or _norm(x['id']) in span]
  if len(matches)!=1 or matches[0]['id']!=ref['id']:raise Clarify('RESOURCE_AMBIGUOUS','other','无法唯一确定业务对象，请补充完整编号或名称。')
  return matches[0]
 needed={'DEPOSIT_WITHDRAW':'deposit','DEPOSIT_RENEW':'deposit','FUND_SUBSCRIBE':'fund_product','FUND_REDEEM':'fund_product','FUND_CANCEL':'fund_order','FUND_CONFIRM':'fund_order','CARD_REFUND':'credit_entry','GOLD_SAVINGS_BUY':'savings'}
 resource=unique('resource','extension') if op in needed else None
 if resource and resource['kind']!=needed[op]:raise Clarify('RESOURCE_TYPE','other','业务对象类型不匹配，请重新选择。')
 n={'task_type':'FINANCE_MANAGEMENT','operation':op,'needs_clarification':False};draft={'type':op.lower()}
 if op=='GOLD_SAVINGS_BUY':return [{'normalized':n|{'operation':'GOLD_BUY','savings_prepare':{'plan_id':resource['id'],'due':resource['due']}}}]
 if op in {'DEPOSIT_WITHDRAW','DEPOSIT_RENEW'}:draft['deposit_id']=resource['id']
 else:
  if resource and resource.get('card_id'):draft['card_id']=resource['card_id']
  else:draft['card_id']=(unique('card','card') if 'card' in a else v._single_card())['id']
 if op in {'DEPOSIT_OPEN','FUND_SUBSCRIBE','CARD_REFUND'}:
  if 'amount' not in a:raise Clarify('AMOUNT_REQUIRED','amount','请输入本次金额。')
  draft['amount']=v.amount(a['amount'])
 if op=='DEPOSIT_OPEN':
  if 'term' not in a:raise Clarify('TERM_REQUIRED','other','选择存期：90天、180天或365天。')
  v.span(a['term']['span'],'other','存期');m=re.fullmatch(r'(90|180|365)天',_norm(a['term']['span']))
  if not m or int(m[1])!=a['term']['value']:raise Clarify('TERM_UNGROUNDED','other','请按天数说明存期。')
  draft['term']=a['term']['value']
 if op in {'FX_BUY','FX_SELL'}:
  currency=a.get('currency');names={'USD':['美元','美金','USD'],'EUR':['欧元','EUR'],'JPY':['日元','JPY'],'HKD':['港币','港元','HKD']}
  if currency not in names or not any(name in latest for name in names[currency]):raise Clarify('CURRENCY_REQUIRED','other','请明确币种：美元、欧元、日元或港币。')
  draft.update(currency=currency,quote_mode='reference') # Model cannot select fictional practice rates.
 if op in {'FX_BUY','FX_SELL','FUND_REDEEM'}:
  if 'quantity' not in a:raise Clarify('QUANTITY_REQUIRED','other','请说明外币数量或赎回份额。')
  if op=='FUND_REDEEM':
   q=a['quantity'];v.span(q['span'],'other','份额');span=q['span'].strip().removesuffix('份').strip()
   try:
    value=Decimal(str(q['value']))
    valid=bool(re.fullmatch(r'[0-9]+(?:\.[0-9]{1,4})?',span)) and value==Decimal(span) and 0<value<=100000000
   except InvalidOperation:valid=False
   if not valid:raise Clarify('QUANTITY_UNGROUNDED','other','请用数字填写赎回份额，最多4位小数。')
   draft['units']=int(value*10000)
  else:draft['units']=v.amount(a['quantity'])
 if op in {'FUND_SUBSCRIBE','FUND_REDEEM'}:draft['product_id']=resource['id']
 if op in {'FUND_CANCEL','FUND_CONFIRM'}:draft['order_id']=resource['id']
 if op=='CARD_REFUND':draft['entry_id']=resource['id']
 if op=='CARD_REPAY':
  mode=a.get('repay_mode');pattern=r'全部|全额|还清' if mode=='full' else r'最低'
  if mode not in {'full','minimum'} or not re.search(pattern,latest):raise Clarify('REPAY_MODE_REQUIRED','other','请说明全额还款还是最低还款。')
  draft['repay_mode']=mode
 return [{'normalized':n|{'finance_draft':draft}}]

def rule(text,catalog):
 """Conservative fallback: asks for missing fields via the very same validator."""
 if re.search(r'打开|进入|查看|查询',text):return None
 op=None
 for pattern,candidate in [(r'确认.*基金.*份额|确认份额','FUND_CONFIRM'),(r'基金.*撤单|撤销申购|取消申购','FUND_CANCEL'),(r'赎回.*基金|基金.*赎回','FUND_REDEEM'),(r'(申购|买入).*基金','FUND_SUBSCRIBE'),(r'信用.*(全额|最低|还清).*还款|信用.*还清|信用.*还款','CARD_REPAY'),(r'信用.*退款','CARD_REFUND'),(r'(退还|取回).*溢缴','CARD_SURPLUS'),(r'到期续存','DEPOSIT_RENEW'),(r'(支取|取出).*存单|存单.*支取','DEPOSIT_WITHDRAW'),(r'存入|存定期|开立.*存款','DEPOSIT_OPEN'),(r'卖出.*(美元|欧元|日元|港币)|结汇|换回人民币','FX_SELL'),(r'(买入|购买|兑换|购汇).*(美元|欧元|日元|港币)','FX_BUY'),(r'(执行|办理).*定投','GOLD_SAVINGS_BUY')]:
  if re.search(pattern,text):op=candidate;break
 if not op:return None
 a={'operation':op}
 for key,items in [('resource',catalog.get('extensions',[])),('card',catalog.get('cards',[]))]:
  matched=[(x,x['id'] if x['id'] in text else x['display'].split(' ')[0]) for x in items if x['id'] in text or x['display'].split(' ')[0] in text]
  if len(matched)==1:a[key]={'id':matched[0][0]['id'],'span':matched[0][1]}
 numbers=list(re.finditer(r'(?<![0-9.])([0-9]+(?:\.[0-9]{1,4})?)\s*(元|份|美元|欧元|日元|港币)',text))
 if numbers:
  m=numbers[-1];a['quantity' if op in {'FX_BUY','FX_SELL','FUND_REDEEM'} else 'amount']={'value':m[1],'span':m[1]}
 term=re.search(r'(90|180|365)天',text)
 if term:a['term']={'value':int(term[1]),'span':term[0]}
 for word,code in [('美元','USD'),('欧元','EUR'),('日元','JPY'),('港币','HKD')]:
  if word in text:a['currency']=code
 if op=='CARD_REPAY':a['repay_mode']='minimum' if '最低' in text else 'full' if re.search('全额|全部|还清',text) else None
 if a.get('repay_mode') is None:a.pop('repay_mode',None)
 return [{'name':'propose_extension','arguments':a}]
