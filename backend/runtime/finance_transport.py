"""Launch-bound Finance broker; the child cannot select a signer or bank tool."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time
from cryptography.hazmat.primitives import serialization
from backend.domain import Rejected,canonical,digest,uid
from backend.runtime.taskgraph import read_tool
from backend.security.crypto import load_public
from backend.runtime.windows_container import ContainerProcess

SCHEMA='''CREATE TABLE IF NOT EXISTS agent_delivery_jobs(
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL, step_id TEXT NOT NULL, state TEXT NOT NULL,
 body TEXT NOT NULL, UNIQUE(task_id,step_id));'''
ALLOWED={'accounts.read','balances.read','transactions.read','finance.summary','finance.anomalies'}


class FinanceTransport:
    def __init__(self,service):
        self.s=service;self.gate=threading.RLock();self.process=None
        self.key=hmac.new(service.config.signing_key,b'trusta-agent-outbox-v1',hashlib.sha256).digest()
        conn=service.db.connect()
        try: conn.executescript(SCHEMA)
        finally: conn.close()
        # An interrupted dispatch is safe to retry because this worker has no
        # write capabilities. A committed result is reused; writes never enter.

    def encode(self,body):
        return canonical({'body':body,'seal':hmac.new(self.key,canonical(body).encode(),hashlib.sha256).hexdigest()})

    def decode(self,row):
        wrapper=json.loads(row['body']);body=wrapper['body']
        if not hmac.compare_digest(wrapper['seal'],hmac.new(self.key,canonical(body).encode(),hashlib.sha256).hexdigest()):
            raise Rejected('AGENT_OUTBOX_TAMPERED')
        if row['state']!=body['state']: raise Rejected('AGENT_OUTBOX_TAMPERED')
        return body

    def _launch(self):
        if self.process is not None: self.process.close()
        directory=Path(os.environ['LOCALAPPDATA'])/'TRUSTA-Agent-Runtime'/'finance-v1'
        self.process=ContainerProcess(directory,timeout=self.s.config.agent_timeout_seconds)
        snapshot=self.s.crypto_registry.snapshot();identities={v['agent_id']:v for v in snapshot['agents']}
        keys={}
        for key in snapshot['keys']:
            if key['agent_id']=='supervisor' and (key['status']=='ACTIVE' or key['status']=='VERIFY_ONLY' and key['verify_until']>int(time.time()*1000)):
                identity=identities[key['agent_id']]
                keys[key['key_id']]={'agent_id':key['agent_id'],'identity_uri':identity['identity_uri'],
                    'capability_hash':identity['capability_hash'],
                    'verify_until':key['verify_until'],
                    'point':load_public(key['public_key']).public_bytes(serialization.Encoding.X962,
                        serialization.PublicFormat.UncompressedPoint).hex()}
        ready=self.process.exchange({'bootstrap_public_keys':keys,
            'finance_config':{'anomaly_large_amount':self.s.config.anomaly_large_amount,
                'anomaly_duplicate_seconds':self.s.config.anomaly_duplicate_seconds,
                'currencies':sorted(self.s.finance.analysis_currencies),
                'default_currency':self.s.finance.default_currency}})
        if ready.get('ready') is not True or ready.get('pid')!=self.process.pid:
            self.close();raise Rejected('AGENT_BOOTSTRAP_FAILED')

    def close(self):
        if self.process: self.process.close();self.process=None

    def run(self,node,task,values):
        if node['owner']!='finance' or node['capability'] not in ALLOWED:
            raise Rejected('AGENT_TRANSPORT_CAPABILITY_DENIED')
        s=self.s;task_id=task['task_id'];step_id=node['node_id']
        with self.gate:
            fresh=s.blackboard.get(task_id)
            step=next((v for v in fresh['steps'] if v['step_id']==step_id),None)
            if (fresh['status']!='RUNNING' or not step or step['owner']!='finance'
                    or step['status']!='RUNNING' or fresh['user_id']!=task['user_id']):
                raise Rejected('AGENT_TRANSPORT_CONTEXT_DENIED')
            row=s.db.one('SELECT * FROM agent_delivery_jobs WHERE task_id=? AND step_id=?',(task_id,step_id))
            s.crypto_registry.require_active('finance')
            if row:
                job=self.decode(row)
                if job['input_hash']!=digest(values) or job['capability']!=node['capability']:
                    raise Rejected('AGENT_TRANSPORT_INPUT_CONFLICT')
                if job['state'] in {'RESULT_COMMITTED','APPLIED'}: return job['result']
            else:
                job={'id':uid(),'task_id':task_id,'step_id':step_id,'capability':node['capability'],
                     'input_hash':digest(values),'state':'PENDING','attempts':0}
            if job['attempts']>=2: raise Rejected('AGENT_DELIVERY_RETRY_EXHAUSTED')
            s.crypto_registry.require_active('finance')
            parent_hashes=[digest(json.loads(r['body'])) for r in s.db.rows(
                'SELECT body FROM agent_handoff_evidence WHERE task_id=?',(task_id,))]
            payload={'request_id':job['id'],'task_id':task_id,'step_id':step_id,'revision':fresh['revision'],
                     'capability':node['capability'],'values':values,'parent_hashes':parent_hashes}
            message=s.messages.sign('supervisor','finance',task_id,step_id,'REQUEST','handoff:'+job['id'],payload,payload_version=fresh['revision'])
            with s.db.transaction() as conn:
                latest=json.loads(conn.execute('SELECT body FROM tasks WHERE id=?',(task_id,)).fetchone()['body'])
                if latest['revision']!=fresh['revision']: raise Rejected('MESSAGE_STALE_PAYLOAD')
                s.messages.verify_in(message,payload,'finance',conn)
                s.messages.replay.consume_in(conn,'supervisor',message['nonce'],message['issued_at']/1000)
                job.update(state='DISPATCHED',attempts=job['attempts']+1,request=message,request_hash=digest(payload))
                conn.execute('INSERT INTO agent_delivery_jobs VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,body=excluded.body',
                    (job['id'],task_id,step_id,job['state'],self.encode(job)))
                s.audit.append_in(conn,task_id,None,'AGENT_REQUEST_DISPATCHED','runtime',message)
            # No DB transaction spans IPC. A launch-bound broker validates the
            # exact request, not child-selected roles, tools or customer IDs.
            self._launch()
            response=self.process.exchange({'message':message,'payload':payload})
            if node['capability'] in {'accounts.read','balances.read','transactions.read'}:
                expected={'request_id':job['id'],'capability':node['capability'],'values':values}
                if set(response)!={'read_request'} or response['read_request']!=expected:
                    self.close();raise Rejected('AGENT_BROKER_READ_DENIED')
                result=s.gateway.read('finance',task['user_id'],read_tool(node['capability'],values),**values)
                response=self.process.exchange({'bank_read':result,'request_id':job['id']})
            elif node['capability']=='finance.summary': result=s.finance.summarize(values['records'])
            else: result=s.finance.anomalies(values['records'])
            if (set(response)!={'request_id','result','result_hash','pid'} or response['pid']!=self.process.pid
                    or response['request_id']!=job['id'] or response['result']!=result or response['result_hash']!=digest(result)):
                self.close();raise Rejected('AGENT_RESULT_MISMATCH')
            # Agent output is not an authoritative bank fact: broker independently
            # checks the exact Gateway response or deterministic Finance result.
            job.update(state='RESULT_COMMITTED',result=result,process=self.process.security)
            with s.db.transaction() as conn:
                latest=json.loads(conn.execute('SELECT body FROM tasks WHERE id=?',(task_id,)).fetchone()['body'])
                if latest['revision']!=fresh['revision']: raise Rejected('MESSAGE_STALE_PAYLOAD')
                current=self.decode(dict(conn.execute('SELECT * FROM agent_delivery_jobs WHERE id=?',(job['id'],)).fetchone()))
                if current['state']!='DISPATCHED' or current['request']!=message: raise Rejected('AGENT_DELIVERY_CONFLICT')
                conn.execute('UPDATE agent_delivery_jobs SET state=?,body=? WHERE id=?',(job['state'],self.encode(job),job['id']))
                s.audit.append_in(conn,task_id,None,'AGENT_RESULT_COMMITTED','runtime',{'request_id':job['id'],'output_hash':digest(result),'process':job['process']})
            return result

    def apply_in(self,conn,message,payload,task):
        row=conn.execute('SELECT * FROM agent_delivery_jobs WHERE task_id=? AND step_id=?',(task['task_id'],message['step_id'])).fetchone()
        if not row: return None
        job=self.decode(dict(row))
        if job['state']!='RESULT_COMMITTED' or job['result']!=payload or message['sender_agent']!='finance':
            raise Rejected('AGENT_RESULT_CONTEXT_DENIED')
        job.update(state='APPLIED',return_message=message)
        conn.execute('UPDATE agent_delivery_jobs SET state=?,body=? WHERE id=?',(job['state'],self.encode(job),job['id']))
        return job
