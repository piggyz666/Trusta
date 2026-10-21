"""Closed semantic DSL and deterministic financial expansion; no executable model code."""
from copy import deepcopy
import re
from backend.domain import Rejected, canonical
from backend.bank_sdk.contracts import CANONICAL
from backend.security.capability import CapabilityManager
from backend.banking.actions import ACTION_SPECS

SEMANTIC = {'READ','ANALYZE','RESOLVE','COMPARE','DECIDE','PROPOSE_ACTION','WAIT_USER','WAIT_TIME','BRANCH','JOIN','REPORT'}
SAFETY = ('SCHEMA','CAPABILITY','POLICY','AUTHORIZATION','CONTRACT','EXECUTE','VERIFY','RECEIPT')
OWNERS={'SCHEMA':'schema','CAPABILITY':'schema','POLICY':'policy','AUTHORIZATION':'authorization','CONTRACT':'executor','EXECUTE':'executor','VERIFY':'verifier','RECEIPT':'receipts'}
FIELDS={'node_id','node_type','owner','capability','input_refs','output_refs','dependencies','condition','failure_policy','parameters'}
SPECIAL={'ANALYZE':{'finance':{'finance.summary','finance.anomalies','finance.recurring'},'wealth':{'wealth.recommend','wealth.budget_plan'}},
         'RESOLVE':{'service':{'service.resolve'},'transaction':{'split.request'}},'COMPARE':{'wealth':{'wealth.compare'}},
         'DECIDE':{'supervisor':{'decision.boolean'}},'BRANCH':{'supervisor':{'decision.boolean'}},
         'WAIT_USER':{'supervisor':{'wait.user'}},'WAIT_TIME':{'supervisor':{'wait.time'}},
         'JOIN':{'supervisor':{'graph.join'}},'REPORT':{'supervisor':{'graph.report'}}}
PARAMS={'finance.summary':{'records'},'finance.anomalies':{'records'},'finance.recurring':{'account_id'},
        'wealth.budget_plan':{'budget','cancellation','month'},'split.request':{'account_id','amount','participants','method','values','deadline'},
        'wealth.recommend':set(),'service.resolve':{'candidates','authority'},'wealth.compare':{'product_ids'},
        'decision.boolean':{'value'},'wait.user':{'deadline'},'wait.time':{'execute_at'},'graph.join':set(),'graph.report':set(),
        'transfer.propose':{'account_id','recipient_name','amount','currency'},'service.propose':{'action_type','account_id','target_id','values'},
        'wealth.propose':{'action_type','account_id','target_id','values'}}
READS={CANONICAL[k]:k for k in CapabilityManager.READ_MAP if k in CANONICAL}
READ_PARAMS={'account.get':{'account_id'},'account.get_balance':{'account_id'},'payee.resolve':{'name'},
             'transaction.list':{'account_id','month'},'customer.get_risk_profile':set(),'wealth.list_products':set(),
             'card.list':set(),'card.get':{'resource_id'},'card_application.get':{'resource_id'},'subscription.list':set(),'subscription.get':{'resource_id'},
             'direct_debit.list':set(),'direct_debit.get':{'resource_id'},'portfolio.get':{'account_id','product_id'},
             'transaction.recurring_history':{'account_id'}}

def read_tool(capability, parameters):
    matches=[tool for tool in READ_PARAMS if CANONICAL[tool]==capability and set(parameters)==READ_PARAMS[tool]]
    if len(matches)!=1: raise Rejected('REJECT_PLAN','READ_PARAMETER_SCHEMA')
    return matches[0]

def node(node_id, node_type, owner, capability, dependencies=(), parameters=None, condition=None):
    parameters=parameters or {}
    return {'node_id':node_id,'node_type':node_type,'owner':owner,'capability':capability,
            'input_refs':list(dict.fromkeys(parameters.values())),'output_refs':[node_id+':result'],
            'dependencies':list(dependencies),'condition':condition,'failure_policy':{'mode':'STOP','max_retries':0},'parameters':parameters}

