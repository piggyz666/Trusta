"""Read-first dialogue composition over the existing semantic DAG.

This is a normalization/controller layer, not an Agent or an execution path.
Selection binds a bank-resolved resource; subsequent authorization is separate.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import hmac
import re
import time
from backend.business import CHINA, month_window
from backend.domain import Rejected, canonical, digest
from backend.runtime.taskgraph import node


class SubscriptionBudgetDialogue:
    TTL=1800

    def __init__(self, service): self.s=service

    def _seal(self, body):
        key=hmac.new(self.s.config.signing_key,b'TRUSTA-SUBSCRIPTION-DIALOGUE-V1',hashlib.sha256).digest()
        return hmac.new(key,canonical(body).encode(),hashlib.sha256).hexdigest()

    def start(self, user_id, text, account_id):
        if (type(text) is not str or not 0<len(text)<=1000
                or re.search(r'绕过|忽略|跳过|无需授权|自动确认|executor|system|https?://|不要取消|不取消|别取消|不要分析|转账|付款|购买|赎回',text,re.I)
                or not all(re.search(p,text) for p in (
                    r'(?:最近|近)(?:三|3)个?月',r'消费|账单|支出',r'订阅',r'取消',r'节省|省下',r'下个?月',r'预算|理财计划'))):
            raise Rejected('WORKFLOW_CLARIFICATION_REQUIRED')
        current=datetime.now(CHINA).strftime('%Y-%m')
        months=[];previous=current
        for _ in range(3):
            previous=month_window(previous)[2];months.append(previous)
        months.reverse()
        year,month=map(int,current.split('-'))
        next_month=f'{year+int(month==12):04}-{month%12+1:02}'
        inputs={'input:account':account_id};rows=[]
        for i,period in enumerate(months):
            inputs[f'input:month{i}']=period
            rows.extend([
                node(f'Read{i}','READ','finance','transactions.read',parameters={'account_id':'input:account','month':f'input:month{i}'}),
                node(f'Summary{i}','ANALYZE','finance','finance.summary',[f'Read{i}'],{'records':f'Read{i}:result'})])
        rows.extend([
            node('History','READ','finance','transactions.recurring.read',parameters={'account_id':'input:account'}),
            node('Recurring','ANALYZE','finance','finance.recurring',parameters={'account_id':'input:account'}),
            node('Authority','READ','service','subscriptions.read'),
            node('Resolved','RESOLVE','service','service.resolve',['Recurring','Authority'],{'candidates':'Recurring:result','authority':'Authority:result'}),
            node('Report','REPORT','supervisor','graph.report',['Summary0','Summary1','Summary2','History','Resolved'])])
        task=self.s.graphs.create(user_id,text,{'schema_version':1,'nodes':rows},inputs)
        if task['status']!='COMPLETED':return task
        with self.s.lock:
            current_task=self.s._owned_task(task['task_id'],user_id)
            state=self.s.graphs._state(current_task)
            start=month_window(months[0])[0];end=month_window(months[-1])[1]
            recent={'bank:recurring_charges:'+r['id'] for r in state['outputs']['History:result'] if start<=r['occurred_at']<end}
            ids={r['subscription_id'] for r in state['outputs']['Resolved:result']
                 if r['resolution']=='RESOLVED' and len(set(r['candidate']['evidence_refs'])&recent)>=3}
            # No subjective inference that a subscription is unnecessary.
            choices=[deepcopy(r) for r in state['outputs']['Authority:result']
                     if r['id'] in ids and r['account_id']==account_id and r['frequency']=='MONTHLY' and r['currency']=='CNY']
            body={'user_id':user_id,'task_id':task['task_id'],'account_id':account_id,'choices':choices,
                  'months':months,'budget_month':next_month,'expires_at':time.time()+self.TTL,'consumed':False}
            context=deepcopy(current_task['context']);context['subscription_dialogue']={'body':body,'seal':self._seal(body)}
            self.s.audit.append(task['task_id'],None,'SUBSCRIPTION_SELECTION_REQUIRED','supervisor',
                                {'choice_ids':sorted(r['id'] for r in choices),'source_state_hash':digest(state),'authority':'NONE'})
            return self.s.blackboard.update(task['task_id'],'runtime',context=context,
                clarification='请选择一个订阅并明确下月基础预算；选择后仍须逐笔授权取消。未登记或有歧义的候选不会取消。')

    def select(self, user_id, task_id, expected_revision, text):
        # Currency suffix is outside the exact numeral span. No inference from
        # free text, no "cancel all", and no authorization words accepted here.
        match=re.fullmatch(r'\s*取消\s*([^，,；;\s]+)\s*[，,；;]\s*下个?月基础预算\s*([0-9]+(?:\.[0-9]{1,2})?|[零〇一二两三四五六七八九十百千万点]+)\s*(?:元|人民币)\s*[。.]?\s*',text) if type(text) is str and len(text)<=1000 else None
        if not match or type(expected_revision) is not int:raise Rejected('WORKFLOW_SELECTION_REQUIRED')
        from backend.conversation.schemas import amount_cents
        budget=amount_cents(match[2])
        with self.s.lock, self.s.graphs.claims.hold(task_id):
            source=self.s._owned_task(task_id,user_id)
            stored=source['context'].get('subscription_dialogue',{});body=stored.get('body')
            if (source['revision']!=expected_revision or source['status']!='COMPLETED' or type(body) is not dict
                    or body.get('user_id')!=user_id or body.get('task_id')!=task_id or body.get('consumed')
                    or body.get('expires_at',0)<=time.time() or type(stored.get('seal')) is not str
                    or not hmac.compare_digest(stored['seal'],self._seal(body))):
                raise Rejected('WORKFLOW_STALE_OR_TAMPERED')
            self.s.graphs._state(source)
            choices=[r for r in body['choices'] if match[1] in {r['id'],r['merchant']}]
            if len(choices)!=1:raise Rejected('WORKFLOW_AMBIGUOUS_SELECTION')
            chosen=choices[0]
            fresh=self.s.gateway.read('service',user_id,'subscription.get',resource_id=chosen['id'])
            if fresh!=chosen:raise Rejected('WORKFLOW_RESOURCE_CHANGED')
            inputs={'input:account':body['account_id'],'input:target':chosen['id'],'input:action':'SUBSCRIPTION_CANCEL',
                    'input:values':{},'input:budget':budget,'input:month':body['budget_month']}
            graph={'schema_version':1,'nodes':[
                node('Cancel','PROPOSE_ACTION','service','service.propose',parameters={
                    'account_id':'input:account','target_id':'input:target','action_type':'input:action','values':'input:values'}),
                node('Budget','ANALYZE','wealth','wealth.budget_plan',['Cancel'],{
                    'budget':'input:budget','month':'input:month','cancellation':'Cancel:result'}),
                node('Report','REPORT','supervisor','graph.report',['Budget'])]}
            # Durable one-time reservation under the existing DB claim, across
            # Core instances. A crash before/after proposal creation never
            # reopens consent or silently makes a second proposal.
            updated=deepcopy(body);updated['consumed']=True
            context=deepcopy(source['context']);context['subscription_dialogue']={'body':updated,'seal':self._seal(updated)}
            self.s.blackboard.update(task_id,'runtime',context=context)
            self.s.audit.append(task_id,None,'SUBSCRIPTION_SELECTION_RESERVED','supervisor',{
                'selected_resource':chosen['id'],'input_hash':digest(inputs),'financial_authorization':False})
            # No read frontier here; the new graph stops at independent auth.
            try:task=self.s.graphs.create(user_id,text,graph,inputs)
            except Exception:
                self.s.audit.append(task_id,None,'SUBSCRIPTION_SELECTION_STOPPED','runtime',{
                    'reason':'PROPOSAL_CREATION_INTERRUPTED','automatic_retry':False})
                raise
            updated['child_task_id']=task['task_id']
            context['subscription_dialogue']={'body':updated,'seal':self._seal(updated)}
            self.s.blackboard.update(task_id,'runtime',context=context)
            self.s.audit.append(task['task_id'],None,'SUBSCRIPTION_USER_SELECTED','supervisor',{
                'source_task_id':task_id,'source_state_hash':digest(source['graph_state']),
                'selected_resource':chosen['id'],'input_hash':digest(inputs),'financial_authorization':False})
            return task
