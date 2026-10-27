"""Virtual deposits and FX using Finance's signed, independently verified ledger path.
Rates below are explicit exercise terms, not real bank offers. No live banking API.
"""
import copy,json,time,calendar
from datetime import datetime,timedelta,date
from decimal import Decimal,ROUND_HALF_UP,ROUND_DOWN
from backend.domain import Rejected,canonical,uid
from backend.business import CHINA

LABELS={'DEPOSIT_OPEN':'开立虚拟定期存款','DEPOSIT_WITHDRAW':'支取虚拟定期存款','DEPOSIT_RENEW':'到期续存','FX_BUY':'购入虚拟外币','FX_SELL':'售出虚拟外币'}
INCOMING={'DEPOSIT_WITHDRAW','DEPOSIT_RENEW','FX_SELL'}
RATES={90:80,180:100,365:130}
PRACTICE={'USD':'7.00','EUR':'8.00','HKD':'0.90','JPY':'0.05'}
SCHEMA='''
CREATE TABLE IF NOT EXISTS term_deposits(id TEXT PRIMARY KEY,customer_id TEXT NOT NULL,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fx_wallets(customer_id TEXT NOT NULL,account_id TEXT NOT NULL,currency TEXT NOT NULL,units INTEGER NOT NULL CHECK(units>=0),cost INTEGER NOT NULL CHECK(cost>=0),version INTEGER NOT NULL,PRIMARY KEY(customer_id,account_id,currency));
CREATE TABLE IF NOT EXISTS gold_savings_plans(id TEXT PRIMARY KEY,customer_id TEXT NOT NULL,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gold_savings_runs(plan_id TEXT NOT NULL,due TEXT NOT NULL,contract_id TEXT NOT NULL,PRIMARY KEY(plan_id,due));
'''
def rounded(n):return int(Decimal(str(n)).quantize(Decimal(1),rounding=ROUND_HALF_UP))
def integer(n,lo,hi):
 if type(n) is not int or not lo<=n<=hi:raise Rejected('INVALID_INPUT')
 return n
def owned(c,table,owner,key):
 r=c.execute('SELECT body FROM '+table+' WHERE id=? AND customer_id=?',(key,owner)).fetchone()
 if not r:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
 return json.loads(r['body'])
def snapshots(c,owner,t):
 d=None;w=None;plan=None;run=None
 if t['type'].startswith('deposit_'):
  r=c.execute('SELECT body FROM term_deposits WHERE id=? AND customer_id=?',(t.get('deposit_id',t['id']),owner)).fetchone();d=json.loads(r['body']) if r else None
 if t['type'].startswith('fx_'):
  r=c.execute('SELECT * FROM fx_wallets WHERE customer_id=? AND account_id=? AND currency=?',(owner,t['account_id'],t['foreign_currency'])).fetchone()
  w=dict(r) if r else dict(customer_id=owner,account_id=t['account_id'],currency=t['foreign_currency'],units=0,cost=0,version=0)
 if t.get('savings_plan'):
  plan=owned(c,'gold_savings_plans',owner,t['savings_plan'])
  r=c.execute('SELECT * FROM gold_savings_runs WHERE plan_id=? AND due=?',(plan['id'],t['savings_due'])).fetchone();run=dict(r) if r else None
 return {'deposit':d,'wallet':w,'savings_plan':plan,'savings_run':run}
def interest(d,at):
 days=max(0,min(d['term'],(datetime.fromtimestamp(at,CHINA).date()-date.fromisoformat(d['opened'])).days))
 rate=d['rate_bps'] if datetime.fromtimestamp(at,CHINA).date()>=date.fromisoformat(d['matures']) else 20
 return d['principal']*rate*days//3650000,days,rate
