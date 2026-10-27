"""Virtual membership ledger and owner-scoped cases using the existing human queue."""
import json,time
from datetime import datetime,timedelta
from backend.business import CHINA
from backend.domain import Rejected,canonical,uid,digest

SCHEMA='''
CREATE TABLE IF NOT EXISTS member_enrolments(owner TEXT PRIMARY KEY,started REAL NOT NULL,earned INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS member_observations(owner TEXT NOT NULL,day TEXT NOT NULL,net INTEGER NOT NULL,PRIMARY KEY(owner,day));
CREATE TABLE IF NOT EXISTS points_ledger(id TEXT PRIMARY KEY,owner TEXT NOT NULL,delta INTEGER NOT NULL,reason TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS member_rewards(id TEXT PRIMARY KEY,owner TEXT NOT NULL,reward TEXT NOT NULL,status TEXT NOT NULL,points INTEGER NOT NULL,created REAL NOT NULL,UNIQUE(owner,reward));
CREATE TABLE IF NOT EXISTS member_quotes(id TEXT PRIMARY KEY,owner TEXT NOT NULL,body TEXT NOT NULL);
'''
REWARDS=[{'id':'travel','name':'旅行计划权益体验券','points':100}, {'id':'birthday','name':'生日计划权益体验券','points':50}]

