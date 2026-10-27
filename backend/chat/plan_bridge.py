"""Connect editable activity drafts to the existing signed wish-fund pipeline.
Form preparation never authorizes funds and never calls an LLM. A persistent
one-per-plan binding prevents retries from creating duplicate funding proposals.
"""
import json
from datetime import datetime,timedelta
from backend.business import CHINA
from backend.domain import Rejected,canonical
from backend.banking.personal import Personal,plan_snapshot,day

class PlanBridge:
 def __init__(self,chat):self.chat=chat;self.s=chat.s
 def open(self,owner,b):
  if set(b)!={'plan_id'} or not isinstance(b['plan_id'],str):raise Rejected('INVALID_INPUT')
  with self.s.db.transaction() as c:p=plan_snapshot(c,owner,b['plan_id'])
  if not p['binding']:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
  session=self.chat.get(p['binding']['session_id'],owner)
  if not session['active'] and p['fund']:
   fresh=self.chat.create_session(owner,'app',p['fund']['account_id'])
   with self.s.db.transaction() as c:
    c.execute('UPDATE life_plan_bindings SET session_id=? WHERE plan_id=? AND owner=? AND session_id=?',(fresh['session_id'],p['id'],owner,session['session_id']))
    sid=c.execute('SELECT session_id FROM life_plan_bindings WHERE plan_id=? AND owner=?',(p['id'],owner)).fetchone()['session_id']
   return {'session_id':sid}
  return {'session_id':session['session_id']}
 def view(self,owner):
  result=Personal(self.s).plans(owner)
  result['accounts']=self.chat.catalog(owner)['accounts']
  for a in result['accounts']:
   row=self.s.db.one('SELECT balance,frozen FROM accounts WHERE id=? AND customer_id=?',(a['id'],owner));a.update(row)
  result['funds']=self.s.service_agent.resources(owner,'wish_fund')
  result['merchants']=[{'id':m['id'],'name':m['name'],'item':m['item'],'price':m['price']} for m in self.s.service_agent.resources(owner,'merchant') if m['status']=='ACTIVE']
  linked={p['binding']['fund_id'] for p in result['plans'] if p['binding']}
  result['funds']=[f for f in result['funds'] if f['id'] not in linked]
  return result
 def prepare(self,owner,b):
  if set(b)!={'plan_id','account_id','items'} or not isinstance(b['plan_id'],str) or not isinstance(b['account_id'],str) or not isinstance(b['items'],list) or len(b['items'])>3 or any(not isinstance(x,str) for x in b['items']) or len(set(b['items']))!=len(b['items']):raise Rejected('INVALID_INPUT')
  existing=self.s.db.one('SELECT session_id FROM life_plan_bindings WHERE plan_id=? AND owner=?',(b['plan_id'],owner))
  if existing:return {'session_id':existing['session_id'],'reused':True}
  with self.s.db.transaction() as c:p=plan_snapshot(c,owner,b['plan_id'])
  today=datetime.now(CHINA).date()
  if p['status']!='ACTIVE' or not today<=day(p['start'])<=day(p['end'])<=today+timedelta(days=366):raise Rejected('PLAN_DATE_OR_STATE_INVALID')
  if p['spent']:raise Rejected('PLAN_HAS_LEGACY_SPENDING')
  catalog=self.chat.catalog(owner)
  if b['account_id'] not in {a['id'] for a in catalog['accounts']}:raise Rejected('ACCOUNT_ACCESS_DENIED')
  merchants={m['id']:m for m in self.s.service_agent.resources(owner,'merchant') if m['status']=='ACTIVE'}
  if any(x not in merchants for x in b['items']):raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
  if sum(merchants[x]['price'] for x in b['items'])>p['budget']:raise Rejected('ORDER_EXCEEDS_FUND')
  session=self.chat.create_session(owner,'app',b['account_id'])
  state={'state':'PREPARING','version':p['version'],'budget':p['budget'],'items':b['items']}
  with self.s.db.transaction() as c:
   current=plan_snapshot(c,owner,p['id'])
   if current['version']!=p['version'] or current['status']!='ACTIVE':raise Rejected('PLAN_CHANGED')
   if current['binding']:return {'session_id':current['binding']['session_id'],'reused':True}
   c.execute('INSERT INTO life_plan_bindings VALUES(?,?,?,?,?)',(p['id'],owner,session['session_id'],'',canonical(state)))
  amount=f"{p['budget']//100}.{p['budget']%100:02d}"
  message=f"为{p['name']}准备心愿金{amount}元，日期{p['start']}，结束日期{p['end']}。"
  args={'name':p['name'][:20],'lock_amount':{'value':amount,'span':amount+'元'},'date':{'iso':p['start'],'span':p['start']},'end_date':{'iso':p['end'],'span':p['end']}}
  if b['items']:
   message+='仅虚拟测试商品：'+'、'.join(merchants[x]['item'] for x in b['items'])+'。'
   args['items']=[{'id':x,'span':merchants[x]['item']} for x in b['items']]
   args['order_days_before']=2
  try:
   turn=self.chat.send(session['session_id'],owner,'activity-prepare',session['revision'],message,prepared_calls=[{'name':'propose_wish_plan','arguments':args}])
   card=next((c for c in turn['reply']['cards'] if c['type']=='confirm' and c['action_type']=='FUND_LOCK'),None)
   fund_id=''
   if card:
    task=self.s.get_task(card['task_id'],owner)
    fund_id=next(a['target']['id'] for a in [r['action'] for r in task['action_records']] if a['action_id']==card['action_id'])
   state['state']='READY' if card else 'NEEDS_REVIEW'
   with self.s.db.transaction() as c:c.execute('UPDATE life_plan_bindings SET fund_id=?,body=? WHERE plan_id=? AND owner=?',(fund_id,canonical(state),p['id'],owner))
   return {'session_id':session['session_id'],'prepared':bool(card)}
  except Exception:
   state['state']='NEEDS_REVIEW'
   with self.s.db.transaction() as c:c.execute('UPDATE life_plan_bindings SET body=? WHERE plan_id=? AND owner=?',(canonical(state),p['id'],owner))
   raise
 def release(self,owner,b):
  if set(b)!={'plan_id'} or not isinstance(b['plan_id'],str):raise Rejected('INVALID_INPUT')
  with self.s.db.transaction() as c:p=plan_snapshot(c,owner,b['plan_id'])
  if not p['binding'] or not p['fund'] or p['fund']['balance']<=0:raise Rejected('WISH_FUND_NOT_ACTIVE')
  session=self.chat.get(self.open(owner,b)['session_id'],owner)
  if session['pending']:return {'session_id':session['session_id'],'reused':True}
  f=p['fund'];message=f"请释放心愿金{f['name']}的余额，先给我确认卡。"
  self.chat.send(session['session_id'],owner,'activity-release-'+str(f['version']),session['revision'],message,
   prepared_calls=[{'name':'propose_fund_release','arguments':{'fund':{'id':f['id'],'span':f['name']}}}])
  return {'session_id':session['session_id']}