def errors(kind,s,p,now):
 t=s['target'];out=[]
 if t['type']!=kind.lower() or t['status']!='DRAFT':out.append('FINANCE_STATE_INVALID')
 if now>=t['expires_at']:out.append('QUOTE_EXPIRED')
 if kind=='DEPOSIT_OPEN' and s['deposit']:out.append('DEPOSIT_EXISTS')
 if kind=='DEPOSIT_OPEN' and t['opened']!=datetime.fromtimestamp(now,CHINA).date().isoformat():out.append('QUOTE_EXPIRED')
 if kind=='DEPOSIT_RENEW' and t['renew_opened']!=datetime.fromtimestamp(now,CHINA).date().isoformat():out.append('QUOTE_EXPIRED')
 if kind in {'DEPOSIT_WITHDRAW','DEPOSIT_RENEW'}:
  d=s['deposit']
  if not d or d['status']!='ACTIVE':out.append('DEPOSIT_NOT_ACTIVE')
  elif interest(d,now)!=(t['interest'],t['days'],t['applied_bps']):out.append('QUOTE_EXPIRED')
  if kind=='DEPOSIT_RENEW' and d and datetime.fromtimestamp(now,CHINA).date()<date.fromisoformat(d['matures']):out.append('DEPOSIT_NOT_MATURE')
 if kind=='FX_SELL' and s['wallet']['units']<t['units']:out.append('INSUFFICIENT_HOLDING')
 return out
def recurring_errors(s):
 t=s['target'];p=s.get('savings_plan');r=s.get('savings_run')
 if t.get('savings_plan') and (not p or p['status']!='ACTIVE' or p['version']!=t['savings_version'] or not r or r['contract_id']!=t['id']):return ['SAVINGS_PLAN_CHANGED']
 return []
def expected(kind,s,p):
 r=copy.deepcopy(s);t=r['target'];amount=p['amount']
 r['account']['balance']+=amount*(1 if kind in INCOMING else -1);r['account']['version']+=1;r['credit']['version']+=1
 t['status']='COMPLETED';t['version']+=1
 if kind=='DEPOSIT_OPEN':
  r['deposit']={k:t[k] for k in ('id','customer_id','account_id','card_id','principal','term','rate_bps','opened','matures')}
  r['deposit'].update(status='ACTIVE',version=0,earned=0,cycles=1)
 elif kind in {'DEPOSIT_WITHDRAW','DEPOSIT_RENEW'}:
  d=r['deposit'];d['earned']+=t['interest'];d['version']+=1
  if kind=='DEPOSIT_WITHDRAW':d['status']='CLOSED'
  else:d.update(opened=t['renew_opened'],matures=t['renew_matures'],cycles=d['cycles']+1)
 else:
  w=r['wallet'];w['version']+=1
  if kind=='FX_BUY':w['units']+=t['units'];w['cost']+=amount
  else:
   cost=w['cost']*t['units']//w['units'] if w['units'] else 0;w['units']-=t['units'];w['cost']-=cost;t['realized_profit']=amount-cost
 return r
def apply(c,action,before,after,opid):
 d=after.get('deposit');w=after.get('wallet')
 if d:c.execute('INSERT INTO term_deposits VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',(d['id'],d['customer_id'],canonical(d)))
 if w:c.execute('INSERT INTO fx_wallets VALUES(?,?,?,?,?,?) ON CONFLICT(customer_id,account_id,currency) DO UPDATE SET units=excluded.units,cost=excluded.cost,version=excluded.version',tuple(w[k] for k in ('customer_id','account_id','currency','units','cost','version')))