class Membership:
 def __init__(self,chat):self.chat=chat;self.s=chat.s
 def _sync(self,c,owner):
  e=c.execute('SELECT * FROM member_enrolments WHERE owner=?',(owner,)).fetchone()
  if not e:return
  # Enrollment prevents seed data from becoming spendable points. Net cents prevents refund rounding exploits.
  net=c.execute("SELECT COALESCE(SUM(CASE WHEN r.kind='REFUND' THEN -r.amount ELSE r.amount END),0) n FROM consumption_records r JOIN accounts a ON a.id=r.account_id WHERE a.customer_id=? AND r.occurred_at>=? AND r.currency='CNY' AND r.status='SETTLED' AND r.kind IN ('PURCHASE','REFUND') AND r.category!='融资费用'",(owner,e['started'])).fetchone()['n']
  # Wish-fund orders are stored in the original bank operation ledger rather than consumption_records.
  for r in c.execute("SELECT body FROM bank_operations WHERE customer_id=? AND action_type='WISH_ORDER'",(owner,)):
   op=json.loads(r['body'])
   if op['status']=='SETTLED' and op['executed_at']>=e['started']:net+=op['parameters']['amount']
  earned=max(0,net//100);delta=earned-e['earned']
  if delta:
   c.execute('INSERT INTO points_ledger VALUES(?,?,?,?,?)',(uid(),owner,delta,'净消费积分' if delta>0 else '退款扣回积分',time.time()))
   c.execute('UPDATE member_enrolments SET earned=? WHERE owner=?',(earned,owner))
 def observe(self,owner,b):
  if b:raise Rejected('INVALID_INPUT')
  from backend.chat.activity import ActivityFeed
  net=max(0,ActivityFeed(self.chat).overview(owner)['finance']['net_at_cost_minor'])
  with self.s.db.transaction() as c:
   c.execute('INSERT OR IGNORE INTO member_enrolments VALUES(?,?,0)',(owner,time.time()))
   c.execute('INSERT OR IGNORE INTO member_observations VALUES(?,?,?)',(owner,datetime.now(CHINA).date().isoformat(),net))
  return self.view(owner)
 def view(self,owner):
  today=datetime.now(CHINA).date();since=(today-timedelta(days=29)).isoformat()
  with self.s.db.transaction() as c:
   self._sync(c,owner)
   rows=[dict(r) for r in c.execute('SELECT day,net FROM member_observations WHERE owner=? AND day>=? AND day<=? ORDER BY day',(owner,since,today.isoformat()))]
   ledger=[dict(r) for r in c.execute('SELECT * FROM points_ledger WHERE owner=? ORDER BY created DESC',(owner,))]
   rewards=[dict(r) for r in c.execute('SELECT * FROM member_rewards WHERE owner=?',(owner,))]
   enrolled=bool(c.execute('SELECT owner FROM member_enrolments WHERE owner=?',(owner,)).fetchone())
  avg=sum(r['net'] for r in rows)//30
  tier='基础会员' if len(rows)<30 or avg<1000000 else '金卡会员' if avg<5000000 else '白金会员'
  return dict(enrolled=enrolled,observations=rows,days=len(rows),average=avg,tier=tier,points=sum(r['delta'] for r in ledger),ledger=ledger[:100],rewards=rewards,catalog=REWARDS)
 def prepare(self,owner,b):
  if set(b)!={'reward'}:raise Rejected('INVALID_INPUT')
  r=next((r for r in REWARDS if r['id']==b['reward']),None)
  if not r:raise Rejected('INVALID_INPUT')
  q=dict(id=uid(),reward=r['id'],name=r['name'],points=r['points'],expires=time.time()+300)
  with self.s.db.transaction() as c:c.execute('INSERT INTO member_quotes VALUES(?,?,?)',(q['id'],owner,canonical(q)))
  return {'quote':q,'hash':digest(q)}
 def confirm(self,owner,b):
  if set(b)!={'quote_id','hash','approve'} or type(b['approve']) is not bool:raise Rejected('INVALID_INPUT')
  with self.s.db.transaction() as c:
   row=c.execute('SELECT body FROM member_quotes WHERE id=? AND owner=?',(b['quote_id'],owner)).fetchone()
   if not row:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
   q=json.loads(row['body'])
   if digest(q)!=b['hash'] or q['expires']<=time.time():raise Rejected('QUOTE_EXPIRED')
   if not b['approve']:return {'status':'DECLINED'}
   self._sync(c,owner)
   old=c.execute('SELECT * FROM member_rewards WHERE owner=? AND reward=?',(owner,q['reward'])).fetchone()
   if old:return {'reward':dict(old)}
   balance=c.execute('SELECT COALESCE(SUM(delta),0) n FROM points_ledger WHERE owner=?',(owner,)).fetchone()['n']
   if balance<q['points']:raise Rejected('INSUFFICIENT_POINTS')
   key=uid();c.execute('INSERT INTO member_rewards VALUES(?,?,?,?,?,?)',(key,owner,q['reward'],'AVAILABLE',q['points'],time.time()))
   c.execute('INSERT INTO points_ledger VALUES(?,?,?,?,?)',(uid(),owner,-q['points'],'兑换：'+q['name'],time.time()))
   self.s.audit.append_in(c,None,None,'MEMBER_REDEEM','customer',{'owner':owner,'reward_id':key,'points':q['points']})
  return {'status':'COMPLETED'}
 def update(self,owner,b):
  if set(b)!={'id','operation'} or b['operation'] not in {'use','cancel'}:raise Rejected('INVALID_INPUT')
  with self.s.db.transaction() as c:
   r=c.execute('SELECT * FROM member_rewards WHERE id=? AND owner=?',(b['id'],owner)).fetchone()
   if not r:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
   if r['status']!='AVAILABLE':raise Rejected('REWARD_NOT_AVAILABLE')
   self._sync(c,owner)
   if b['operation']=='use' and c.execute('SELECT COALESCE(SUM(delta),0) n FROM points_ledger WHERE owner=?',(owner,)).fetchone()['n']<0:raise Rejected('POINTS_REFUND_DEBT')
   status='USED' if b['operation']=='use' else 'CANCELLED'
   c.execute('UPDATE member_rewards SET status=? WHERE id=?',(status,r['id']))
   if status=='CANCELLED':c.execute('INSERT INTO points_ledger VALUES(?,?,?,?,?)',(uid(),owner,r['points'],'取消权益返还',time.time()))
   self.s.audit.append_in(c,None,None,'MEMBER_REWARD_'+status,'customer',{'owner':owner,'reward_id':r['id']})
  return {'status':status}

class Support:
 def __init__(self,chat):self.chat=chat;self.s=chat.s
 def view(self,owner):
  from backend.chat.activity import ActivityFeed
  cases=[]
  for r in self.s.db.rows('SELECT body,session_id FROM chat_handoffs WHERE owner=? ORDER BY created_at DESC LIMIT 100',(owner,)):
   case=json.loads(r['body']);session=self.chat.store.get(r['session_id'],owner)
   case['replies']=[{'text':m.get('text',''),'at':m.get('at')} for m in session['messages'] if m['role']=='human_agent'];cases.append(case)
  return {'cases':cases,'transactions':ActivityFeed(self.chat).list(owner,limit=100)}
 def create(self,owner,b):
  if set(b)!={'action_id','text'} or not isinstance(b['text'],str) or not 1<=len(b['text'].strip())<=500:raise Rejected('INVALID_INPUT')
  from backend.chat.activity import ActivityFeed
  item=next((i for i in ActivityFeed(self.chat).list(owner,limit=1000) if i['id']==b['action_id']),None)
  if not item:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
  if self.s.db.one("SELECT COUNT(*) n FROM chat_handoffs WHERE owner=? AND status='OPEN'",(owner,))['n']>=10:raise Rejected('CASE_LIMIT')
  sid=self.chat.create_session(owner)['session_id'];body=self.chat.store.get(sid,owner)
  body['messages'].append({'role':'user','text':b['text'],'at':time.time(),'message_id':uid(),'cards':[]})
  self.chat._open_handoff(body,owner,b['text'],'human',[])
  self.chat.store.save(body);hid=body['handoff']['id']
  with self.s.db.transaction() as c:
   row=c.execute('SELECT body FROM chat_handoffs WHERE id=?',(hid,)).fetchone();case=json.loads(row['body']);case['transaction']=item
   c.execute('UPDATE chat_handoffs SET body=? WHERE id=?',(canonical(case),hid))
  return {'id':hid,'session_id':sid}
 def update(self,owner,b):
  if set(b) not in ({'id','operation'},{'id','operation','text'}) or b['operation'] not in {'message','close'}:raise Rejected('INVALID_INPUT')
  row=self.s.db.one('SELECT session_id,status FROM chat_handoffs WHERE id=? AND owner=?',(b['id'],owner))
  if not row:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
  if row['status']!='OPEN':raise Rejected('HANDOFF_NOT_OPEN')
  body=self.chat.store.get(row['session_id'],owner)
  if b['operation']=='close':
   self.chat._close_handoff_row(b['id'],'CANCELLED_BY_USER');body['handoff']={'id':b['id'],'status':'CLOSED'};self.chat.store.save(body)
  else:
   text=b.get('text')
   if not isinstance(text,str) or not 1<=len(text.strip())<=500:raise Rejected('INVALID_INPUT')
   self.chat._handoff_message(body,owner,text);self.chat.store.save(body)
  return {'status':'OK'}
