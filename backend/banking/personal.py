"""Owner-scoped preferences and per-event budgets. Never writes account balances."""
import json,time
from datetime import date,datetime
from backend.business import CHINA
from backend.domain import Rejected,uid,canonical,digest
SCHEMA='''CREATE TABLE IF NOT EXISTS personal_profiles(owner TEXT PRIMARY KEY,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS life_plans(id TEXT PRIMARY KEY,owner TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,place TEXT NOT NULL,start TEXT NOT NULL,end TEXT NOT NULL,budget INTEGER NOT NULL CHECK(budget>0),status TEXT NOT NULL,version INTEGER NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS life_plan_bindings(plan_id TEXT PRIMARY KEY,owner TEXT NOT NULL,session_id TEXT NOT NULL,fund_id TEXT NOT NULL,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profile_event_sync(owner TEXT PRIMARY KEY, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plan_spending(action_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, owner TEXT NOT NULL, amount INTEGER NOT NULL);'''
KINDS={'wash','travel','birthday','anniversary','concert','custom'}
def text(value,limit,required=False):
 if not isinstance(value,str) or len(value)>limit or (required and not value.strip()):raise Rejected('INVALID_INPUT')
 return value.strip()
def day(value):
 try:return date.fromisoformat(value)
 except (ValueError,TypeError):raise Rejected('INVALID_DATE')

def next_occurrence(month,day_number,today=None):
 today=today or datetime.now(CHINA).date()
 for year in range(today.year,today.year+9):
  try:candidate=date(year,month,day_number)
  except ValueError:continue
  if candidate>=today:return candidate
 raise Rejected('INVALID_DATE')

def event_rows(s,owner,proactive=False):
 profile=s.db.one('SELECT body FROM personal_profiles WHERE owner=?',(owner,))
 if not profile:return s.service_agent.optional_resources(owner,'life_event')
 if proactive and not json.loads(profile['body']).get('suggestions'):return []
 # Dates the customer entered in their TRUSTA profile are TRUSTA's own data, read locally whatever bank is connected.
 return s.db.rows("SELECT * FROM life_events WHERE customer_id=? AND id LIKE 'profile-%' ORDER BY month,day,id",(owner,))

def sync_profile_events(c,owner,b):
 c.execute("DELETE FROM life_events WHERE customer_id=? AND id LIKE 'profile-%'",(owner,))
 for d in b['dates']:
  if d['kind']=='custom':continue
  when=day(d['date']);label=text(d['name'],40,True)
  suffix='生日' if d['kind']=='birthday' else '纪念日'
  person=label[:-len(suffix)] if label.endswith(suffix) and len(label)>len(suffix) else label
  eid='profile-'+digest({'owner':owner,'date':d})[:28]
  c.execute('INSERT OR IGNORE INTO life_events VALUES(?,?,?,?,?,?,?)',(eid,owner,person,'','BIRTHDAY' if d['kind']=='birthday' else 'ANNIVERSARY',when.month,when.day))
 c.execute('INSERT INTO profile_event_sync VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET hash=excluded.hash',(owner,digest(b)))

def plan_snapshot(c,owner,pid,service=None):
 """A plan with its budget use. Inside the sandbox bank (`service` None) the bank's tables are read on `c`.
 On TRUSTA's side use plan_view(service, ...) instead: it runs outside any database transaction, reads the bound wish
 fund through the bank interface and takes spending from TRUSTA's own receipts."""
 one=(lambda sql,args:service.db.one(sql,args)) if service is not None else (lambda sql,args:(lambda r:dict(r) if r else None)(c.execute(sql,args).fetchone()))
 row=one('SELECT * FROM life_plans WHERE id=? AND owner=?',(pid,owner))
 if not row:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
 p=dict(row);p['spent']=one("SELECT COALESCE(SUM(amount),0) AS n FROM retail_orders WHERE customer_id=? AND status='PAID' AND json_extract(detail,'$.plan_id')=?",(owner,pid))['n']
 link=one('SELECT * FROM life_plan_bindings WHERE plan_id=? AND owner=?',(pid,owner))
 p['binding']=dict(link) if link else None;p['fund']=None;p['reserved']=0
 if link:
  p['binding'].pop('body',None)
  if service is not None:
   fund=None
   if link['fund_id'] and service.gateway.offers('wish_fund.get'):
    try:fund=service.gateway.read('service',owner,'wish_fund.get',resource_id=link['fund_id'])
    except Rejected:fund=None
   p['fund']=fund;p['reserved']=fund['balance'] if fund else 0
   p['spent']+=one('SELECT COALESCE(SUM(amount),0) n FROM plan_spending WHERE plan_id=? AND owner=?',(pid,owner))['n']
  else:
   fund=one('SELECT * FROM wish_funds WHERE customer_id=? AND id=?',(owner,link['fund_id']))
   p['fund']=fund;p['reserved']=fund['balance'] if fund else 0
   p['spent']+=one("SELECT COALESCE(SUM(json_extract(body,'$.parameters.amount')),0) n FROM bank_operations WHERE customer_id=? AND action_type='WISH_ORDER' AND json_extract(body,'$.target.id')=? AND json_extract(body,'$.status')='SETTLED'",(owner,link['fund_id']))['n']
 return p

def plan_view(service,owner,pid):
 return plan_snapshot(None,owner,pid,service)

