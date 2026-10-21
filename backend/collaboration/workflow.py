"""Additive readonly workflow over the original six-Agent GraphRuntime.

No new Agent, bus, executor, payment tool or LLM endpoint is introduced.
Banking tasks outside this explicitly selected workflow remain unchanged.
"""
import json
from backend.domain import Rejected,digest
from backend.runtime.taskgraph import node,SecurityExpansionCompiler
from backend.audit_trust.records import verify_record
from backend.security.registry import CapabilityRegistry
from backend.runtime.handoff_evidence import verify_bundle as verify_handoffs


def readonly_graph():
    return {'schema_version':1,'nodes':[
        node('Read','READ','finance','transactions.read',parameters={'account_id':'input:account','month':'input:month'}),
        node('Summary','ANALYZE','finance','finance.summary',['Read'],{'records':'Read:result'}),
        node('Wealth','ANALYZE','wealth','wealth.recommend'),
        node('Balance','READ','transaction','balance.read',parameters={'account_id':'input:account'}),
        node('Report','REPORT','supervisor','graph.report',['Summary','Wealth','Balance'])]}


class ReadonlyCrosscheck:
    def __init__(self,service,rounds):self.s,self.rounds=service,rounds

    def run(self,owner,*,account_id,month,deadline,policy_id,model_commitments):
        agents=['finance','wealth','transaction']
        goal='只读核对消费、余额与理财建议；禁止资金操作'
        task_id=self.s.supervisor.receive(owner,goal,{'task_type':'GENERAL_GRAPH','needs_clarification':False})
        state=self.rounds.start(task_id,owner,agents,k=2,deadline=deadline,policy_id=policy_id,
                                model_commitments=model_commitments)
        inputs={'input:account':account_id,'input:month':month};graph=readonly_graph()
        task=self.s.graphs.create(owner,goal,graph,inputs,existing_task_id=task_id)
        if task['status']!='COMPLETED' or task['proposed_actions'] or task['receipts']:
            raise Rejected('ANCHOR_READONLY_BUSINESS_NOT_COMPLETED')
        business={'task_id':task_id,'inputs':inputs,'result':task['result'],
                  'compiler_hash':task['graph_state']['compiler_hash']}
        facts=digest(business);accepted=[]
        for agent,output,cap in [('finance','Summary:result','finance.summary'),('wealth','Wealth:result','wealth.recommend'),
                                  ('transaction','Balance:result','account.get_balance')]:
            assessment={'verdict':'CONSISTENT','facts_hash':facts,'result_hash':digest(task['result'][output]),'capability':cap}
            accepted.append({'agent':agent,**self.rounds.accept(state['round_id'],owner,
                self.rounds.sign(state['round_id'],owner,agent,assessment))})
        return {'state':state,'task':task,'business':business,'graph':graph,'accepted':accepted}

    def export_business(self,context,owner):
        business=context['business'];task_id=business['task_id']
        required=[('GRAPH_INSTALLED',{'compiler_hash':business['compiler_hash'],'semantic_hash':digest(context['graph'])})]
        events=[json.loads(r['body']) for r in self.s.db.rows("SELECT body FROM audit_events WHERE json_extract(body,'$.task_id')=?",(task_id,))]
        proofs=[]
        while self.s.checkpoints.generate_checkpoint() is not None:pass
        for kind,payload in required:
            matches=[e for e in events if e['event_type']==kind and e['payload_hash']==digest(payload)]
            if len(matches)!=1:raise Rejected('ANCHOR_BUSINESS_CAUSAL_EVIDENCE_MISSING')
            proofs.append({'payload':payload,'proof':self.s.records.query(owner,audit_event_id=matches[0]['audit_event_id'])})
        return {'version':1,'scope':'LOCAL_VIRTUAL_BANK_READONLY','business':business,'graph':context['graph'],
                'audit_proofs':proofs,'handoffs':self.s.handoffs.export(task_id,owner),
                'runtime_registry':self.s.crypto_registry.snapshot(),
                'financial_write_count':0,'bank_payment_receipt':'NOT_APPLICABLE_READONLY'}


def verify_business(bundle,round_bundle,pinned_key,*,pinned_registry_hash):
    try:
        b=bundle['business'];graph=readonly_graph();record=round_bundle['record']
        if (bundle['version']!=1 or bundle['scope']!='LOCAL_VIRTUAL_BANK_READONLY' or bundle['graph']!=graph
                or bundle['financial_write_count']!=0 or b['task_id']!=record['task_id']):return False
        compiled=SecurityExpansionCompiler(CapabilityRegistry(5)).compile(graph,b['inputs'])
        if digest(compiled)!=b['compiler_hash']:return False
        required=[('GRAPH_INSTALLED',{'compiler_hash':b['compiler_hash'],'semantic_hash':digest(graph)})]
        if len(bundle['audit_proofs'])!=len(required):return False
        for item,(kind,payload) in zip(bundle['audit_proofs'],required):
            proof=item['proof'];event=proof['event']
            if (not verify_record(proof,pinned_key) or item['payload']!=payload or event['task_id']!=b['task_id']
                    or event['event_type']!=kind or event['payload_hash']!=digest(payload)):return False
        registry=bundle['runtime_registry']
        if digest(registry)!=pinned_registry_hash:return False
        keys={k['key_id']:k for k in registry['keys']}
        identities={a['agent_id']:a for a in registry['agents']+[registry['audit_signer']]}
        if identities['audit-checkpoint']['public_key']!=pinned_key:return False
        if not verify_handoffs(bundle['handoffs'],keys,identities):return False
        entries=bundle['handoffs']['entries']
        if len(entries)!=len(graph['nodes']):return False
        by_step={entry['evidence']['step_id']:entry['evidence'] for entry in entries}
        if set(by_step)!={n['node_id'] for n in graph['nodes']}:return False
        for n in graph['nodes']:
            evidence=by_step[n['node_id']]
            if (evidence['output_hash']!=digest(b['result'][n['node_id']+':result'])
                    or evidence['financial_evidence_hashes']):return False
        outputs={'finance':'Summary:result','wealth':'Wealth:result','transaction':'Balance:result'}
        for evidence in round_bundle['evidence']:
            assessment=evidence['payload']['assessment'];agent=evidence['message']['sender_agent']
            if assessment['facts_hash']!=digest(b) or assessment['result_hash']!=digest(b['result'][outputs[agent]]):return False
        return True
    except (KeyError,TypeError,ValueError,Rejected):return False
