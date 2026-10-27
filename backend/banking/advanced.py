"""Credit statements and unit-based practice funds: bank rules (used by the sandbox bank's desk) and TRUSTA's view.

All amounts are integer CNY cents. Fund units and NAV use 4 decimal places.
No external bank, merchant, market or model calls. Non-cash entries use distinct
ledger accounts and never masquerade as changes to a deposit balance.
"""
import copy,json,time,calendar
from datetime import datetime,timedelta,date
from backend.business import CHINA
from backend.domain import Rejected,canonical,uid
from backend.banking.extensions import integer

LABELS={'CARD_CHARGE':'登记虚拟信用消费','CARD_REFUND':'信用消费退款','CARD_REPAY':'偿还信用账单','CARD_SURPLUS':'退还信用溢缴款',
        'FUND_SUBSCRIBE':'基金申购','FUND_CONFIRM':'确认基金份额','FUND_CANCEL':'撤销基金申购','FUND_REDEEM':'按份额赎回基金'}
INCOMING={'CARD_SURPLUS','FUND_CANCEL','FUND_REDEEM'}
NONCASH={'CARD_CHARGE','CARD_REFUND','FUND_CONFIRM'}
SCHEMA='''CREATE TABLE IF NOT EXISTS advanced_positions(customer_id TEXT NOT NULL,card_id TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(customer_id,card_id));'''
LIMIT=2000000
PRODUCTS=[{'id':'practice-bond','name':'稳健债券练习基金','nav':10250,'risk':2,'buy_bps':20,'sell_bps':10},
          {'id':'practice-index','name':'宽基指数练习基金','nav':12500,'risk':3,'buy_bps':50,'sell_bps':20}]
def product(key):
 p=next((p for p in PRODUCTS if p['id']==key),None)
 if not p:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
 return p
def empty(owner,card):return dict(customer_id=owner,card_id=card,version=0,entries=[],orders=[],holdings={})
def state(c,owner,card):
 r=c.execute('SELECT body FROM advanced_positions WHERE customer_id=? AND card_id=?',(owner,card)).fetchone()
 return json.loads(r['body']) if r else empty(owner,card)
def debt(s):return sum(e['amount']-e['refunded']-e['paid'] for e in s['entries'])
def remaining(e):return max(0,e['amount']-e['refunded']-e['paid'])
def dates(at):
 d=datetime.fromtimestamp(at,CHINA).date()
 # Credit purchase posts next calendar day. Statement closes on the fifth.
 posted=d+timedelta(days=1);y,m=posted.year,posted.month
 if posted.day>5:y,m=divmod(y*12+m,12);m+=1
 closing=date(y,m,5)
 return str(posted),str(closing),str(date(y,m,25))
