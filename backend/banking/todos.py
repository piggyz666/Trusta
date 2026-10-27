"""Read-only owner-scoped work list; no scheduler, AI call or automatic approval."""
import json,time
from datetime import datetime,timedelta
from backend.business import CHINA
from backend.banking.advanced import Advanced
from backend.banking.extensions import Extensions,GoldSavings
from backend.banking.personal import Personal

def view(chat,owner):
 today=datetime.now(CHINA).date();until=today+timedelta(days=30);items=[]
 def add(key,title,due,page,detail,amount=None):
  if due and due>str(until):return
  items.append(dict(id=key,title=title,due=due,page=page,detail=detail,amount=amount,overdue=bool(due and due<str(today))))
 for t in Extensions(chat).f.view(owner)['contracts']:
  if t['type'] in {'loan','installment'} and t['status']=='ACTIVE':
   row=next((r for r in t['schedule'] if not r['paid']),None)
   if row:add('repay:'+t['id'],'贷款 / 分期还款',row['due'],'credit',t['card_label'],row['total'])
 for d in Extensions(chat).view(owner)['deposits']:
  if d['status']=='ACTIVE':add('deposit:'+d['id'],'定期存款到期',d['matures'],'deposits','到期可支取或续存，不会自动扣款',d['principal'])
 for p in GoldSavings(chat).view(owner)['plans']:
  if p['status']!='ACTIVE':continue
  run=next((r for r in p['runs'] if r['state']!='COMPLETED'),None)
  if run:add('gold:'+p['id']+':'+run['due'],p['name'],run['due'],'gold-savings','定投预算；核对当期金价后确认',p['amount'])
 for s in Advanced(chat).view(owner)['states']:
  for g in s['statements']:
   if g['status'] in {'BILLED','OVERDUE'}:add('statement:'+s['card_id']+g['date'],'信用账单待还',g['due'],'credit-account','已出账；可全额或最低还款',g['outstanding'])
  for o in s['orders']:
   if o['status']=='PENDING':add('fund:'+o['id'],'基金份额待确认',None,'funds','可确认份额或撤单；确认份额不重复扣款',o['amount'])
 personal=Personal(chat.s).plans(owner)
 for p in personal['plans']:
  if p['status']=='ACTIVE':add('plan:'+p['id'],p['name'],p['start'],'plans','计划预算独立核对；不自动消费',p['budget'])
 for i,d in enumerate(personal['suggestions']):add('date:'+str(i)+d['date'],d['name'],d['date'],'plans','来自你已开启提醒的重要日期')
 for r in chat.s.db.rows("SELECT id,body FROM chat_handoffs WHERE owner=? AND status IN ('OPEN','RESOLVED')",(owner,)):
  b=json.loads(r['body'])
  if b.get('transaction'):add('support:'+r['id'],'交易工单进展',None,'support','查看处理状态和客服回复；不会自动退款')
 return {'items':sorted(items,key=lambda x:(not x['overdue'],x['due'] or '9999',x['id'])),'as_of':time.time(),'note':'未来30天及逾期待办；此页只提醒，不执行资金操作。'}
