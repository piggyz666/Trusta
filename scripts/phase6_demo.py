"""Multi-identity HTTP collection and dynamic cross-budget graph in the local bank."""
import sys,json,tempfile,threading,time,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from urllib.request import Request,urlopen
from backend.config import Config
from backend.service import TrustaService
from backend.server import create_server
from backend.runtime.taskgraph import node
from backend.domain import digest

def configured(root):return TrustaService(Config(db_path=root+'/bank.sqlite',banking_version=5,communication_version=3,signing_key=b'phase6-demo-root-only-32-bytes-xx'))
def provision(s):
    with s.db.transaction() as conn:
        for u in ('alice','bob','carol'):
            conn.execute('INSERT INTO customers VALUES(?,?)',(u,u));conn.execute("INSERT INTO accounts VALUES(?,?,'CNY',100000,0,0)",('acct-'+u,u))
            conn.execute('INSERT INTO payees VALUES(?,?,?,0)',('payee-'+u,u,'Organizer'))
            conn.execute("INSERT INTO split_payee_routes VALUES(?,'acct-demo')",('payee-'+u,))
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=ROOT/'docs');args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as root:
        s=configured(root);provision(s);server=create_server(s,'unused',0,{u+'-local-demo':u for u in ('demo','alice','bob','carol')});threading.Thread(target=server.serve_forever,daemon=True).start()
        def http(u,path,body=None):
            req=Request(f'http://127.0.0.1:{server.server_port}'+path,headers={'Authorization':'Bearer '+u+'-local-demo','Content-Type':'application/json'},data=None if body is None else json.dumps(body).encode())
            with urlopen(req,timeout=15) as r:return json.load(r)
        try:
            bill=http('demo','/api/splits',{'account_id':'acct-demo','total_amount':10000,'participants':['alice','bob','carol']})
            http('demo','/api/splits/'+bill['id']+'/publish',{});timeline=[]
            for u in ('alice','bob','carol'):
                t=http(u,'/api/splits/'+bill['id']+'/prepare',{'source_account_id':'acct-'+u})
                done=http(u,'/api/splits/'+bill['id']+'/pay',{'action_hash':digest(t['proposed_actions'][0]),'approve':True})
                timeline.append({'payer':u,'amount':t['proposed_actions'][0]['amount'],'task_id':t['task_id'],'authorization_id':done['authorization']['authorization_id'],'action_hash':digest(t['proposed_actions'][0]),'receipt_id':done['receipt']['receipt_id'],'status':done['status'],'collection_status':http('demo','/api/splits/'+bill['id'])['status']})
            final=http('demo','/api/splits/'+bill['id']);aa={'scope':'LOCAL_SANDBOX_VERIFIED; real loopback HTTP identities, virtual bank money only','bill':final,'payments':timeline,'receiver_balance':s.gateway.read('transaction','demo','account.get',account_id='acct-demo')['balance'],'independent_authorizations':len({r['authorization_id'] for r in timeline}),'audit_chain_valid':s.audit.verify_chain()}
            aa['participant_balances']={u:s.gateway.read('transaction',u,'account.get',account_id='acct-'+u)['balance'] for u in ('alice','bob','carol')}
            aa['ledger_transfers']=[dict(r) for r in s.db.rows('SELECT * FROM transfers')]
            while s.checkpoints.generate_checkpoint() is not None:pass
            aa['receipt_proofs']=[s.records.query(r['payer'],receipt_id=r['receipt_id']) for r in timeline]
            aa['checkpoint_public_keys']=[s.checkpoints.public_key(p['checkpoint'])[0] for p in aa['receipt_proofs']]
            from backend.audit_trust.records import verify_record
            assert all(verify_record(p,k) for p,k in zip(aa['receipt_proofs'],aa['checkpoint_public_keys']))
            assert sum(100000-v for v in aa['participant_balances'].values())==10000
        finally:server.shutdown();server.server_close()
        inputs={'input:account':'acct-demo','input:month':'2026-09','input:kind':'SUBSCRIPTION_CANCEL','input:target':'sub-stream','input:values':{},'input:budget':300000,'input:nextmonth':'2026-11'}
        graph={'schema_version':1,'nodes':[node('Records','READ','finance','transactions.read',parameters={'account_id':'input:account','month':'input:month'}),node('Subscriptions','READ','service','subscriptions.read'),node('Analyze','ANALYZE','finance','finance.summary',['Records'],{'records':'Records:result'}),node('Cancel','PROPOSE_ACTION','service','service.propose',['Analyze','Subscriptions'],{'account_id':'input:account','action_type':'input:kind','target_id':'input:target','values':'input:values'}),node('Budget','ANALYZE','wealth','wealth.budget_plan',['Cancel'],{'budget':'input:budget','cancellation':'Cancel:result','month':'input:nextmonth'}),node('Report','REPORT','supervisor','graph.report',['Budget'])]}
        t=s.graphs.create('demo','analyze, confirm cancellation, plan next-month budget',graph,inputs);r=t['graph_state']['actions']['Cancel']
        paused={'status':t['status'],'wealth_started':'Budget:result' in t['graph_state']['outputs'],'bank_changed':s.gateway.read('service','demo','subscription.get',resource_id='sub-stream')['status']}
        s=configured(root);done=s.graphs.authorize(t['task_id'],'demo','Cancel',digest(r['action']),True)
        demo={'scope':'LOCAL_SANDBOX_VERIFIED','semantic_graph':graph,'compiled_steps':done['steps'],'before_approval':paused,'after_restart_and_approval':{'status':done['status'],'budget':done['result'].get('Budget:result'),'receipts':done['receipts']},'financial_path':'BankActionIR -> Capability -> Policy -> Authorization -> Contract -> Gateway -> Adapter -> Verification -> Receipt/Audit'}
        (args.output_dir/'aa-demo.json').write_text(json.dumps(aa,ensure_ascii=False,indent=2),encoding='utf-8')
        (args.output_dir/'phase6-taskgraph-demo.json').write_text(json.dumps(demo,ensure_ascii=False,indent=2),encoding='utf-8')
        assert aa['bill']['status']=='COMPLETED' and aa['receiver_balance']==1010000 and demo['after_restart_and_approval']['status']=='COMPLETED'
        print(json.dumps({'aa':'COMPLETED','graph':done['status'],'budget':done['result'].get('Budget:result')},ensure_ascii=True))
if __name__=='__main__':main()