class TaskGraphValidator:
    def __init__(self, registry): self.registry=registry

    def validate(self, graph, inputs):
        def require(ok,reason):
            if not ok: raise Rejected('REJECT_PLAN',reason)
        try:
            require(isinstance(graph,dict) and set(graph)=={'schema_version','nodes'} and type(graph['schema_version']) is int and graph['schema_version']==1,'GRAPH_SCHEMA')
            require(isinstance(inputs,dict) and all(re.fullmatch(r'input:[A-Za-z][A-Za-z0-9_]{0,63}',k) for k in inputs),'INPUT_PROVENANCE')
            require(len(canonical(graph))<=65536 and isinstance(graph['nodes'],list) and 1<=len(graph['nodes'])<=32,'GRAPH_SIZE')
            rows=graph['nodes'];ids=[r.get('node_id') for r in rows]
            require(all(isinstance(v,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,31}',v) for v in ids) and len(set(ids))==len(ids),'NODE_ID')
            lookup=dict(zip(ids,rows));ancestors={};depth={};remaining=set(ids);outputs=set()
            while remaining:
                frontier=[v for v in ids if v in remaining and isinstance(lookup[v].get('dependencies'),list) and set(lookup[v]['dependencies'])<=set(ancestors)]
                require(bool(frontier),'CYCLIC_OR_MISSING_DEPENDENCY')
                for v in frontier:
                    r=lookup[v];deps=r['dependencies'];ancestors[v]=set(deps)|set().union(*(ancestors[d] for d in deps))
                    depth[v]=1+max((depth[d] for d in deps),default=0);require(depth[v]<=20,'GRAPH_DEPTH');remaining.remove(v)
            writes=[]
            for r in rows:
                require(set(r)==FIELDS,'NODE_SCHEMA');v=r['node_id'];kind=r['node_type'];owner=r['owner'];cap=r['capability']
                require(kind in SEMANTIC,'EXECUTION_AUTHORITY_FORBIDDEN');self.registry.agents.require_active(owner)
                require(len(r['dependencies'])==len(set(r['dependencies'])) and len(r['dependencies'])<=16,'DEPENDENCIES')
                require(r['output_refs']==[v+':result'] and v+':result' not in outputs,'OUTPUT_PROVENANCE');outputs.add(v+':result')
                require(r['failure_policy']=={'mode':'STOP','max_retries':0},'BOUNDED_RETRY')
                if kind=='READ':
                    require(cap in READS and READS[cap] in READ_PARAMS,'READ_CAPABILITY')
                    tool=read_tool(cap,r['parameters'])
                    self.registry.check_bank(owner,CapabilityManager.READ_MAP[tool])
                    keys=READ_PARAMS[tool]
                elif kind=='PROPOSE_ACTION':
                    require(cap in {'transfer.propose','service.propose','wealth.propose'},'PROPOSAL_CAPABILITY')
                    self.registry.check_bank(owner,cap);keys=PARAMS[cap];writes.append(v)
                else:
                    require(cap in SPECIAL.get(kind,{}).get(owner,set()),'SEMANTIC_CAPABILITY');keys=PARAMS[cap]
                require(isinstance(r['parameters'],dict) and set(r['parameters'])==keys,'PARAMETER_SCHEMA')
                refs=r['input_refs'];require(isinstance(refs,list) and len(refs)==len(set(refs)) and set(refs)==set(r['parameters'].values()),'INPUT_REFS')
                for ref in refs:
                    require(isinstance(ref,str) and (ref in inputs or ref.endswith(':result') and ref[:-7] in ancestors[v]),'PROVENANCE_OR_DEPENDENCY')
                if cap=='split.request':
                    require(all(ref in inputs for ref in refs),'SPLIT_PARAMETERS_REQUIRE_EXPLICIT_INPUT')
                    from backend.banking.split import allocate
                    values={k:inputs[ref] for k,ref in r['parameters'].items()}
                    allocate(values['amount'],values['participants'],values['method'],values['values'])
                    import math
                    require(type(values['deadline']) in {int,float} and math.isfinite(values['deadline']),'SPLIT_DEADLINE')
                    writes.append(v)
                for param,expected in {'records':{'transactions.read'},'authority':{'subscriptions.read'},'candidates':{'finance.recurring'}}.items():
                    if param in r['parameters']:
                        ref=r['parameters'][param]
                        require(ref.endswith(':result') and ref[:-7] in ancestors[v] and lookup[ref[:-7]]['capability'] in expected,'AUTHORITATIVE_FACT_PROVENANCE')
                if cap=='wealth.budget_plan':
                    ref=r['parameters']['cancellation']
                    require(ref.endswith(':result') and ref[:-7] in ancestors[v] and lookup[ref[:-7]]['node_type']=='PROPOSE_ACTION','SAVINGS_RECEIPT_PROVENANCE')
                condition=r['condition']
                if condition is not None:
                    require(isinstance(condition,dict) and set(condition)=={'ref','equals'} and type(condition['equals']) is bool,'CONDITION_SCHEMA')
                    ref=condition['ref'];require(isinstance(ref,str) and ref.endswith(':result') and ref[:-7] in ancestors[v] and lookup[ref[:-7]]['node_type'] in {'BRANCH','DECIDE','WAIT_USER'},'CONDITION_PROVENANCE')
                # Values are trusted runtime inputs or referenced output, never model-authored bank state.
                if kind=='PROPOSE_ACTION':
                    require(all(ref in inputs for ref in refs),'ACTION_PARAMETERS_REQUIRE_EXPLICIT_INPUT')
                    values={k:inputs[ref] for k,ref in r['parameters'].items()}
                    if cap=='transfer.propose':
                        require(type(values['amount']) is int and values['amount']>0 and values['currency'] in {'CNY','GBP','EUR'},'ACTION_AMOUNT')
                    else:
                        spec=ACTION_SPECS.get(values['action_type']);require(spec and spec['agent']==owner,'ACTION_OWNER')
                        from backend.banking.actions import validate_parameters
                        validate_parameters(values['action_type'],values['values'])
                require(not any(x in canonical(r).lower() for x in ('https://','http://','__import__','eval(','exec(','sqlite','authorization_id','action_hash','contract_id')),'RAW_AUTHORITY_OR_CODE')
            for i,a in enumerate(writes):
                for b in writes[i+1:]: require(a in ancestors[b] or b in ancestors[a],'UNSAFE_PARALLEL_WRITES')
            return deepcopy(graph)
        except Rejected: raise
        except Exception: raise Rejected('REJECT_PLAN','INVALID_GRAPH_VALUE') from None

class SecurityExpansionCompiler:
    def __init__(self, registry): self.validator=TaskGraphValidator(registry)

    def compile(self, graph, inputs):
        graph=self.validator.validate(graph,inputs)
        terminal={r['node_id']:r['node_id']+'XRECEIPT' if r['node_type']=='PROPOSE_ACTION' else r['node_id'] for r in graph['nodes']}
        steps=[]
        for r in graph['nodes']:
            deps=[terminal[d] for d in r['dependencies']]
            steps.append({'step_id':r['node_id'],'name':r['node_type'],'owner':r['owner'],'dependencies':deps,'status':'PENDING'})
            if r['node_type']=='PROPOSE_ACTION':
                previous=r['node_id']
                for kind in SAFETY:
                    step=r['node_id']+'X'+kind
                    steps.append({'step_id':step,'name':kind,'owner':OWNERS[kind],'dependencies':[previous],'status':'PENDING'})
                    previous=step
        if len({s['step_id'] for s in steps})!=len(steps): raise Rejected('REJECT_PLAN','COMPILED_ID_COLLISION')
        return {'schema_version':1,'semantic':graph,'steps':steps,'authority':'DETERMINISTIC_COMPILER'}
