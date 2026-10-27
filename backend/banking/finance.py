"""Virtual credit and gold. Money writes only run through the signed banking executor."""
import calendar
import copy
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from backend.domain import Rejected, canonical, uid
from backend.business import CHINA
from backend.banking import extensions as extras
from backend.banking import advanced

SCHEMA = '''
CREATE TABLE IF NOT EXISTS finance_contracts(id TEXT PRIMARY KEY,customer_id TEXT NOT NULL,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finance_links(task_id TEXT PRIMARY KEY,customer_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gold_positions(customer_id TEXT NOT NULL,account_id TEXT NOT NULL,units INTEGER NOT NULL,cost INTEGER NOT NULL,version INTEGER NOT NULL,PRIMARY KEY(customer_id,account_id));
CREATE TABLE IF NOT EXISTS finance_versions(customer_id TEXT PRIMARY KEY,version INTEGER NOT NULL);
INSERT OR IGNORE INTO accounts VALUES('finance-clearing',NULL,'CNY',10000000000,0,0);
'''
KINDS = ('LOAN_DRAW','CREDIT_REPAY','CREDIT_SETTLE','INSTALLMENT_START','GOLD_BUY','GOLD_SELL')
LABELS = dict(zip(KINDS, ('贷款放款','偿还本期','提前结清','办理消费分期','买入黄金','卖出黄金')))
INCOMING = {'LOAN_DRAW','INSTALLMENT_START','GOLD_SELL'}
KINDS += tuple(extras.LABELS)
LABELS.update(extras.LABELS)
INCOMING |= extras.INCOMING | advanced.INCOMING
KINDS += tuple(advanced.LABELS)
LABELS.update(advanced.LABELS)
LIMIT = 2000000  # 20,000 yuan, shared virtual credit limit

