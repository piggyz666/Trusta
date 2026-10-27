"""Virtual retail orders. The six-agent executor is the sole writer of money."""
import json,time,copy
from backend.domain import Rejected,uid,canonical
SCHEMA='''CREATE TABLE IF NOT EXISTS retail_orders(id TEXT PRIMARY KEY,customer_id TEXT NOT NULL,account_id TEXT NOT NULL,card_id TEXT NOT NULL,title TEXT NOT NULL,category TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),currency TEXT NOT NULL,status TEXT NOT NULL,version INTEGER NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL,detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS card_designs(card_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL,theme TEXT NOT NULL,nickname TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS retail_task_links(task_id TEXT PRIMARY KEY,order_id TEXT NOT NULL,customer_id TEXT NOT NULL);'''
CATALOG=[{'id':'wash','title':'标准洗车体验','category':'汽车服务','amount':3500}, {'id':'gift','title':'生日花束体验','category':'商城','amount':9900}, {'id':'movie','title':'电影权益体验','category':'生活权益','amount':3900}, {'id':'electric','title':'电费缴费体验','category':'生活缴费','amount':10000}, {'id':'mobile','title':'话费充值体验','category':'生活缴费','amount':5000}]

def order(conn,owner,oid):
 r=conn.execute('SELECT * FROM retail_orders WHERE id=? AND customer_id=?',(oid,owner)).fetchone()
 if not r:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
 result=dict(r)
 pid=json.loads(result["detail"]).get("plan_id")
 if pid:
  from backend.banking.personal import plan_snapshot
  result["plan"]=plan_snapshot(conn,owner,pid)
 return result

def snapshot(conn,owner,oid):
 target=order(conn,owner,oid)
 card=conn.execute('SELECT * FROM cards WHERE id=? AND customer_id=? AND account_id=?',(target['card_id'],owner,target['account_id'])).fetchone()
 if not card:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
 return target,dict(card)

def errors(kind,s,p,now):
 t,c,a=s['target'],s['card'],s['account'];out=[]
 if p!={'amount':t['amount'],'currency':t['currency']}:out.append('ORDER_AMOUNT_MISMATCH')
 if kind=='RETAIL_PAY':
  if t.get('plan',{}).get('binding'):out.append('PLAN_USES_WISH_FUND')
  if t.get('plan') and (t['plan']['status']!='ACTIVE' or t['plan']['spent']+p['amount']>t['plan']['budget']):out.append('PLAN_BUDGET_EXCEEDED_OR_ARCHIVED')
  if t['status']!='PENDING':out.append('ORDER_NOT_PENDING')
  if t['expires_at']<=now:out.append('ORDER_EXPIRED')
  if c['status']!='ACTIVE' or not c['online_enabled']:out.append('CARD_NOT_PAYABLE')
  if p['amount']>c['spending_limit']:out.append('CARD_LIMIT_EXCEEDED')
  if p['amount']>a['balance']:out.append('INSUFFICIENT_BALANCE')
 elif t['status']!='PAID':out.append('ORDER_NOT_REFUNDABLE')
 return out

def expected(kind,s,p):
 result=copy.deepcopy(s);result['account']['balance']+=p['amount']*(-1 if kind=='RETAIL_PAY' else 1)
 result['account']['version']+=1
 result['target']['status']='PAID' if kind=='RETAIL_PAY' else 'REFUNDED'
 result['target']['version']+=1
 if result['target'].get('plan'):
  result['target']['plan']['spent']+=p['amount']*(-1 if kind=='RETAIL_REFUND' else 1)
  result['target']['plan']['version']+=1
 return result

def apply(conn,action,before,after,opid):
 t,a=after['target'],after['account'];amount=t['amount'];pay=action['action_type']=='RETAIL_PAY'
 if t.get('plan'):conn.execute('UPDATE life_plans SET version=? WHERE id=?',(t['plan']['version'],t['plan']['id']))
 conn.execute('UPDATE retail_orders SET status=?,version=? WHERE id=?',(t['status'],t['version'],t['id']))
 conn.execute('UPDATE accounts SET balance=?,version=? WHERE id=?',(a['balance'],a['version'],a['id']))
 balance=conn.execute("SELECT balance FROM accounts WHERE id='settlement'").fetchone()['balance']
 if not pay and balance<amount:raise Rejected('SETTLEMENT_INSUFFICIENT')
 conn.execute("UPDATE accounts SET balance=balance+?,version=version+1 WHERE id='settlement'",(amount if pay else -amount,))
 for aid,side in ((a['id'],'DEBIT' if pay else 'CREDIT'),('settlement','CREDIT' if pay else 'DEBIT')):
  conn.execute('INSERT INTO ledger_entries VALUES(?,?,?,?,?)',(uid(),opid,aid,side,amount))
 conn.execute('INSERT INTO consumption_records VALUES(?,?,?,?,?,?,?,?,?)',(opid,a['id'],t['title'],t['category'],amount,'CNY',time.time(),'PURCHASE' if pay else 'REFUND','SETTLED'))

