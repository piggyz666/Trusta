"""Blackboard-centred bounded graph execution using the six registered agents."""
from concurrent.futures import ThreadPoolExecutor
import time
from backend.domain import Rejected, canonical, digest
from backend.runtime.taskgraph import SecurityExpansionCompiler, TaskGraphValidator, READS, SAFETY, read_tool
import hashlib
import hmac
import threading
import weakref

class GraphRuntime:
    def __init__(self, service):
        self.s=service;self._gates: weakref.WeakValueDictionary[str,threading.RLock]=weakref.WeakValueDictionary();self._gate_lock=threading.Lock()
        from backend.runtime.graph_claims import GraphClaims
        self.claims=GraphClaims(service)

    def propose(self,user_id,goal,inputs):
        from backend.llm.graph_protocol import input_names
        input_names(inputs)
        with self.s.lock:
            task_id=self.s.supervisor.receive(user_id,goal,{'task_type':'GENERAL_GRAPH','needs_clarification':False})
            snapshot=self.s.blackboard.get(task_id)
        result=self.s.llm.propose_graph(self.s.registry,snapshot,goal,inputs)
        with self.s.lock:
            current=self.s._owned_task(task_id,user_id)
            if current['revision']!=snapshot['revision'] or current['status']!=snapshot['status']: raise Rejected('LLM_STALE_TASK')
            if self.s.llm.settings.mode=='shadow':
                return self.s.blackboard.update(task_id,'runtime',status='BLOCKED',errors=['LLM_SHADOW_ONLY'],result={'proposal_validated':result.status=='SUCCEEDED','proposal_hash':digest(result.output) if result.output else None})
            if result.status=='NEEDS_CLARIFICATION':
                # A question installs no graph and conveys no authorization. The
                # user supplies explicit inputs in a subsequent proposal request.
                return self.s.blackboard.update(task_id,'runtime',status='BLOCKED',errors=['GRAPH_CLARIFICATION_REQUIRED'],result=result.output)
            if result.status!='SUCCEEDED':
                return self.s.blackboard.update(task_id,'runtime',status='BLOCKED',errors=[result.error_code or 'REJECT_PLAN'])
        try: return self.create(user_id,goal,result.output,inputs,task_id,expected_revision=snapshot['revision'])
        except Rejected as exc:
            with self.s.lock: return self.s.blackboard.update(task_id,'runtime',status='BLOCKED',errors=[exc.code])

    def create(self, user_id, goal, graph, inputs, existing_task_id=None, *, expected_revision=None):
        if self.s.config.banking_version!=5: raise Rejected('PHASE5_REQUIRED')
        from contextlib import ExitStack
        with ExitStack() as ownership:
            with self.s.lock:
                if existing_task_id is not None:
                    current=self.s._owned_task(existing_task_id,user_id)
                    if (current.get('graph_state') or current['status']!='PENDING'
                            or expected_revision is not None and current['revision']!=expected_revision):
                        raise Rejected('LLM_STALE_TASK')
                # Ground resource identifiers against the bank before graph installation.
                TaskGraphValidator(self.s.registry).validate(graph,inputs)
                for n in graph['nodes']:
                    if n['node_type']=='READ':
                        params={k:inputs[v] for k,v in n['parameters'].items() if v in inputs}
                        if len(params)==len(n['parameters']): self.s.gateway.read(n['owner'],user_id,read_tool(n['capability'],params),**params)
                    if n['node_type']=='PROPOSE_ACTION':
                        params={k:inputs[v] for k,v in n['parameters'].items()}
                        self.s.gateway.read(n['owner'],user_id,'account.get',account_id=params['account_id'])
                        if n['capability']=='transfer.propose': self.s.transaction.recipient(user_id,params['recipient_name'])
                        else:
                            from backend.banking.actions import snapshot_from_gateway
                            snapshot_from_gateway(self.s.gateway,n['owner'],user_id,params['account_id'],params['action_type'],params['target_id'])
                compiled=SecurityExpansionCompiler(self.s.registry).compile(graph,inputs)
                task_id=existing_task_id or self.s.supervisor.receive(user_id,goal,{'task_type':'GENERAL_GRAPH','needs_clarification':False})
                ownership.enter_context(self.claims.hold(task_id))
                self.s.blackboard.install_graph(task_id,graph,inputs,self.s.registry)
                state={'graph':graph,'inputs':inputs,'outputs':{},'actions':{},'signals':{},'skipped':[], 'compiler_hash':digest(compiled)}
                self._save(task_id,state,status='RUNNING')
                self.s.audit.append(task_id,None,'GRAPH_INSTALLED','planner',{'compiler_hash':state['compiler_hash'],'semantic_hash':digest(graph)})
            return self.advance(task_id,user_id)

    def _save(self, task_id, state, **changes):
        actions=[r for r in state['actions'].values() if r.get('action')]
        state['seal']=self._seal(state)
        return self.s.blackboard.update(task_id,'runtime',graph_state=state,action_records=actions,
            proposed_actions=[r['action'] for r in actions],authorizations=[r['authorization'] for r in actions if r.get('authorization')],
            verifications=[r['verification'] for r in actions if r.get('verification')],receipts=[r['receipt'] for r in actions if r.get('receipt')],**changes)

    def _seal(self,state):
        key=hmac.new(self.s.config.signing_key,b'trusta-taskgraph-state-v1',hashlib.sha256).digest()
        return hmac.new(key,canonical({k:v for k,v in state.items() if k!='seal'}).encode(),hashlib.sha256).hexdigest()

    def _state(self,task):
        state=task['graph_state']
        if not isinstance(state.get('seal'),str) or not hmac.compare_digest(state['seal'],self._seal(state)): raise Rejected('GRAPH_STORAGE_TAMPERED')
        return state

    def _values(self, node, state):
        values=state['inputs']|state['outputs']
        return {key:values[ref] for key,ref in node['parameters'].items()}

    def _semantic(self, node, task, state):
        s=self.s;user=task['user_id'];values=self._values(node,state);cap=node['capability'];kind=node['node_type']
        s.registry.agents.require_active(node['owner'])
        s.crypto_registry.require_active(node['owner'])
        if s.finance_transport and node['owner']=='finance' and cap in {'accounts.read','balances.read','transactions.read','finance.summary','finance.anomalies'}:
            return s.finance_transport.run(node,task,values)
        if kind=='READ': return s.gateway.read(node['owner'],user,read_tool(cap,values),**values)
        if cap=='finance.summary': return s.finance.summarize(values['records'])
        if cap=='finance.anomalies': return s.finance.anomalies(values['records'])
        if cap=='finance.recurring': return s.finance.recurring(user,values['account_id'])
        if cap=='wealth.recommend': return s.wealth.explain(s.wealth.risk_profile(user),s.wealth.match(s.wealth.risk_profile(user),s.wealth.products(user)))
        if cap=='wealth.budget_plan':
            from backend.business import month_window
            month_window(values['month'])
            budget=values['budget'];cancellation=values['cancellation']
            if type(budget) is not int or budget<0: raise Rejected('GRAPH_INVALID_BUDGET')
            saving=cancellation.get('budget_delta',0) if cancellation.get('verified') else 0
            return {'month':values['month'],'base_budget':budget,'monthly_saving':saving,'planned_budget':budget+saving,
                    'receipt_ref':cancellation.get('receipt_id'),'financial_write':False,'guaranteed_return':False}
        if cap=='split.request':
            bill=s.splits.create(user,values['account_id'],values['amount'],values['participants'],values['method'],values['values'],values['deadline'])
            return s.splits.publish(bill['id'],user)
        if cap=='service.resolve': return s.service_agent.resolve_subscriptions(values['candidates'],values['authority'])
        if cap=='wealth.compare': return s.wealth.compare(user,values['product_ids'])
        if cap=='decision.boolean':
            if type(values['value']) is not bool: raise Rejected('GRAPH_BOOLEAN_REQUIRED')
            return values['value']
        if cap=='wait.user':
            self._deadline(values['deadline'])
            if node['node_id'] not in state['signals']: return 'WAITING_USER'
            return state['signals'][node['node_id']]
        if cap=='wait.time':
            due=values['execute_at']
            if type(due) not in {float,int} or not 0<due<=time.time()+30*86400: raise Rejected('GRAPH_INVALID_TIME')
            return 'WAITING_TIME' if time.time()<due else {'due':due}
        if kind in {'JOIN','REPORT'}:
            return {dep:state['outputs'].get(dep+':result') for dep in node['dependencies']}
        if kind=='PROPOSE_ACTION':
            if cap=='transfer.propose':
                local_task=dict(task);local_task['context']={'normalized':{'amount':values['amount'],'currency':values['currency']}}
                account=s.transaction.account(user,values['account_id'])
                action=s.transaction.propose(local_task,s.transaction.recipient(user,values['recipient_name']),account,s.transaction.balance(user,values['account_id']))
            else:
                agent=s.wealth if cap=='wealth.propose' else s.service_agent
                args=(task,values['action_type'],values['target_id'],values['values'],values['account_id'])
                action=agent.propose(*args,s.config.action_ttl) if cap=='wealth.propose' else agent.propose(*args)
            with s.db.transaction() as conn: conn.execute('INSERT INTO actions VALUES(?,?)',(action['action_id'],canonical(action)))
            state['actions'][node['node_id']]={'action':action,'policy':None,'authorization':None,'contract':None,'transaction':None,'verification':None,'receipt':None,'status':'PROPOSED'}
            return {'action_id':action['action_id'],'action_hash':digest(action),'authority':'PROPOSAL_ONLY'}
        raise Rejected('REJECT_PLAN')

    @staticmethod
    def _deadline(value):
        if type(value) not in {int,float} or not time.time()<value<=time.time()+30*86400: raise Rejected('GRAPH_WAIT_EXPIRED_OR_INVALID')

    def _safety(self, node_id, kind, task, state):
        s=self.s;r=state['actions'][node_id];a=r['action']
        if r['status'] in {'DENIED','SKIPPED'}: return {'stage':kind,'outcome':'SKIPPED','receipt':None}
        if kind=='SCHEMA': s.schema.validate(a)
        elif kind=='CAPABILITY':
            s.capabilities.check(a['initiating_agent'],'transfer.propose' if a['action_type']=='TRANSFER' else 'wealth.propose' if a['initiating_agent']=='wealth' else 'service.propose')
            s.capabilities.check('executor',a['requested_capability'])
        elif kind=='POLICY':
            r['policy']=s.policy.evaluate(a)
            if r['policy']['decision']=='BLOCK': raise Rejected('POLICY_BLOCKED')
        elif kind=='AUTHORIZATION':
            if r['authorization'] is None: r['authorization']=s.auth.request(a,r['policy'])
            status=s.auth.get(r['authorization']['authorization_id'])['status']
            if status.startswith('PENDING'): return 'WAITING_USER'
            if status!='AUTHORIZED': raise Rejected('INVALID_AUTHORIZATION')
            r['status']='AUTHORIZED'
        elif kind=='CONTRACT':
            policy=s.policy.evaluate(a);auth=s.auth.validate(r['authorization']['authorization_id'],a)
            if policy['decision']=='BLOCK': raise Rejected('POLICY_BLOCKED')
            r['policy']=policy;r['contract']=s.contracts.issue(a,policy['policy_decision_id'],auth['authorization_id'])
        elif kind=='EXECUTE':
            self._save(task['task_id'],state,status='EXECUTING')
            r['transaction']=s.gateway.execute('executor',a,r['contract']);r['status']='EXECUTED'
        elif kind=='VERIFY':
            r['verification']=s.verifier.verify(a,r['transaction']['id'])
            s.safety.verification_result(a['customer_id'],r['verification'])
            if r['verification']['status']!='VERIFIED': raise Rejected('MANUAL_REVIEW_REQUIRED')
        elif kind=='RECEIPT':
            r['receipt']=s.receipts.create(a,r['contract'],r['transaction'],r['verification']);r['status']='COMPLETED'
            delta=0
            if a['action_type']=='SUBSCRIPTION_CANCEL':
                target=a['preconditions']['target']
                if target['frequency']=='MONTHLY': delta=target['amount']
            state['outputs'][node_id+':result']={'action_id':a['action_id'],'receipt_id':r['receipt']['receipt_id'],'verified':True,
                    'status':'COMPLETED','budget_delta':delta,'authority':'VERIFIED_RECEIPT'}
        return {'stage':kind,'outcome':'COMPLETED'}

    def advance(self, task_id, user_id):
        with self._gate_lock:
            gate=self._gates.get(task_id)
            if gate is None:gate=threading.RLock();self._gates[task_id]=gate
        with gate:
            self.s._owned_task(task_id,user_id)
            with self.claims.hold(task_id,reentrant=True):
                return self._advance(task_id,user_id)

    def _advance(self, task_id, user_id):
        s=self.s
        with s.lock:
            task=s._owned_task(task_id,user_id)
            if task['task_type']!='GENERAL_GRAPH': raise Rejected('GRAPH_TASK_REQUIRED')
            if task['status'] in {'COMPLETED','CANCELLED','BLOCKED','FAILED','MANUAL_REVIEW'}: return task
            state=self._state(task);graph=state['graph'];nodes={n['node_id']:n for n in graph['nodes']}
            # Revalidate the immutable semantic plan and deterministic compiled graph on every resume.
            compiled=SecurityExpansionCompiler(s.registry).compile(graph,state['inputs'])
            if digest(compiled)!=state['compiler_hash'] or [{k:v for k,v in p.items() if k!='status'} for p in task['steps']]!=[{k:v for k,v in p.items() if k!='status'} for p in compiled['steps']]:
                raise Rejected('GRAPH_STORAGE_TAMPERED')
            for _ in range(len(task['steps'])+1):
                task=s.blackboard.get(task_id);completed={p['step_id'] for p in task['steps'] if p['status']=='COMPLETED'}
                ready=[p for p in task['steps'] if p['status'] in {'PENDING','WAITING_USER','WAITING_TIME'} and set(p['dependencies'])<=completed]
                if not ready:
                    done=len(completed)==len(task['steps'])
                    return self._save(task_id,state,status='COMPLETED' if done else task['status'],result=state['outputs'])
                reads=[p for p in ready if p['step_id'] in nodes and nodes[p['step_id']]['node_type']=='READ' and nodes[p['step_id']]['condition'] is None]
                try:
                    # Real concurrent read frontier; all writes and persistence remain serial.
                    if len(reads)>1:
                        for p in reads: s.scheduler.start(task_id,p['step_id'])
                        revision=s.blackboard.get(task_id)['revision']
                        # Workers acquire the identity registry lock themselves. Never wait
                        # for those workers while owning that same lock.
                        s.lock.release()
                        try:
                            with ThreadPoolExecutor(max_workers=min(4,len(reads))) as pool:
                                futures={p['step_id']:pool.submit(self._semantic,nodes[p['step_id']],task,state) for p in reads}
                                values={code:future.result() for code,future in futures.items()}
                        finally:
                            s.lock.acquire()
                        if s.blackboard.get(task_id)['revision']!=revision: raise Rejected('GRAPH_STALE_READ')
                        for code,value in values.items():
                            state['outputs'][code+':result']=value;s.scheduler.transition(task_id,code,'COMPLETED')
                            s._agent_message(nodes[code]['owner'],'supervisor',task_id,code,'READ_ONLY_REPORT',value)
                        self._save(task_id,state,status='RUNNING');continue
                    p=ready[0];code=p['step_id'];s.scheduler.start(task_id,code)
                    if code in nodes:
                        n=nodes[code];condition=n['condition']
                        if condition and state['outputs'].get(condition['ref']) is not condition['equals']:
                            state['skipped'].append(code);value={'outcome':'SKIPPED'}
                            if n['node_type']=='PROPOSE_ACTION': state['actions'][code]={'action':None,'status':'SKIPPED'}
                        else: value=self._semantic(n,task,state)
                    else:
                        stage=p['name']
                        if stage not in SAFETY or not code.endswith('X'+stage): raise Rejected('REJECT_PLAN')
                        root=code[:-len('X'+stage)]
                        if root in state['skipped']: value={'stage':stage,'outcome':'SKIPPED'}
                        else: value=self._safety(root,stage,task,state)
                    if isinstance(value,str) and value in ('WAITING_USER','WAITING_TIME'):
                        s.scheduler.transition(task_id,code,value)
                        return self._save(task_id,state,status=value)
                    state['outputs'][code+':result']=value;s.scheduler.transition(task_id,code,'COMPLETED')
                    if code in nodes: s._agent_message(nodes[code]['owner'],'supervisor',task_id,code,'ACTION_PROPOSED' if nodes[code]['node_type']=='PROPOSE_ACTION' else 'READ_ONLY_REPORT',value)
                    s.audit.append(task_id,None,'GRAPH_NODE_FINISHED','runtime',{'node_id':code,'output_hash':digest(value)})
                    self._save(task_id,state,status='RUNNING')
                except Exception as exc:
                    uncertain=p['name'] in {'EXECUTE','VERIFY','RECEIPT'}
                    status='MANUAL_REVIEW' if uncertain else 'BLOCKED'
                    for running in s.blackboard.get(task_id)['steps']:
                        if running['status']=='RUNNING': s.scheduler.transition(task_id,running['step_id'],status)
                    s.audit.append(task_id,None,'GRAPH_STOPPED','runtime',{'reason':exc.code if isinstance(exc,Rejected) else 'GRAPH_OPERATION_UNCERTAIN','node_id':p['step_id']})
                    return self._save(task_id,state,status=status,errors=[exc.code if isinstance(exc,Rejected) else 'GRAPH_OPERATION_UNCERTAIN'])
            raise Rejected('GRAPH_STEP_BOUND_EXCEEDED')

    def signal(self, task_id, user_id, node_id, value):
        with self.claims.hold(task_id):
            return self._signal(task_id,user_id,node_id,value)

    def _signal(self, task_id, user_id, node_id, value):
        with self.s.lock:
            task=self.s._owned_task(task_id,user_id);state=self._state(task)
            n=next((n for n in state['graph']['nodes'] if n['node_id']==node_id),None)
            p=next((p for p in task['steps'] if p['step_id']==node_id),None)
            if not n or n['node_type']!='WAIT_USER' or not p or p['status']!='WAITING_USER' or type(value) is not bool: raise Rejected('GRAPH_SIGNAL_DENIED')
            self._deadline(self._values(n,state)['deadline']);state['signals'][node_id]=value
            self.s.audit.append(task_id,None,'GRAPH_USER_SELECTION','runtime',{'node_id':node_id,'value':value,'financial_authorization':False})
            self._save(task_id,state)
        return self._advance(task_id,user_id)

    def cancel(self, task_id, user_id, expected_revision=None):
        """Stop unexecuted work; preserve completed payments and uncertainty.

        Claim is nonblocking: do not wait for a worker while holding Core lock.
        No authorize/advance call, so denying one action cannot start another.
        """
        with self.s.lock, self.claims.hold(task_id):
            task=self.s._owned_task(task_id,user_id)
            if task['task_type']!='GENERAL_GRAPH': raise Rejected('GRAPH_TASK_REQUIRED')
            if expected_revision is not None and task['revision']!=expected_revision:
                raise Rejected('LLM_STALE_TASK')
            if task['status'] not in {'WAITING_USER','WAITING_TIME'}: return False
            state=self._state(task)
            if any(r.get('status') in {'AUTHORIZED','EXECUTED'} for r in state['actions'].values()):
                return False
            for r in state['actions'].values():
                if r.get('authorization') and r['authorization']['status'].startswith('PENDING'):
                    r['authorization']=self.s.auth.decide(r['authorization']['authorization_id'],user_id,r['action'],False)
                    r['status']='DENIED'
            for step in task['steps']:
                if step['status'] in {'PENDING','READY','WAITING_USER','WAITING_TIME'}:
                    self.s.scheduler.transition(task_id,step['step_id'],'CANCELLED')
            self.s.audit.append(task_id,None,'GRAPH_CANCELLED','runtime',{'completed_receipts':sum(bool(r.get('receipt')) for r in state['actions'].values()),'new_financial_writes':0})
            self._save(task_id,state,status='CANCELLED')
            return True

    def authorize(self, task_id, user_id, node_id, action_hash, approve, strong_secret=''):
        with self.claims.hold(task_id):
            return self._authorize(task_id,user_id,node_id,action_hash,approve,strong_secret)

    def _authorize(self, task_id, user_id, node_id, action_hash, approve, strong_secret=''):
        with self.s.lock:
            task=self.s._owned_task(task_id,user_id);state=self._state(task);r=state['actions'].get(node_id)
            step=next((p for p in task['steps'] if p['step_id']==node_id+'XAUTHORIZATION'),None)
            if type(approve) is not bool or not r or not r.get('authorization') or not step or step['status']!='WAITING_USER' or digest(r['action'])!=action_hash:
                raise Rejected('GRAPH_AUTHORIZATION_DENIED')
            if approve and self.s.safety.enabled:
                fresh=self.s.policy.evaluate(r['action'])
                if fresh['decision']=='BLOCK': raise Rejected('POLICY_BLOCKED')
                if fresh['decision']=='STRONG_AUTH' and r['authorization']['auth_method']!='totp':
                    r['policy']=fresh;r['authorization']=self.s.auth.request(r['action'],fresh)
                    return self._save(task_id,state,clarification='日累计或风险已提高，请进行一次性验证码授权。')
            r['authorization']=self.s.auth.decide(r['authorization']['authorization_id'],user_id,r['action'],approve,strong_secret)
            if r['authorization']['status']=='DENIED': r['status']='DENIED'
            self.s.audit.append(task_id,r['action']['action_id'],'GRAPH_ACTION_DECISION','authorization',r['authorization'])
            self._save(task_id,state)
        return self._advance(task_id,user_id)