def cents(value):
    return int(Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

def schedule(amount, months, installment=False, now=None):
    start = datetime.fromtimestamp(now or time.time(), CHINA)
    rows=[];remaining=amount
    for i in range(months):
        principal=amount//months + (i < amount % months)
        # Explicit sandbox product terms; not an offer by any real bank.
        fee=cents(Decimal(amount)*Decimal('0.005')) if installment else cents(Decimal(remaining)*Decimal('0.003'))
        year, month=divmod(start.year*12+start.month+i,12);month+=1
        due=start.replace(year=year,month=month,day=min(start.day,calendar.monthrange(year,month)[1])).date().isoformat()
        rows.append({'period':i+1,'due':due,'principal':principal,'fee':fee,'total':principal+fee,'paid':False})
        remaining-=principal
    return rows

def contracts(conn, owner):
    return [json.loads(r['body']) for r in conn.execute('SELECT body FROM finance_contracts WHERE customer_id=?',(owner,))]

def card_label(db, owner, card_id):
    row=db.one('SELECT d.nickname,c.card_type FROM cards c LEFT JOIN card_designs d ON d.card_id=c.id AND d.customer_id=c.customer_id WHERE c.id=? AND c.customer_id=?',(card_id,owner))
    return ((row or {}).get('nickname') or '虚拟银行卡')+' · 标识 '+card_id[-4:]

def snapshot(conn, owner, resource_id):
    r=conn.execute('SELECT body FROM finance_contracts WHERE id=? AND customer_id=?',(resource_id,owner)).fetchone()
    if not r:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
    target=json.loads(r['body'])
    card=conn.execute('SELECT * FROM cards WHERE id=? AND customer_id=? AND account_id=?',(target['card_id'],owner,target['account_id'])).fetchone()
    if not card:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
    position=conn.execute('SELECT * FROM gold_positions WHERE customer_id=? AND account_id=?',(owner,target['account_id'])).fetchone()
    position=dict(position) if position else {'customer_id':owner,'account_id':target['account_id'],'units':0,'cost':0,'version':0}
    all_contracts=contracts(conn,owner)
    debt=sum(t['remaining'] for t in all_contracts if t['type'] in {'loan','installment'} and t['status']=='ACTIVE')
    financed=[t['source_id'] for t in all_contracts if t['type']=='installment' and t['status'] in {'ACTIVE','SETTLED'}]
    v=conn.execute('SELECT version FROM finance_versions WHERE customer_id=?',(owner,)).fetchone()
    source=None
    if target['type']=='installment':
        row=conn.execute('SELECT * FROM consumption_records WHERE id=? AND account_id=?',(target['source_id'],target['account_id'])).fetchone()
        source=dict(row) if row else None
    risk=conn.execute('SELECT * FROM risk_profiles WHERE customer_id=?',(owner,)).fetchone()
    return {'target':target,'card':dict(card),'position':position,'source':source,'risk_profile':dict(risk) if risk else None,'credit':{'outstanding':debt,'version':v['version'] if v else 0,'financed':sorted(financed)}} | extras.snapshots(conn,owner,target) | advanced.snapshots(conn,owner,target)

def payment(kind, target):
    if kind in extras.LABELS or kind in advanced.LABELS:return target['amount']
    if kind in {'LOAN_DRAW','INSTALLMENT_START','GOLD_BUY','GOLD_SELL'}:return target['amount']
    pending=[p for p in target['schedule'] if not p['paid']]
    if not pending:return 0
    return pending[0]['total'] if kind=='CREDIT_REPAY' else target['remaining']+pending[0]['fee']

def errors(kind,s,p,now):
    t,c,a=s['target'],s['card'],s['account'];out=[]
    if p!={'amount':payment(kind,t),'currency':'CNY'}:out.append('FINANCE_AMOUNT_MISMATCH')
    if c['status']!='ACTIVE' or not c['online_enabled']:out.append('CARD_NOT_PAYABLE')
    if p['amount']>c['spending_limit']:out.append('CARD_LIMIT_EXCEEDED')
    if kind not in INCOMING and kind not in advanced.NONCASH and p['amount']>a['balance']:out.append('INSUFFICIENT_BALANCE')
    if kind in advanced.LABELS:return out+advanced.errors(kind,s,p,now)
    if kind in extras.LABELS:return out+extras.errors(kind,s,p,now)
    out += extras.recurring_errors(s)
    valid={'LOAN_DRAW':'loan','INSTALLMENT_START':'installment','GOLD_BUY':'gold_buy','GOLD_SELL':'gold_sell'}
    if kind in valid:
        if t['type']!=valid[kind] or t['status']!='DRAFT':out.append('FINANCE_STATE_INVALID')
        if now>=t['expires_at']:out.append('QUOTE_EXPIRED')
    elif t['type'] not in {'loan','installment'} or t['status']!='ACTIVE':out.append('FINANCE_STATE_INVALID')
    if kind in {'LOAN_DRAW','INSTALLMENT_START'} and s['credit']['outstanding']+t['amount']>LIMIT:out.append('CREDIT_LIMIT_EXCEEDED')
    if kind=='INSTALLMENT_START' and t['source_id'] in s['credit']['financed']:out.append('ALREADY_FINANCED')
    if kind=='INSTALLMENT_START' and (not s['source'] or s['source']['status']!='SETTLED' or s['source']['kind']!='PURCHASE' or s['source']['amount']!=t['amount']):out.append('INSTALLMENT_SOURCE_INVALID')
    if kind=='GOLD_SELL' and t['units']>s['position']['units']:out.append('INSUFFICIENT_HOLDING')
    if kind=='GOLD_BUY':
        risk=s['risk_profile']
        if not risk or not risk['assessed_at']<=now<risk['expires_at']:out.append('RISK_PROFILE_REQUIRED')
        elif risk['risk_level']<3:out.append('SUITABILITY_RISK_MISMATCH')
    return out

def expected(kind,s,p):
    if kind in advanced.LABELS:return advanced.expected(kind,s,p)
    if kind in extras.LABELS:return extras.expected(kind,s,p)
    r=copy.deepcopy(s);t=r['target'];amount=p['amount']
    r['account']['balance']+=amount*(1 if kind in INCOMING else -1);r['account']['version']+=1
    t['version']+=1;r['credit']['version']+=1
    if kind in {'LOAN_DRAW','INSTALLMENT_START'}:
        t['status']='ACTIVE';r['credit']['outstanding']+=amount
        if kind=='INSTALLMENT_START':r['credit']['financed']=sorted(r['credit']['financed']+[t['source_id']])
    elif kind in {'CREDIT_REPAY','CREDIT_SETTLE'}:
        pending=[x for x in t['schedule'] if not x['paid']]
        principal=pending[0]['principal'] if kind=='CREDIT_REPAY' else t['remaining']
        t['paid_fees']+=pending[0]['fee'];t['remaining']-=principal
        r['credit']['outstanding']-=principal
        if kind=='CREDIT_REPAY':pending[0]['paid']=True
        else:
            for i,x in enumerate(pending):
                x['paid']=True
                if i:x['waived_fee']=x['fee'];x['fee']=0;x['total']=x['principal']
        if not t['remaining']:t['status']='SETTLED'
    else:
        pos=r['position'];t['status']='COMPLETED'
        if kind=='GOLD_BUY':pos['units']+=t['units'];pos['cost']+=amount
        else:
            sold_cost=cents(Decimal(pos['cost'])*t['units']/pos['units']) if pos['units'] else 0
            pos['units']-=t['units'];pos['cost']-=sold_cost;t['realized_profit']=amount-sold_cost
        pos['version']+=1
    return r

def apply(conn,action,before,after,opid):
    kind=action['action_type'];amount=action['parameters']['amount'];incoming=kind in INCOMING
    t,a,pos=after['target'],after['account'],after['position']
    conn.execute('UPDATE finance_contracts SET body=? WHERE id=?',(canonical(t),t['id']))
    conn.execute('UPDATE accounts SET balance=?,version=? WHERE id=?',(a['balance'],a['version'],a['id']))
    clearing=conn.execute("SELECT balance FROM accounts WHERE id='finance-clearing'").fetchone()['balance']
    if incoming and kind not in advanced.NONCASH and clearing<amount:raise Rejected('SETTLEMENT_INSUFFICIENT')
    if kind not in advanced.NONCASH:conn.execute("UPDATE accounts SET balance=balance+?,version=version+1 WHERE id='finance-clearing'",(-amount if incoming else amount,))
    conn.execute('INSERT INTO finance_versions VALUES(?,?) ON CONFLICT(customer_id) DO UPDATE SET version=excluded.version',(t['customer_id'],after['credit']['version']))
    if kind in advanced.LABELS:advanced.apply(conn,action,before,after,opid)
    if kind in extras.LABELS:extras.apply(conn,action,before,after,opid)
    if kind.startswith('GOLD_'):
        conn.execute('INSERT INTO gold_positions VALUES(?,?,?,?,?) ON CONFLICT(customer_id,account_id) DO UPDATE SET units=excluded.units,cost=excluded.cost,version=excluded.version',tuple(pos[k] for k in ('customer_id','account_id','units','cost','version')))
    entries=advanced.ledger(action) if kind in advanced.LABELS else {(a['id'],'CREDIT' if incoming else 'DEBIT',amount),('finance-clearing','DEBIT' if incoming else 'CREDIT',amount)}
    for aid,side,amount in entries:
        conn.execute('INSERT INTO ledger_entries VALUES(?,?,?,?,?)',(uid(),opid,aid,side,amount))
    # Principal and asset transfers are not consumption. Only interest/fees enter the expense bill.
    fee=t['paid_fees']-before['target']['paid_fees']
    if fee:conn.execute('INSERT INTO consumption_records VALUES(?,?,?,?,?,?,?,?,?)',(opid,a['id'],LABELS[kind],'基金费用' if kind.startswith('FUND_') else '融资费用',fee,'CNY',time.time(),'PURCHASE','SETTLED'))

class Finance:
    def __init__(self,chat):
        self.chat=chat;self.s=chat.s
        if self.s.config.bank_config_path or getattr(self.s.gateway._adapter,'external',False):raise Rejected('VIRTUAL_BANK_ONLY')

    def view(self,owner):
        from backend.banking.retail import Retail
        with self.s.db.transaction() as c:items=contracts(c,owner)
        sources=self.s.db.rows("SELECT r.* FROM consumption_records r JOIN accounts a ON a.id=r.account_id WHERE a.customer_id=? AND r.kind='PURCHASE' AND r.status='SETTLED' AND r.category!='融资费用' ORDER BY occurred_at DESC LIMIT 100",(owner,))
        # Legacy local retail orders can still be refunded, so do not refinance those purchases.
        eligible=[]
        financed={t['source_id'] for t in items if t['type']=='installment' and t['status'] in {'ACTIVE','SETTLED'}}
        for row in sources:
            if row['id'] in financed or not 10000<=row['amount']<=LIMIT:continue
            if self.s.db.one('SELECT id FROM bank_operations WHERE id=?',(row['id'],)):continue
            eligible.append(row)
        return {'cards':Retail(self.chat).view(owner)['cards'],'contracts':sorted(items,key=lambda t:t['created_at'],reverse=True)[:100],
                'positions':self.s.db.rows('SELECT * FROM gold_positions WHERE customer_id=?',(owner,)), 'eligible':eligible,
                'risk':self.s.db.one('SELECT * FROM risk_profiles WHERE customer_id=?',(owner,)),
                'credit_limit':LIMIT,'outstanding':sum(t['remaining'] for t in items if t['status']=='ACTIVE' and t['type'] in {'loan','installment'}),
                'rules':'仅虚拟产品：贷款按剩余本金计息，年利率 3.60%；消费分期每期按原本金收取 0.50% 手续费（非年化利率）。提前结清收取当前一期费用，免除未来期费用。每月一期，不自动扣款。'}

    def create(self,owner,b):
        if b.get('type','').upper() in advanced.LABELS:return advanced.Advanced(self.chat).create(owner,b)
        if b.get('type','').upper() in extras.LABELS:return extras.Extensions(self.chat).create(owner,b)
        typ=b.get('type');base={'type','card_id'}
        required=base|({'amount','months'} if typ=='loan' else {'source_id','months'} if typ=='installment' else {'units'} if typ in {'gold_buy','gold_sell'} else set())
        if typ not in {'loan','installment','gold_buy','gold_sell'} or set(b)!=required:raise Rejected('INVALID_INPUT')
        if typ in {'loan','installment'} and (type(b['months']) is not int or b['months'] not in (3,6,12)):raise Rejected('INVALID_INPUT')
        if typ=='loan' and (type(b['amount']) is not int or not 10000<=b['amount']<=LIMIT):raise Rejected('INVALID_INPUT')
        quote=None
        if typ.startswith('gold_'):
            if type(b['units']) is not int or not 1<=b['units']<=100000:raise Rejected('INVALID_INPUT')
            from backend.chat.http import market
            provider=market(self.chat)
            if hasattr(provider,'refresh'):provider.refresh()
            quote=next((q for q in provider.view()['quotes'] if q['symbol']=='XAU'),None)
            if not quote or quote['snapshot'] or quote['stale'] or not 0<quote['price']<100000:raise Rejected('GOLD_QUOTE_UNAVAILABLE')
            price=cents(Decimal(str(quote['price']))*100)
        with self.s.db.transaction() as c:
            card=c.execute('SELECT * FROM cards WHERE id=? AND customer_id=?',(b['card_id'],owner)).fetchone()
            if not card:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
            if typ=='installment':
                source=c.execute("SELECT * FROM consumption_records WHERE id=? AND account_id=? AND kind='PURCHASE' AND status='SETTLED' AND category!='融资费用'",(b['source_id'],card['account_id'])).fetchone()
                if not source or c.execute('SELECT id FROM bank_operations WHERE id=?',(b['source_id'],)).fetchone():raise Rejected('INSTALLMENT_SOURCE_INVALID')
                amount=source['amount']
                if not 10000<=amount<=LIMIT:raise Rejected('INSTALLMENT_SOURCE_INVALID')
            else:amount=b['amount'] if typ=='loan' else cents(Decimal(price)*b['units']/100)
            if amount<=0:raise Rejected('INVALID_INPUT')
            now=time.time();cid='fin-'+uid()
            t={'id':cid,'customer_id':owner,'account_id':card['account_id'],'card_id':card['id'],'card_label':card_label(self.s.db,owner,card['id']),'type':typ,'status':'DRAFT','version':0,
               'amount':amount,'remaining':amount if typ in {'loan','installment'} else 0,'paid_fees':0,'source_id':b.get('source_id',''),
               'schedule':schedule(amount,b['months'],typ=='installment') if typ in {'loan','installment'} else [],'created_at':now,'expires_at':now+(120 if quote else 1800),
               'units':b.get('units',0),'price':price if quote else 0,'quote':quote,'realized_profit':0}
            c.execute('INSERT INTO finance_contracts VALUES(?,?,?)',(cid,owner,canonical(t)))
        return {'contract':t}

    def archive(self,owner,b):
        if set(b)!={'contract_id'}:raise Rejected('INVALID_INPUT')
        with self.s.db.transaction() as c:
            row=c.execute('SELECT body FROM finance_contracts WHERE id=? AND customer_id=?',(b['contract_id'],owner)).fetchone()
            if not row:raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
            t=json.loads(row['body'])
            if t['status']!='DRAFT':raise Rejected('FINANCE_STATE_INVALID')
            # Changing status invalidates every old signed precondition; no ledger/history is removed.
            t['status']='ARCHIVED';t['version']+=1;t['remaining']=0
            c.execute('UPDATE finance_contracts SET body=? WHERE id=?',(canonical(t),t['id']))
        return {'contract':t}

    def propose(self,owner,b):
        if set(b)!={'contract_id','operation'} or b['operation'] not in KINDS:raise Rejected('INVALID_INPUT')
        with self.s.db.transaction() as c:t=snapshot(c,owner,b['contract_id'])['target']
        amount=payment(b['operation'],t)
        if not amount:raise Rejected('FINANCE_STATE_INVALID')
        task=self.s.banking.create(LABELS[b['operation']],owner,t['account_id'],{'task_type':'FINANCE_MANAGEMENT','operation':b['operation'],'target_id':t['id'],'parameters':{'amount':amount,'currency':'CNY'}})
        with self.s.db.transaction() as c:c.execute('INSERT INTO finance_links VALUES(?,?)',(task['task_id'],owner))
        return self.result(owner,task)

    def result(self,owner,task):
        from backend.chat.cards import CardRenderer
        cards,_=CardRenderer(self.s,owner,self.chat.catalog(owner)).task_cards(task)
        return {'task_id':task['task_id'],'status':task['status'],'cards':cards,'errors':[r.get('policy',{}).get('reason_codes',[]) for r in task.get('action_records',[]) if r.get('status') in {'BLOCKED','FAILED'}]}

    def owned_task(self,owner,task_id):
        if not self.s.db.one('SELECT task_id FROM finance_links WHERE task_id=? AND customer_id=?',(task_id,owner)):raise Rejected('RESOURCE_NOT_FOUND_OR_DENIED')
        return self.s.get_task(task_id,owner)

    def decide(self,owner,b):
        if set(b)-{'task_id','action_id','action_hash','approve','verification'} or not {'task_id','action_id','action_hash','approve'}<=set(b) or type(b['approve']) is not bool:raise Rejected('INVALID_INPUT')
        self.owned_task(owner,b['task_id']);secret='';v=b.get('verification')
        if v is not None:
            if not isinstance(v,dict) or set(v)!={'method','code'} or v['method'] not in {'TOTP','SMS','DEMO_SECRET'} or not isinstance(v['code'],str) or not 1<=len(v['code'])<=64:raise Rejected('INVALID_VERIFICATION')
            secret=('sms:' if v['method']=='SMS' else '')+v['code']
        return self.result(owner,self.s.banking.authorize(b['task_id'],owner,b['action_hash'],b['approve'],secret,b['action_id']))

    def sms(self,owner,b):
        if set(b)!={'task_id','action_id'}:raise Rejected('INVALID_INPUT')
        task=self.owned_task(owner,b['task_id'])
        record=next((r for r in task['action_records'] if r['action']['action_id']==b['action_id']),None)
        if not record or not record.get('authorization') or record['status']!='WAITING_USER':raise Rejected('ACTION_NOT_PENDING')
        return self.s.safety.issue_sms(owner,record['authorization'],demo=os.environ.get('TRUSTA_DEMO_MODE','false').lower()=='true')
