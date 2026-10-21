"""Runtime-issued causal attestations, layered on V3 and existing Merkle audit."""
import json
from backend.domain import Rejected, canonical, digest
from backend.security.crypto import sign_object, verify_object
from backend.security.crypto_registry import AUDIT_SIGNER
from backend.security.message_v3 import MESSAGE_DOMAIN, FIELDS
from backend.audit_trust.records import verify_record

DOMAIN = 'TRUSTA-HANDOFF-EVIDENCE-V1'
SCHEMA = '''CREATE TABLE IF NOT EXISTS agent_handoff_evidence(
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL, step_id TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS handoff_task ON agent_handoff_evidence(task_id);
CREATE TRIGGER IF NOT EXISTS handoff_no_update BEFORE UPDATE ON agent_handoff_evidence
 BEGIN SELECT RAISE(ABORT,'immutable handoff'); END;
CREATE TRIGGER IF NOT EXISTS handoff_no_delete BEFORE DELETE ON agent_handoff_evidence
 BEGIN SELECT RAISE(ABORT,'immutable handoff'); END;'''


class HandoffEvidence:
    def __init__(self, service):
        self.s = service
        conn=service.db.connect()
        try: conn.executescript(SCHEMA)
        finally: conn.close()

    def record_in(self, conn, message, task, input_hash=None, process=None, transport_request=None):
        s=self.s
        step=next((v for v in task['steps'] if v['step_id']==message['step_id']),None)
        prior=[json.loads(row['body']) for row in conn.execute(
            'SELECT body FROM agent_handoff_evidence WHERE task_id=? ORDER BY rowid',(task['task_id'],))]
        # Capture actual runtime ordering, not agent/model-supplied parent claims.
        dependencies=step['dependencies'] if step else []
        parents=[digest(v) for v in prior if v['evidence']['message']['step_id'] in dependencies]
        if prior and not parents: parents=[digest(prior[-1])]
        identity=s.crypto_registry._identity(AUDIT_SIGNER,conn)
        identity,key=s.crypto_registry.resolve_in(AUDIT_SIGNER,identity['key_id'],conn)
        financial=[]
        for record in task.get('action_records',[]):
            if record.get('action'):
                financial.append({name:digest(record[name]) if record.get(name) else None
                    for name in ('action','authorization','contract','verification','receipt')})
        body={'schema_version':1,'evidence_id':message['message_id'],
            'task_id':task['task_id'],'step_id':message['step_id'],'task_revision':task['revision'],
            'message':message,'input_hash':input_hash,'output_hash':message['payload_hash'],
            'parents':parents,'dependencies':dependencies,
            'dependency_state_hash':digest(task['steps']),
            'financial_evidence_hashes':financial,'process':process,
            'transport_request':transport_request,
            'signer_key_id':key['key_id']}
        body['signature']=sign_object(s.crypto_registry.store.load(key),DOMAIN,body)
        audit_id=s.audit.append_in(conn,task['task_id'],None,'HANDOFF_EVIDENCE_RECORDED',
                                   'runtime',body)
        stored={'evidence':body,'audit_event_id':audit_id}
        conn.execute('INSERT INTO agent_handoff_evidence VALUES(?,?,?,?)',
            (message['message_id'],task['task_id'],message['step_id'],canonical(stored)))
        return stored

    def export(self, task_id, user_id):
        self.s._owned_task(task_id,user_id)
        while self.s.checkpoints.generate_checkpoint() is not None: pass
        entries=[]
        for row in self.s.db.rows('SELECT body FROM agent_handoff_evidence WHERE task_id=? ORDER BY rowid',(task_id,)):
            stored=json.loads(row['body'])
            stored['audit_proof']=self.s.records.query(user_id,audit_event_id=stored['audit_event_id'])
            entries.append(stored)
        task=self.s.blackboard.get(task_id);plan=None
        if task['task_type']=='GENERAL_GRAPH':
            state=self.s.graphs._state(task)
            compiled={'schema_version':1,'semantic':state['graph'],
                'steps':[v|{'status':'PENDING'} for v in task['steps']],
                'authority':'DETERMINISTIC_COMPILER'}
            row=self.s.db.one("SELECT body FROM audit_events WHERE json_extract(body,'$.task_id')=? AND json_extract(body,'$.event_type')='GRAPH_INSTALLED'",(task_id,))
            if not row: raise Rejected('HANDOFF_PLAN_EVIDENCE_MISSING')
            event=json.loads(row['body'])
            plan={'compiled':compiled,'audit_proof':self.s.records.query(user_id,audit_event_id=event['audit_event_id'])}
        return {'schema_version':1,'task_id':task_id,'entries':entries,'plan':plan,
                'authority':'RUNTIME_ATTESTATION_NOT_BANK_AUTHORIZATION'}