def backfill_spending(db):
 """One-off for data from before plan_spending existed (sandbox only, where the bank's operations share this database)."""
 try:
  with db.transaction() as c:
   c.execute("""INSERT OR IGNORE INTO plan_spending SELECT json_extract(o.body,'$.action_id'),b.plan_id,b.owner,json_extract(o.body,'$.parameters.amount')
                FROM bank_operations o JOIN life_plan_bindings b ON b.fund_id=json_extract(o.body,'$.target.id') AND b.owner=o.customer_id
                WHERE o.action_type='WISH_ORDER' AND json_extract(o.body,'$.status')='SETTLED'""")
 except Exception:
  pass

class Personal:
 def __init__(self,s):
  self.s=s
  from backend.bank_sdk.discovery import is_external
  if not is_external(s):backfill_spending(s.db)
 def profile(self,owner):
  row=self.s.db.one('SELECT body FROM personal_profiles WHERE owner=?',(owner,))
  return json.loads(row['body']) if row else {'name':'','city':'','has_car':False,'suggestions':False,'interests':[],'dates':[]}
 def save_profile(self,owner,b):
  if set(b)!={'name','city','has_car','suggestions','interests','dates'} or type(b['has_car']) is not bool or type(b['suggestions']) is not bool:raise Rejected('INVALID_INPUT')
  b=dict(b);b['name']=text(b['name'],30);b['city']=text(b['city'],40)
  if not isinstance(b['interests'],list) or len(b['interests'])>8 or any(not isinstance(i,str) or i not in {'旅行','音乐','电影','美食','运动','购物'} for i in b['interests']):raise Rejected('INVALID_INPUT')
  if not isinstance(b['dates'],list) or len(b['dates'])>30:raise Rejected('INVALID_INPUT')
  for d in b['dates']:
   if not isinstance(d,dict) or set(d)!={'name','kind','date'} or not isinstance(d['kind'],str) or d['kind'] not in {'birthday','anniversary','custom'}:raise Rejected('INVALID_INPUT')
   text(d['name'],40,True);day(d['date'])
  with self.s.db.transaction() as c:
   c.execute('INSERT INTO personal_profiles VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET body=excluded.body',(owner,canonical(b)))
   sync_profile_events(c,owner,b)
  return {'saved':True}
 def plans(self,owner):
  plans=[plan_view(self.s,owner,r['id']) for r in self.s.db.rows('SELECT id FROM life_plans WHERE owner=? ORDER BY created_at DESC',(owner,))]
  profile=self.profile(owner);suggestions=[];today=datetime.now(CHINA).date()
  if profile['suggestions']:
   for d in profile['dates']:
    source=day(d['date']);nextday=next_occurrence(source.month,source.day,today)
    if nextday and (nextday-today).days<=30:suggestions.append({'name':d['name'],'kind':d['kind'],'date':nextday.isoformat(),'days':(nextday-today).days})
  return {'plans':plans,'suggestions':suggestions,'has_car':profile['has_car'],'city':profile['city']}
 def create(self,owner,b):
  if set(b)!={'name','kind','place','start','end','budget'} or not isinstance(b['kind'],str) or b['kind'] not in KINDS or type(b['budget']) is not int or not 0<b['budget']<=100000000:raise Rejected('INVALID_INPUT')
  name=text(b['name'],50,True);place=text(b['place'],80);start,end=day(b['start']),day(b['end'])
  if end<start or (end-start).days>365:raise Rejected('INVALID_DATE_RANGE')
  with self.s.db.transaction() as c:
   if c.execute("SELECT COUNT(*) n FROM life_plans WHERE owner=? AND status='ACTIVE'",(owner,)).fetchone()['n']>=100:raise Rejected('PLAN_LIMIT')
   pid='plan-'+uid();c.execute('INSERT INTO life_plans VALUES(?,?,?,?,?,?,?,?,?,?,?)',(pid,owner,name,b['kind'],place,start.isoformat(),end.isoformat(),b['budget'],'ACTIVE',0,time.time()))
  return {'plan_id':pid}
 def update(self,owner,b):
  if set(b)!={'plan_id','budget','status'} or type(b['budget']) is not int or not 0<b['budget']<=100000000 or not isinstance(b['status'],str) or b['status'] not in {'ACTIVE','ARCHIVED'}:raise Rejected('INVALID_INPUT')
  p=plan_view(self.s,owner,b['plan_id'])  # reads the bank (wish fund) first, outside the write transaction
  with self.s.db.transaction() as c:
   if c.execute('SELECT version FROM life_plans WHERE id=? AND owner=?',(p['id'],owner)).fetchone()['version']!=p['version']:raise Rejected('PLAN_CHANGED')
   binding=c.execute('SELECT * FROM life_plan_bindings WHERE plan_id=? AND owner=?',(p['id'],owner)).fetchone()
   if binding:
    if b['budget']!=p['budget']:raise Rejected('PLAN_BUDGET_BOUND')
    if b['status']=='ARCHIVED':
     if p['reserved']>0:raise Rejected('PLAN_FUNDS_REMAIN')
     # Pending authorizations must be explicitly declined in the original conversation first.
     row=c.execute('SELECT body FROM chat_sessions WHERE id=? AND owner=?',(binding['session_id'],owner)).fetchone()
     session=json.loads(row['body']) if row else {}
     if json.loads(binding['body']).get('state')=='PREPARING' or session.get('pending') or session.get('busy'):raise Rejected('PLAN_CONFIRMATION_PENDING')
   if b['budget']<p['spent']:raise Rejected('BUDGET_BELOW_SPENT')
   c.execute('UPDATE life_plans SET budget=?,status=?,version=version+1 WHERE id=?',(b['budget'],b['status'],p['id']))
  return {'saved':True}