def fields(t):
 from backend.chat.amounts import format_cents
 if t['type'].startswith('deposit_'):
  out=[{'label':'存款本金','value':format_cents(t['principal'])},{'label':'期限','value':str(t['term'])+' 天'},{'label':'产品年利率','value':str(t['rate_bps']/100)+'%（虚拟产品）'}]
  if t['type']!='deposit_open':out += [{'label':'计息天数 / 实际年利率','value':f"{t['days']} 天 / {t['applied_bps']/100}%"},{'label':'利息','value':format_cents(t['interest'])},{'label':'到期处理','value':'利息回原卡，本金原额续存同期限' if t['type']=='deposit_renew' else '本金与利息回原卡，存单关闭'}]
  else:out += [{'label':'到期日','value':t['matures']},{'label':'持有到期利息','value':format_cents(t['principal']*t['rate_bps']*t['term']//3650000)},{'label':'提前支取','value':'整笔支取，按实际天数及虚拟活期年利率 0.20% 计算；不自动续存'}]
  if t['type']=='deposit_renew':out += [{'label':'续存起止日期','value':t['renew_opened']+' 至 '+t['renew_matures']}]
  return out
 return [{'label':'外币金额','value':f"{t['units']/100:.2f} {t['foreign_currency']}"},{'label':'汇率','value':f"1 {t['foreign_currency']} = {t['rate']} 人民币"},{'label':'来源','value':t['quote']['source']+' · '+t['quote']['as_of']},{'label':'手续费','value':'0 元；仅虚拟兑换，不是银行实盘汇价'}]

class Extensions:
 def __init__(self,chat):
  from backend.banking.finance import Finance
  self.chat=chat;self.s=chat.s;self.f=Finance(chat)
 def view(self,owner):
  from backend.banking.retail import Retail
  return {'deposits':[json.loads(r['body']) for r in self.s.db.rows('SELECT body FROM term_deposits WHERE customer_id=?',(owner,))],
   'wallets':self.s.db.rows('SELECT * FROM fx_wallets WHERE customer_id=?',(owner,)),
   'contracts':[t for t in self.f.view(owner)['contracts'] if t['type'].startswith(('deposit_','fx_'))],
   'cards':Retail(self.chat).view(owner)['cards'],'deposit_rates':RATES,'currencies':list(PRACTICE)}
 def create(self,owner,b):
  from backend.banking.finance import card_label
  typ=b.get('type');allowed={'deposit_open':{'type','card_id','amount','term'},'deposit_withdraw':{'type','deposit_id'},'deposit_renew':{'type','deposit_id'},'fx_buy':{'type','card_id','currency','units','quote_mode'},'fx_sell':{'type','card_id','currency','units','quote_mode'}}
  if typ not in allowed or set(b)!=allowed[typ]:raise Rejected('INVALID_INPUT')
  now=time.time();today=datetime.fromtimestamp(now,CHINA).date();extras={};quote=None
  if typ=='deposit_open':
   amount=integer(b['amount'],10000,100000000)
   if type(b['term']) is not int or b['term'] not in RATES:raise Rejected('INVALID_INPUT')
   extras=dict(principal=amount,term=b['term'],rate_bps=RATES[b['term']],opened=today.isoformat(),matures=(today+timedelta(days=b['term'])).isoformat())
  elif typ.startswith('fx_'):
   if b['currency'] not in PRACTICE or b['quote_mode'] not in {'reference','practice'}:raise Rejected('INVALID_INPUT')
   units=integer(b['units'],1,100000000)
   if b['quote_mode']=='practice':rate=PRACTICE[b['currency']];quote={'source':'内置练习汇率（非市场报价）','as_of':today.isoformat(),'mode':'practice'}
   else:
    from backend.chat.http import market
    provider=market(self.chat);provider.refresh()
    q=next((q for q in provider.view()['quotes'] if q['symbol']==b['currency']+'CNY'),None)
    if not q or q['snapshot'] or q['stale']:raise Rejected('FX_QUOTE_UNAVAILABLE')
    rate=str(Decimal(str(q['price']))/(100 if b['currency']=='JPY' else 1));quote=q|{'mode':'reference'}
   amount=rounded(Decimal(rate)*units)
   if amount<1 or amount>100000000:raise Rejected('INVALID_INPUT')
   extras=dict(units=units,foreign_currency=b['currency'],rate=rate)
  with self.s.db.transaction() as c:
   if typ in {'deposit_withdraw','deposit_renew'}:
    d=owned(c,'term_deposits',owner,b['deposit_id'])
    if d['status']!='ACTIVE':raise Rejected('DEPOSIT_NOT_ACTIVE')
    if typ=='deposit_renew' and today<date.fromisoformat(d['matures']):raise Rejected('DEPOSIT_NOT_MATURE')
    earned,days,rate=interest(d,now);amount=earned+(d['principal'] if typ=='deposit_withdraw' else 0)
    extras={k:d[k] for k in ('principal','term','rate_bps')};extras.update(deposit_id=d['id'],interest=earned,days=days,applied_bps=rate,renew_opened=today.isoformat(),renew_matures=(today+timedelta(days=d['term'])).isoformat())
    card_id=d['card_id']
   else:card_id=b['card_id']
   card=c.execute('SELECT * FROM cards WHERE customer_id=? AND id=?',(owner,card_id)).fetchone()
   if not card:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
   if card['card_type']!='DEBIT':raise Rejected('DEBIT_CARD_REQUIRED')
   key='fin-'+uid();t=dict(id=key,customer_id=owner,account_id=card['account_id'],card_id=card_id,card_label=card_label(self.s.db,owner,card_id),type=typ,status='DRAFT',version=0,amount=amount,remaining=0,paid_fees=0,source_id='',schedule=[],created_at=now,expires_at=now+120,units=0,price=0,quote=quote,realized_profit=0)|extras
   c.execute('INSERT INTO finance_contracts VALUES(?,?,?)',(key,owner,canonical(t)))
  return {'contract':t}

def occurrence(start,cycle,index):
 d=date.fromisoformat(start)
 if cycle=='weekly':return d+timedelta(days=7*index)
 y,m=divmod(d.year*12+d.month-1+index,12);m+=1
 return date(y,m,min(d.day,calendar.monthrange(y,m)[1]))

class GoldSavings:
 def __init__(self,chat):self.chat=chat;self.s=chat.s
 def view(self,owner):
  today=datetime.now(CHINA).date();plans=[]
  for row in self.s.db.rows('SELECT body FROM gold_savings_plans WHERE customer_id=?',(owner,)):
   p=json.loads(row['body']);p['runs']=[]
   for i in range(p['count']):
    due=occurrence(p['start'],p['cycle'],i).isoformat()
    row=self.s.db.one('SELECT f.body FROM gold_savings_runs r JOIN finance_contracts f ON f.id=r.contract_id WHERE r.plan_id=? AND r.due=? AND f.customer_id=?',(p['id'],due,owner))
    t=json.loads(row['body']) if row else None
    p['runs'].append({'due':due,'contract':t,'state':'COMPLETED' if t and t['status']=='COMPLETED' else 'DUE' if due<=today.isoformat() else 'SCHEDULED'})
   plans.append(p)
  return {'plans':plans}
 def create(self,owner,b):
  if set(b)!={'name','card_id','amount','cycle','start','count'} or not isinstance(b['name'],str) or not 1<=len(b['name'].strip())<=30 or b['cycle'] not in {'weekly','monthly'}:raise Rejected('INVALID_INPUT')
  integer(b['amount'],1000,1000000);integer(b['count'],1,12)
  try:d=date.fromisoformat(b['start'])
  except (ValueError,TypeError):raise Rejected('INVALID_INPUT')
  if not datetime.now(CHINA).date()<=d<=datetime.now(CHINA).date()+timedelta(days=366):raise Rejected('INVALID_DATE')
  with self.s.db.transaction() as c:
   if not c.execute("SELECT id FROM cards WHERE id=? AND customer_id=? AND card_type='DEBIT'",(b['card_id'],owner)).fetchone():raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
   if c.execute('SELECT COUNT(*) FROM gold_savings_plans WHERE customer_id=?',(owner,)).fetchone()[0]>=50:raise Rejected('PLAN_LIMIT')
   p=dict(b,id='goldplan-'+uid(),customer_id=owner,status='ACTIVE',version=0);c.execute('INSERT INTO gold_savings_plans VALUES(?,?,?)',(p['id'],owner,canonical(p)))
  return {'plan':p}
 def update(self,owner,b):
  if set(b)!={'plan_id','status'} or b['status'] not in {'ACTIVE','PAUSED','CANCELLED'}:raise Rejected('INVALID_INPUT')
  with self.s.db.transaction() as c:
   p=owned(c,'gold_savings_plans',owner,b['plan_id'])
   if p['status']=='CANCELLED':raise Rejected('PLAN_CANCELLED')
   p['status']=b['status'];p['version']+=1;c.execute('UPDATE gold_savings_plans SET body=? WHERE id=?',(canonical(p),p['id']))
  return {'plan':p}
 def prepare(self,owner,b):
  from backend.banking.finance import Finance
  if set(b)!={'plan_id','due'}:raise Rejected('INVALID_INPUT')
  with self.s.db.transaction() as c:p=owned(c,'gold_savings_plans',owner,b['plan_id'])
  if p['status']!='ACTIVE':raise Rejected('SAVINGS_PLAN_CHANGED')
  if b['due'] not in [occurrence(p['start'],p['cycle'],i).isoformat() for i in range(p['count'])] or b['due']>datetime.now(CHINA).date().isoformat():raise Rejected('PLAN_NOT_DUE')
  f=Finance(self.chat)
  with self.s.db.transaction() as c:
   row=c.execute('SELECT contract_id FROM gold_savings_runs WHERE plan_id=? AND due=?',(p['id'],b['due'])).fetchone()
   if row:
    old=owned(c,'finance_contracts',owner,row['contract_id'])
    if old['status']=='COMPLETED':raise Rejected('PLAN_ALREADY_EXECUTED')
    if old['status']=='DRAFT' and old['expires_at']>time.time() and old['savings_version']==p['version']:return {'contract':old}
  from backend.chat.http import market
  provider=market(self.chat)
  if hasattr(provider,'refresh'):provider.refresh()
  q=next((q for q in provider.view()['quotes'] if q['symbol']=='XAU'),None)
  if not q or q['snapshot'] or q['stale']:raise Rejected('GOLD_QUOTE_UNAVAILABLE')
  units=int((Decimal(p['amount'])/Decimal(str(q['price']))).to_integral_value(rounding=ROUND_DOWN))
  if units<1:raise Rejected('AMOUNT_BELOW_GOLD_UNIT')
  t=f.create(owner,{'type':'gold_buy','card_id':p['card_id'],'units':units})['contract']
  if t['amount']>p['amount']:f.archive(owner,{'contract_id':t['id']});raise Rejected('QUOTE_CHANGED')
  try:
   with self.s.db.transaction() as c:
    current=owned(c,'gold_savings_plans',owner,p['id'])
    if current['version']!=p['version'] or current['status']!='ACTIVE':raise Rejected('SAVINGS_PLAN_CHANGED')
    row=c.execute('SELECT contract_id FROM gold_savings_runs WHERE plan_id=? AND due=?',(p['id'],b['due'])).fetchone()
    if row:
     old=owned(c,'finance_contracts',owner,row['contract_id'])
     if old['status']=='COMPLETED':raise Rejected('PLAN_ALREADY_EXECUTED')
     if old['status']=='DRAFT' and old['expires_at']>time.time() and old['savings_version']==p['version']:
      t['status']='ARCHIVED';c.execute('UPDATE finance_contracts SET body=? WHERE id=?',(canonical(t),t['id']));return {'contract':old}
     old['status']='ARCHIVED';old['version']+=1;c.execute('UPDATE finance_contracts SET body=? WHERE id=?',(canonical(old),old['id']))
    t.update(savings_plan=p['id'],savings_due=b['due'],savings_version=p['version']);c.execute('UPDATE finance_contracts SET body=? WHERE id=?',(canonical(t),t['id']))
    c.execute('INSERT INTO gold_savings_runs VALUES(?,?,?) ON CONFLICT(plan_id,due) DO UPDATE SET contract_id=excluded.contract_id',(p['id'],b['due'],t['id']))
  except Exception:
   f.archive(owner,{'contract_id':t['id']});raise
  return {'contract':t}
