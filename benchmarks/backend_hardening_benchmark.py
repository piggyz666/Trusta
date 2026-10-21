"""Actual local isolation, recovery, HTTP contention and evidence measurements."""
import io
import json
import os
from pathlib import Path
import statistics
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from backend.audit_trust.records import verify_record
from backend.domain import digest
from backend.runtime.finance_transport import FinanceTransport
from backend.runtime.handoff_evidence import verify_bundle
from backend.runtime.taskgraph import node
from backend.server import create_server
from tests.phase6_helpers import service
from tests.test_phase6_graph import read_graph

ROOT=Path(__file__).resolve().parents[1]
def write(name,value):
    (ROOT/'docs'/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def latency(values):
    ordered=sorted(values)
    return {'samples':len(values),'p50_ms':statistics.median(values),'p95_ms':ordered[max(0,int(.95*len(values))-1)],
        'max_ms':max(values),'raw_ms':values}
class Results(unittest.TextTestResult):
    def startTest(self,test):super().startTest(test);self.started=time.perf_counter()
    def stopTest(self,test):
        self.rows.append({'id':test.id(),'passed':not any(t is test for t,e in self.failures+self.errors),
            'duration_ms':(time.perf_counter()-self.started)*1000})
        super().stopTest(test)
    def __init__(self,*args):super().__init__(*args);self.rows=[]

def main():
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in
        ('tests.test_backend_hardening','tests.test_backend_recovery'))
    stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,resultclass=Results,verbosity=2).run(suite)
    (ROOT/'data'/'hardening-security-recovery-run.txt').write_text(stream.getvalue(),encoding='utf-8')
    write('agent-communication-security-benchmark.json',{'classification':'PROCESS_ISOLATION_VERIFIED' if os.name=='nt' and result.wasSuccessful() else 'FAILED_OR_UNSUPPORTED',
        'method':'actual unittest attack attempts; original assertions retained','tests':result.testsRun,'passed':result.wasSuccessful(),
        'skips':len(result.skipped),'cases':result.rows,'scope':'Finance GENERAL_GRAPH read/summary/anomalies only; trusted host broker; not HSM/all-Agent isolation'})
    write('agent-communication-recovery-report.json',{'passed':result.wasSuccessful(),'cases':[v for v in result.rows if 'RecoveryTests' in v['id']],
        'semantics':'sealed persistent read delivery; result reuse before ACK; bounded read retry; conservative default Core restart',
        'automatic_financial_write_retries':0,'automatic_claim_ttl_takeover':False,
        'limitations':['controlled crash injection, not power-cut filesystem testing','no automatic RUNNING workflow resume','terminal claim release is trusted operator maintenance']})
    if not result.wasSuccessful():raise SystemExit(1)
    with tempfile.TemporaryDirectory() as directory:
        s=service(directory);s.finance_transport=FinanceTransport(s);measure=[]
        try:
            for _ in range(8):
                before=time.perf_counter();task=s.graphs.create('demo','monthly report',read_graph(),{'input:account':'acct-demo','input:month':'2026-09'})
                assert task['status']=='COMPLETED' and task['result']['Sum:result']['net_spend']==542500
                measure.append((time.perf_counter()-before)*1000)
            # Read -> analysis -> proposal -> independent confirmation -> full safety chain -> report.
            graph=read_graph();graph['nodes'][-1:]=[
                node('Pay','PROPOSE_ACTION','transaction','transfer.propose',['Sum'],{'account_id':'input:account','recipient_name':'input:recipient','amount':'input:amount','currency':'input:currency'}),
                node('Report','REPORT','supervisor','graph.report',['Pay'])]
            task=s.graphs.create('demo','analysis then confirmed local transfer',graph,{'input:account':'acct-demo','input:month':'2026-09',
                'input:recipient':'张三','input:amount':1,'input:currency':'CNY'})
            assert task['status']=='WAITING_USER';record=task['graph_state']['actions']['Pay']
            task=s.graphs.authorize(task['task_id'],'demo','Pay',digest(record['action']),True)
            assert task['status']=='COMPLETED' and len(task['receipts'])==1
            bundle=s.handoffs.export(task['task_id'],'demo');registry=s.crypto_registry.snapshot()
            assert verify_bundle(bundle,{v['key_id']:v for v in registry['keys']},{v['agent_id']:v for v in registry['agents']})
            write('agent-communication-demo.json',bundle);write('agent-communication-trusted-registry.json',registry)
            audit_start=time.perf_counter()
            for i in range(1000):s.audit.append(task['task_id'],None,'BENCHMARK_EVENT','runtime',{'sequence':i})
            while s.checkpoints.generate_checkpoint() is not None:pass
            audit_ms=(time.perf_counter()-audit_start)*1000
            proof_times=[]
            for _ in range(20):
                start=time.perf_counter();proof=s.records.query('demo',receipt_id=task['receipts'][0]['receipt_id'])
                pem,_=s.checkpoints.public_key(proof['checkpoint']);assert verify_record(proof,pem)
                proof_times.append((time.perf_counter()-start)*1000)
            # SQLite busy contention: actual write lock then a different connection waits.
            lock=s.db.connect();lock.execute('BEGIN IMMEDIATE');waiting=[]
            def contend():
                start=time.perf_counter();s.audit.append(task['task_id'],None,'CONTENTION_TEST','runtime',{})
                waiting.append((time.perf_counter()-start)*1000)
            t=threading.Thread(target=contend);t.start();time.sleep(1);lock.rollback();lock.close();t.join(20)
            assert not t.is_alive() and len(waiting)==1
            http=create_server(s,'hardening-demo-only',port=0,identities={'hardening-demo-only':'demo','hardening-other-only':'other'})
            server_thread=threading.Thread(target=http.serve_forever,daemon=True);server_thread.start()
            def request(i):
                token='hardening-other-only' if i%4==0 else 'hardening-demo-only'
                url='http://127.0.0.1:'+str(http.server_address[1])+'/api/tasks/'+task['task_id']
                start=time.perf_counter()
                try:
                    with urllib.request.urlopen(urllib.request.Request(url,headers={'Authorization':'Bearer '+token}),timeout=20) as response:status=response.status;response.read()
                except urllib.error.HTTPError as error:status=error.code;error.read()
                return {'case':i,'status':status,'expected':403 if i%4==0 else 200,'duration_ms':(time.perf_counter()-start)*1000}
            try:
                with ThreadPoolExecutor(max_workers=8) as pool:http_rows=list(pool.map(request,range(80)))
            finally:http.shutdown();http.server_close();server_thread.join()
            assert all(v['status']==v['expected'] for v in http_rows)
            write('backend-reliability-benchmark.json',{'classification':'LOCAL_HOST_VERIFIED','process_graph':latency(measure),
                'http_clients':8,'http_requests':80,'http_passed':80,'http_cases':http_rows,'http_latency':latency([v['duration_ms'] for v in http_rows]),
                'sqlite_write_contention_ms':waiting,'audit_append_and_checkpoint':{'events':1000,'duration_ms':audit_ms},
                'receipt_proof_query_verify':latency(proof_times),'audit_chain_valid':s.audit.verify_chain(),
                'local_transfer_receipts':1,'external_bank_calls':0,'limitations':['local loopback and synthetic bank','not production throughput or availability','process cold startup included','human confirmation think-time excluded']})
        finally:s.finance_transport.close()

if __name__=='__main__':main()