class Retail:
 def __init__(self,chat):self.chat=chat;self.s=chat.s
 def cards(self,owner):
  """The customer's cards as the bank reports them, with the linked account's balance and TRUSTA's own card face."""
  from backend.bank_sdk.discovery import customer_accounts,optional_read
  accounts={a['id']:a for a in customer_accounts(self.s,owner)}
  cards=[]
  for card in optional_read(self.s,'service',owner,'card.list'):
   a=accounts.get(card.get('account_id'),{})
   card=dict(card)|{'balance':a.get('balance'),'frozen':a.get('frozen'),'currency':a.get('currency','CNY')}
   card['design']=self.s.db.one('SELECT theme,nickname FROM card_designs WHERE card_id=? AND customer_id=?',(card['id'],owner)) or {'theme':'midnight','nickname':''}
   cards.append(card)
  return cards
 def view(self,owner):
  return {'catalog':CATALOG,'cards':self.cards(owner),'orders':self.s.db.rows('SELECT * FROM retail_orders WHERE customer_id=? ORDER BY created_at DESC LIMIT 100',(owner,)),'mode':'virtual'}
 def design(self,owner,body):
  if set(body)!={'card_id','theme','nickname'} or body['theme'] not in {'midnight','champagne','ruby','ocean'} or not isinstance(body['nickname'],str) or len(body['nickname'])>20:raise Rejected('INVALID_INPUT')
  if body['card_id'] not in {c['id'] for c in self.cards(owner)}:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
  with self.s.db.transaction() as c:
   c.execute('INSERT INTO card_designs VALUES(?,?,?,?) ON CONFLICT(card_id) DO UPDATE SET theme=excluded.theme,nickname=excluded.nickname',(body['card_id'],owner,body['theme'],body['nickname']))
  return {'saved':True}
 def create(self,owner,body):
  if set(body) not in ({'product_id','card_id'},{'product_id','card_id','plan_id'}):raise Rejected('INVALID_INPUT')
  if 'plan_id' in body and (not isinstance(body['plan_id'],str) or not body['plan_id']):raise Rejected('INVALID_INPUT')
  item=next((x for x in CATALOG if x['id']==body['product_id']),None)
  if not item:raise Rejected('PRODUCT_NOT_FOUND')
  with self.s.db.transaction() as c:
   card=c.execute('SELECT * FROM cards WHERE id=? AND customer_id=?',(body['card_id'],owner)).fetchone()
   if not card:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
   design=c.execute('SELECT nickname FROM card_designs WHERE card_id=? AND customer_id=?',(card['id'],owner)).fetchone()
   nickname=design['nickname'] if design else ''
   if body.get('plan_id'):
    from backend.banking.personal import plan_snapshot
    plan=plan_snapshot(c,owner,body['plan_id'])
    if plan['status']!='ACTIVE':raise Rejected('PLAN_ARCHIVED')
    if plan.get('binding'):raise Rejected('PLAN_USES_WISH_FUND')
   oid='retail-'+uid();now=time.time()
   c.execute('INSERT INTO retail_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(oid,owner,card['account_id'],card['id'],item['title'],item['category'],item['amount'],'CNY','PENDING',0,now,now+1800,canonical({'plan_name':plan['name'] if body.get('plan_id') else '', 'plan_id':body.get('plan_id'),'payment_card_name':nickname,'source':'内置虚拟商品，不代表真实商户报价','refund_rule':'仅虚拟体验订单，未核销可原路全额退回；不用于真实机票退票'})))
  return {'order_id':oid}
 def propose(self,owner,oid,refund=False):
  if type(refund) is not bool:raise Rejected('INVALID_INPUT')
  with self.s.db.transaction() as c:t=order(c,owner,oid)
  kind='RETAIL_REFUND' if refund else 'RETAIL_PAY'
  task=self.s.banking.create(('退回' if refund else '支付')+t['title'],owner,t['account_id'],{'task_type':'RETAIL_MANAGEMENT','operation':kind,'target_id':oid,'parameters':{'amount':t['amount'],'currency':'CNY'}})
  with self.s.db.transaction() as c:c.execute('INSERT INTO retail_task_links VALUES(?,?,?)',(task['task_id'],oid,owner))
  return self.result(owner,task)
 def result(self,owner,task):
  from backend.chat.cards import CardRenderer
  renderer=CardRenderer(self.s,owner,self.chat.catalog(owner));cards,_=renderer.task_cards(task)
  return {'task_id':task['task_id'],'status':task['status'],'cards':cards,'errors':[r.get('reason') for r in task.get('action_records',[]) if r.get('status') in {'BLOCKED','FAILED'}]}
 def decide(self,owner,body):
  if set(body)!={'task_id','action_id','action_hash','approve'} or type(body['approve']) is not bool:raise Rejected('INVALID_INPUT')
  if not self.s.db.one('SELECT task_id FROM retail_task_links WHERE task_id=? AND customer_id=?',(body['task_id'],owner)):raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
  task=self.s.banking.authorize(body['task_id'],owner,body['action_hash'],body['approve'],action_id=body['action_id'])
  return self.result(owner,task)
