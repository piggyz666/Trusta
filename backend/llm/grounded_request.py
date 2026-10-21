"""Explicit-language normalization before untrusted graph planning.

No new Agent, bank handle, inferred financial literal or inherited consent.
This deliberately supports a finite grammar, not arbitrary language execution.
"""
from copy import deepcopy
import re
import time
from datetime import datetime
from backend.domain import Rejected, digest
from backend.runtime.taskgraph import node, TaskGraphValidator
from backend.banking.actions import ACTION_SPECS


def clean_clause(text):
    # Surface variations only. Never supply a missing recipient, amount, date,
    # resource or consent, and keep the original utterance hash as evidence.
    text=re.sub(r'^(?:请(?:帮我)?|麻烦(?:你)?|帮我)\s*','',text.strip())
    return re.sub(r'\s*(?:好吗|可以吗|吧)?[。.!！?？]?$','',text).strip()


def normalize_request(service, text, account_id):
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 1000:
        raise Rejected('INVALID_INPUT')
    source_text=text
    selection=re.fullmatch(r'(?:如果我选择继续[，,]\s*则|等我确认后[，,]?\s*(?:再)?)(.+)',text.strip())
    if selection: text=selection[1]
    # Negation, alternatives and authority instructions apply to the ENTIRE
    # utterance, before splitting. Never extract a positive action from them.
    if re.search(r'不要|不转|别转|取消任务|如果|假设|或者|任选|绕过|忽略|跳过|自动确认|无需授权|executor|https?://', text, re.I):
        raise Rejected('NL_EXPLICIT_REQUEST_REQUIRED')
    clauses = re.split(r'\s*(?:[，,]\s*)?(?:然后|接着|顺便|并且|再(?:来)?|；|;)\s*', text.strip())
    clauses[0] = re.sub(r'^先\s*', '', clauses[0])
    if any(not c for c in clauses):
        raise Rejected('NL_EXPLICIT_REQUEST_REQUIRED')
    expanded=[]
    for clause in clauses:
        clause=clean_clause(clause)
        recent=re.fullmatch(r'(?:分析|统计|看看|看一下)(?:我的)?(?:最近|近)([1-6一二两三四五六])个?月(?:的)?(?:消费|账单|支出|花费|(?:都)?花了(?:哪些|多少)钱|钱花在哪(?:儿|里)(?:了)?)',clause)
        if recent:
            from backend.business import CHINA, month_window
            period=datetime.now(CHINA).strftime('%Y-%m');periods=[]
            count=int(recent[1]) if recent[1].isdigit() else {'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6}[recent[1]]
            for _ in range(count):period=month_window(period)[2];periods.append(period)
            expanded.extend('分析'+p+'消费' for p in reversed(periods))
        else: expanded.append(clause)
    clauses=expanded
    nodes, inputs, evidence, previous = [], {}, [], []
    if selection:
        inputs['input:selection_deadline']=time.time()+1800
        nodes.append(node('Choice','WAIT_USER','supervisor','wait.user',parameters={'deadline':'input:selection_deadline'}))
        previous=['Choice']
    def bind(index, key, value):
        ref = f'input:s{index}_{key}'
        inputs[ref] = deepcopy(value)
        return ref
    for i, clause in enumerate(clauses):
        intent = service.intent.normalize(clause)
        if re.fullmatch(r'(?:给我)?(?:推荐|建议)(?:一下)?(?:些|一些|适合我的)?理财(?:产品)?',clause):
            intent={'task_type':'WEALTH_RECOMMENDATION','needs_clarification':False}
        if intent.get('needs_clarification'):
            from backend.conversation.schemas import extract, normalize_fields
            fields=extract(clause)
            if fields.get('task_type')!='UNKNOWN' and not fields.get('needs_clarification'):
                intent=normalize_fields(fields)
        if intent.get('needs_clarification'):
            raise Rejected('NL_CLARIFICATION_REQUIRED')
        kind = intent['task_type']; label = f'S{i}'
        account = intent.get('source_account_id', account_id)
        deps = previous[:]
        if kind in {'TRANSFER', 'SCHEDULED_TRANSFER'}:
            if kind == 'SCHEDULED_TRANSFER':
                wait = label+'Time'
                nodes.append(node(wait, 'WAIT_TIME', 'supervisor', 'wait.time', deps,
                                  {'execute_at':bind(i,'execute_at',intent['execute_at'])}))
                deps = [wait]
            nodes.append(node(label,'PROPOSE_ACTION','transaction','transfer.propose',deps,{
                'account_id':bind(i,'account_id',account), 'recipient_name':bind(i,'recipient_name',intent['recipient_name']),
                'amount':bind(i,'amount',intent['amount']), 'currency':bind(i,'currency',intent['currency'])}))
        elif kind == 'BILL_ANALYSIS' and 'month' in intent:
            nodes.append(node(label+'Read','READ','finance','transactions.read',deps,{
                'account_id':bind(i,'account_id',account),'month':bind(i,'month',intent['month'])}))
            nodes.append(node(label,'ANALYZE','finance','finance.summary',[label+'Read'],{'records':label+'Read:result'}))
        elif kind == 'BILL_ANALYSIS' and 'year' in intent:
            # Twelve independent read/analysis branches avoid an artificial
            # serial depth overflow, then join before the next business clause.
            summaries=[]
            for month in range(1,13):
                code=label+'M'+str(month);read=code+'Read'
                nodes.append(node(read,'READ','finance','transactions.read',deps,{
                    'account_id':bind(i,'account_id',account),
                    'month':bind(i,'month'+str(month),f"{intent['year']:04}-{month:02}")}))
                nodes.append(node(code,'ANALYZE','finance','finance.summary',[read],{'records':read+':result'}))
                summaries.append(code)
            nodes.append(node(label,'JOIN','supervisor','graph.join',summaries))
        elif kind == 'WEALTH_RECOMMENDATION':
            nodes.append(node(label,'ANALYZE','wealth','wealth.recommend',deps))
        elif kind == 'PRODUCT_COMPARE':
            nodes.append(node(label,'COMPARE','wealth','wealth.compare',deps,{'product_ids':bind(i,'product_ids',intent['product_ids'])}))
        elif kind == 'SPLIT_REQUEST':
            nodes.append(node(label,'RESOLVE','transaction','split.request',deps,{
                'account_id':bind(i,'account_id',account),'amount':bind(i,'amount',intent['amount']),
                'participants':bind(i,'participants',intent['participants']),
                'method':bind(i,'method','EQUAL'),'values':bind(i,'values',None),
                'deadline':bind(i,'deadline',time.time()+86400)}))
        elif intent.get('operation') in ACTION_SPECS:
            owner=ACTION_SPECS[intent['operation']]['agent']
            nodes.append(node(label,'PROPOSE_ACTION',owner,owner+'.propose',deps,{
                'account_id':bind(i,'account_id',account),'action_type':bind(i,'action_type',intent['operation']),
                'target_id':bind(i,'target_id',intent['target_id']),'values':bind(i,'values',intent['parameters'])}))
        elif intent.get('operation') == 'LIST':
            cap={'CARD_MANAGEMENT':'cards.read','SUBSCRIPTION_MANAGEMENT':'subscriptions.read','DIRECT_DEBIT_MANAGEMENT':'direct_debits.read'}.get(kind)
            if not cap: raise Rejected('NL_UNSUPPORTED_COMPOSITION')
            nodes.append(node(label,'READ','service',cap,deps))
        elif intent.get('operation') == 'DETECT' and kind=='SUBSCRIPTION_MANAGEMENT':
            nodes.append(node(label+'Recurring','ANALYZE','finance','finance.recurring',deps,{'account_id':bind(i,'account_id',account)}))
            nodes.append(node(label+'Authority','READ','service','subscriptions.read',deps))
            nodes.append(node(label,'RESOLVE','service','service.resolve',[label+'Recurring',label+'Authority'],{
                'candidates':label+'Recurring:result','authority':label+'Authority:result'}))
        else:
            raise Rejected('NL_UNSUPPORTED_COMPOSITION')
        evidence.append({'clause':clause,'task_type':kind,'normalized_hash':digest(intent),
                         'input_refs':[k for k in inputs if k.startswith(f'input:s{i}_')]})
        previous=[label]
    nodes.append(node('Report','REPORT','supervisor','graph.report',previous))
    if selection:
        for row in nodes:
            if row['node_id'] not in {'Choice','Report'}:
                row['condition']={'ref':'Choice:result','equals':True}
    graph={'schema_version':1,'nodes':nodes}
    TaskGraphValidator(service.registry).validate(graph,inputs)
    return {'graph':graph,'inputs':inputs,'evidence':evidence,'source_hash':digest(source_text)}


def matches_request(candidate, expected):
    """Compare semantics and transitive order, independent of model node IDs."""
    rows=candidate['nodes']; target=expected['nodes']
    if len(rows)!=len(target):return False
    def signature(row):
        # Result references are checked through matching producer identities.
        return (row['node_type'],row['owner'],row['capability'],tuple(sorted(
            (k,v if v.startswith('input:') else ':result') for k,v in row['parameters'].items())))
    groups={}
    for row in rows:groups.setdefault(signature(row),[]).append(row)
    lookup={r['node_id']:r for r in rows}
    def ancestors(n,seen=None):
        seen=set() if seen is None else seen
        for d in lookup[n]['dependencies']:
            if d not in seen:seen.add(d);ancestors(d,seen)
        return seen
    mapping={};used=set()
    def same_condition(candidate_condition, target_condition):
        if target_condition is None:return candidate_condition is None
        return candidate_condition=={'ref':mapping[target_condition['ref'][:-7]]+':result','equals':target_condition['equals']}
    for row in target:
        options=[r for r in groups.get(signature(row),[]) if r['node_id'] not in used]
        options.sort(key=lambda r:len(ancestors(r['node_id'])))
        options=[r for r in options if same_condition(r['condition'],row['condition'])
                 and {mapping[d] for d in row['dependencies']}<=ancestors(r['node_id'])
                 and all(r['parameters'][k]==(v if v.startswith('input:') else mapping[v[:-7]]+':result')
                         for k,v in row['parameters'].items())]
        if not options:return False
        mapping[row['node_id']]=options[0]['node_id'];used.add(options[0]['node_id'])
    for row in target:
        other=lookup[mapping[row['node_id']]]
        if not same_condition(other['condition'],row['condition']):return False
        for key,ref in row['parameters'].items():
            wanted=ref if ref.startswith('input:') else mapping[ref[:-7]]+':result'
            if other['parameters'][key]!=wanted:return False
        if not {mapping[d] for d in row['dependencies']} <= ancestors(other['node_id']):return False
    return True


class GroundedRequestPlanner:
    def __init__(self,service):self.s=service

    def propose(self,user_id,text,account_id, *, on_task_created=None, current_check=None):
        normalized=normalize_request(self.s,text,account_id)
        s=self.s
        with s.lock:
            # Account ownership is authoritative, including read-only requests.
            s.gateway.read('transaction',user_id,'account.get',account_id=account_id)
            task_id=s.supervisor.receive(user_id,text,{'task_type':'GENERAL_GRAPH','needs_clarification':False})
            if on_task_created:on_task_created(task_id)
            snapshot=s.blackboard.get(task_id)
        if any(n['condition'] is not None for n in normalized['graph']['nodes']):
            # compact_v2 has no condition field. Use the existing full_v1 DSL
            # for this request only; never discard conditions or mutate global
            # settings while other requests are in flight.
            result=s.llm.propose_graph(s.registry,snapshot,text,normalized['inputs'],graph_format='full_v1')
        else:
            result=s.llm.propose_graph(s.registry,snapshot,text,normalized['inputs'])
        with s.lock:
            if current_check:current_check()
            current=s._owned_task(task_id,user_id)
            if current['revision']!=snapshot['revision'] or current['status']!=snapshot['status']:
                raise Rejected('LLM_STALE_TASK')
            s.audit.append(task_id,None,'NL_REQUEST_GROUNDED','supervisor',{
                'source_hash':normalized['source_hash'],'inputs_hash':digest(normalized['inputs']),
                'expected_graph_hash':digest(normalized['graph']),'authority':'NONE'})
            if s.llm.settings.mode=='shadow':
                return s.blackboard.update(task_id,'runtime',status='BLOCKED',errors=['LLM_SHADOW_ONLY'])
            if result.status!='SUCCEEDED':
                return s.blackboard.update(task_id,'runtime',status='BLOCKED',errors=[result.error_code or 'NL_CLARIFICATION_REQUIRED'])
            matched=matches_request(result.output,normalized['graph'])
            if not matched and s.llm.settings.graph_format=='semantic_v3':
                from backend.llm.semantic_adapter import matches_intrinsic_graphs
                matched=matches_intrinsic_graphs(result.output,normalized['graph'],s.registry,normalized['inputs'])
            if not matched:
                return s.blackboard.update(task_id,'runtime',status='BLOCKED',errors=['NL_REQUEST_SEMANTIC_MISMATCH'])
            revision=current['revision']
        # Installation rechecks revision under its own lock. Do not hold the
        # outer service lock while READ workers acquire it in other threads.
        return s.graphs.create(user_id,text,result.output,normalized['inputs'],task_id,expected_revision=revision)
