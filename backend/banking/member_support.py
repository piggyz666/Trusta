"""Owner-scoped support cases (服务工单) using the existing human queue."""
import json,time
from datetime import datetime,timedelta
from backend.business import CHINA
from backend.domain import Rejected,canonical,uid,digest

SCHEMA=''


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