def verify_bundle(bundle, trusted_keys, trusted_capabilities):
    """Pure offline verification. Keys/capabilities MUST be pinned out of band.

    Verifies runtime attestation and inclusion, not the hidden payload's semantics,
    bank settlement, freshness of a historical registry, or external chain anchor.
    """
    try:
        if set(bundle)!={'schema_version','task_id','entries','authority','plan'} or type(bundle['schema_version']) is not int or bundle['schema_version']!=1 or not bundle['entries']:
            return False
        def verify_message(message):
            key=trusted_keys[message['sender_key_id']]
            identity=trusted_capabilities[message['sender_agent']]
            if (key['agent_id']!=message['sender_agent'] or identity['identity_uri']!=message['sender_identity']
                    or identity['capability_hash']!=message['capability_snapshot_hash']):
                raise Rejected('HANDOFF_IDENTITY_DENIED')
            verify_object(key['public_key'],MESSAGE_DOMAIN,
                          {k:v for k,v in message.items() if k!='signature'},message['signature'])
        plan=bundle['plan'];steps={}
        if plan:
            compiled=plan['compiled'];proof=plan['audit_proof'];cp=proof['checkpoint']
            if (trusted_keys[cp['signer_key_id']]['agent_id']!=AUDIT_SIGNER
                    or not verify_record(proof,trusted_keys[cp['signer_key_id']]['public_key'])
                    or proof['event']['task_id']!=bundle['task_id']
                    or proof['event']['event_type']!='GRAPH_INSTALLED'
                    or proof['event']['payload_hash']!=digest({'compiler_hash':digest(compiled),'semantic_hash':digest(compiled['semantic'])})):
                return False
            steps={v['step_id']:v for v in compiled['steps']}
        seen=set();ids=set()
        for stored in bundle['entries']:
            evidence=stored['evidence'];message=evidence['message']
            if (evidence['schema_version']!=1 or set(message)!=FIELDS
                    or evidence['task_id']!=bundle['task_id'] or message['task_id']!=bundle['task_id']
                    or evidence['step_id']!=message['step_id'] or evidence['evidence_id']!=message['message_id']
                    or evidence['task_revision']!=message['payload_version']
                    or evidence['output_hash']!=message['payload_hash']
                    or message['message_id'] in ids or len(set(evidence['parents']))!=len(evidence['parents'])
                    or not set(evidence['parents'])<=seen
                    or evidence['schema_version'] is True):
                return False
            if steps and (message['step_id'] not in steps or steps[message['step_id']]['owner']!=message['sender_agent'] or evidence['dependencies']!=steps[message['step_id']]['dependencies']):
                return False
            verify_message(message)
            signer=trusted_keys[evidence['signer_key_id']]
            if signer['agent_id']!=AUDIT_SIGNER: return False
            request=evidence['transport_request']
            if request:
                verify_message(request)
                if (request['sender_agent']!='supervisor' or request['receiver_agent']!=message['sender_agent']
                        or request['message_type']!='REQUEST' or request['task_id']!=message['task_id']
                        or request['step_id']!=message['step_id'] or request['payload_version']>message['payload_version']):
                    return False
            verify_object(signer['public_key'],DOMAIN,
                          {k:v for k,v in evidence.items() if k!='signature'},evidence['signature'])
            proof=stored['audit_proof'];checkpoint=proof['checkpoint']
            if (trusted_keys[checkpoint['signer_key_id']]['agent_id']!=AUDIT_SIGNER
                    or not verify_record(proof,trusted_keys[checkpoint['signer_key_id']]['public_key'])
                    or proof['event']['task_id']!=bundle['task_id']
                    or proof['event']['event_type']!='HANDOFF_EVIDENCE_RECORDED'
                    or proof['event']['payload_hash']!=digest(evidence)
                    or proof['event']['audit_event_id']!=stored['audit_event_id']):
                return False
            seen.add(digest({'evidence':evidence,'audit_event_id':stored['audit_event_id']}))
            ids.add(message['message_id'])
        return True
    except (Rejected,KeyError,ValueError,TypeError,RecursionError):
        return False