def statements(s,at):
 today=str(datetime.fromtimestamp(at,CHINA).date());groups={}
 for e in s['entries']:
  g=groups.setdefault(e['statement'],dict(date=e['statement'],due=e['due'],total=0,outstanding=0,minimum=0,status='UNBILLED' if e['statement']>today else 'BILLED'))
  net=max(0,e['amount']-e['refunded']);g['total']+=net;g['outstanding']+=remaining(e)
  g['minimum']+=max(0,(net+9)//10-e['paid'])
 for g in groups.values():
  g['minimum']=min(g['minimum'],g['outstanding'])
  if not g['outstanding']:g['status']='PAID'
  elif g['due']<today:g['status']='OVERDUE'
 return sorted(groups.values(),key=lambda g:g['date'])
def snapshots(c,owner,t):
 if t['type'].upper() not in LABELS:return {}
 return {'advanced':state(c,owner,t['card_id'])}
def cash(kind,amount):return 0 if kind in NONCASH else amount*(1 if kind in INCOMING else -1)
def ledger(action):
 kind=action['action_type'];amount=action['parameters']['amount'];t=action['preconditions']['target']
 if kind=='FUND_CONFIRM':a,b='fund-pending:'+t['card_id'],'fund-units:'+t['card_id'];incoming=False
 elif kind in {'CARD_CHARGE','CARD_REFUND'}:a,b='credit-liability:'+t['card_id'],'virtual-merchant';incoming=kind=='CARD_REFUND'
 else:a,b=action['source_account_id'],'finance-clearing';incoming=kind in INCOMING
 return {(a,'CREDIT' if incoming else 'DEBIT',amount),(b,'DEBIT' if incoming else 'CREDIT',amount)}
def errors(kind,s,p,now):
 t=s['target'];v=s['advanced'];out=[]
 if t['type'].upper()!=kind or t['status']!='DRAFT':out.append('FINANCE_STATE_INVALID')
 if now>=t['expires_at']:out.append('QUOTE_EXPIRED')
 if t['state_version']!=v['version']:out.append('STALE_PRECONDITION')
 if kind=='CARD_CHARGE' and debt(v)+t['amount']>LIMIT:out.append('CREDIT_LIMIT_EXCEEDED')
 if kind=='CARD_CHARGE' and t['trade_date']!=str(datetime.fromtimestamp(now,CHINA).date()):out.append('QUOTE_EXPIRED')
 if kind=='CARD_REPAY' and (t['amount']>max(0,debt(v)) or t['amount']<=0):out.append('FINANCE_STATE_INVALID')
 if kind=='CARD_REPAY' and t['trade_date']!=str(datetime.fromtimestamp(now,CHINA).date()):out.append('QUOTE_EXPIRED')
 if kind=='CARD_SURPLUS' and t['amount']!=max(0,-debt(v)):out.append('FINANCE_STATE_INVALID')
 if kind=='CARD_REFUND':
  e=next((e for e in v['entries'] if e['id']==t['entry_id']),None)
  if not e or t['amount']>e['amount']-e['refunded']:out.append('REFUND_EXCEEDS_PURCHASE')
 if kind in {'FUND_SUBSCRIBE','FUND_CONFIRM'}:
  r=s['risk_profile'];pr=product(t['product_id'])
  if not r or not r['assessed_at']<=now<r['expires_at']:out.append('RISK_PROFILE_REQUIRED')
  elif r['risk_level']<pr['risk']:out.append('SUITABILITY_RISK_MISMATCH')
 if kind in {'FUND_CANCEL','FUND_CONFIRM'}:
  o=next((o for o in v['orders'] if o['id']==t['order_id']),None)
  if not o or o['status']!='PENDING':out.append('FUND_ORDER_NOT_PENDING')
 if kind=='FUND_REDEEM' and t['units']>v['holdings'].get(t['product_id'],{}).get('units',0):out.append('INSUFFICIENT_HOLDING')
 return out
def expected(kind,s,p):
 r=copy.deepcopy(s);t=r['target'];v=r['advanced'];amount=t['amount'];v['version']+=1
 t['status']='COMPLETED';t['version']+=1;r['credit']['version']+=1
 delta=cash(kind,amount)
 if delta:r['account']['balance']+=delta;r['account']['version']+=1
 if kind=='CARD_CHARGE':
  v['entries'].append(dict(id=t['id'],amount=amount,refunded=0,paid=0,merchant=t['merchant'],at=t['created_at'],posted=t['posted'],statement=t['statement'],due=t['due']))
  # Apply any refund-generated surplus to new purchases, oldest surplus first.
  new=v['entries'][-1]
  for e in v['entries'][:-1]:
   surplus=max(0,e['paid']+e['refunded']-e['amount']);use=min(surplus,remaining(new));e['paid']-=use;new['paid']+=use
 elif kind=='CARD_REFUND':next(e for e in v['entries'] if e['id']==t['entry_id'])['refunded']+=amount
 elif kind=='CARD_REPAY':
  left=amount
  for e in sorted(v['entries'],key=lambda e:(e['statement'],e['at'],e['id'])):
   use=min(left,remaining(e));e['paid']+=use;left-=use
 elif kind=='CARD_SURPLUS':
  for e in v['entries']:e['paid']=min(e['paid'],max(0,e['amount']-e['refunded']))
 elif kind=='FUND_SUBSCRIBE':
  v['orders'].append({k:t[k] for k in ('id','product_id','amount','fee','units','nav','created_at')}|{'status':'PENDING'})
 elif kind in {'FUND_CONFIRM','FUND_CANCEL'}:
  o=next(o for o in v['orders'] if o['id']==t['order_id']);o['status']='CONFIRMED' if kind=='FUND_CONFIRM' else 'CANCELLED'
  if kind=='FUND_CONFIRM':
   h=v['holdings'].setdefault(t['product_id'],{'units':0,'cost':0});h['units']+=t['units'];h['cost']+=amount-t['fee'];t['paid_fees']=t['fee']
 else:
  h=v['holdings'].setdefault(t['product_id'],{'units':0,'cost':0});cost=h['cost']*t['units']//h['units'] if h['units'] else 0;h['units']-=t['units'];h['cost']-=cost;t['realized_profit']=amount-cost;t['paid_fees']=t['fee']
 if kind in {'CARD_REFUND','CARD_CHARGE'}:
  for source in v['entries']:
   for destination in sorted(v['entries'],key=lambda e:(e['statement'],e['at'],e['id'])):
    credit=max(0,source['paid']+source['refunded']-source['amount']);use=min(credit,remaining(destination))
    source['paid']-=use;destination['paid']+=use
 return r
def apply(c,action,before,after,opid):
 v=after['advanced'];c.execute('INSERT INTO advanced_positions VALUES(?,?,?) ON CONFLICT(customer_id,card_id) DO UPDATE SET body=excluded.body',(v['customer_id'],v['card_id'],canonical(v)))
 # Credit charges are expenses even though no cash leaves the deposit account.
 if action['action_type'] in {'CARD_CHARGE','CARD_REFUND'}:
  t=after['target'];c.execute('INSERT INTO consumption_records VALUES(?,?,?,?,?,?,?,?,?)',(opid,t['account_id'],t['merchant'],'信用消费',t['amount'],'CNY',time.time(),'PURCHASE' if action['action_type']=='CARD_CHARGE' else 'REFUND','SETTLED'))
def fields(t):
 from backend.chat.amounts import format_cents as money
 if t['type'].startswith('card_'):
  f=[{'label':'信用账户','value':'绑定此卡的独立虚拟信用账户；额度 20,000 元'}, {'label':'本次说明','value':{'card_charge':'登记测试消费，不向商户付款；借记卡余额不变','card_refund':'退回信用账户并恢复额度，超额部分形成溢缴款','card_repay':'从绑定借记卡扣款，按账单先后冲抵欠款','card_surplus':'溢缴款退回绑定借记卡'}[t['type']]}]
  if t.get('statement'):f += [{'label':'账单日 / 到期还款日','value':t['statement']+' / '+t['due']}]
  if t.get('repay_mode'):f += [{'label':'还款方式','value':'全部欠款' if t['repay_mode']=='full' else '已出账最低还款（10%）'}]
  return f+[{'label':'虚拟产品规则','value':'每月5日出账、25日还款；次日入账。最低还款不结清剩余欠款，本练习产品不计循环利息/逾期费；不代表真实信用卡规则。'}]
 return [{'label':'基金','value':product(t['product_id'])['name']},{'label':'测试净值','value':f"{t['nav']/10000:.4f} 元/份（固定练习值，非市场行情）"},{'label':'份额','value':f"{t['units']/10000:.4f} 份"},{'label':'手续费','value':money(0 if t['type']=='fund_cancel' else t['fee'])},{'label':'规则','value':'申购扣款后待确认份额；确认前可全额撤单。确认份额不再扣款；赎回按固定测试净值结算到原卡，不模拟真实T+1。'}]

class Advanced:
 """TRUSTA side: the credit account and practice funds through the bank interface."""
 def __init__(self,chat):
  from backend.banking.finance import Finance
  self.chat=chat;self.s=chat.s;self.f=Finance(chat)
 def view(self,owner,state=None):
  from backend.banking.retail import Retail
  st=state or self.f.state(owner)
  cards=[c for c in Retail(self.chat).view(owner)['cards'] if c['card_type']=='DEBIT']
  return {'cards':cards,'states':st['credit_states'],'products':st['fund_products'],'limit':st['credit_card_limit'],
          'contracts':self.f.label(owner,[t for t in st['contracts'] if t['type'].upper() in LABELS][:100])}
 def create(self,owner,b):
  if not isinstance(b,dict) or str(b.get('type','')).upper() not in LABELS:raise Rejected('INVALID_INPUT')
  return self.f.create(owner,b)
