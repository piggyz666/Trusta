import json,tempfile,threading,unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from backend.server import create_server
from backend.domain import digest
from tests.phase6_helpers import service,seed_participants
from tests.test_phase6_graph import read_graph

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=service(self.tmp.name);seed_participants(self.s)
        self.identities={'organizer-token':'demo','alice-token':'alice','bob-token':'bob','carol-token':'carol'}
        self.http=create_server(self.s,'unused',0,self.identities);self.identities['alice-token']='demo'
        threading.Thread(target=self.http.serve_forever,daemon=True).start();self.addCleanup(self.http.server_close);self.addCleanup(self.http.shutdown)
    def request(self,user,path,body=None):
        req=Request(f'http://127.0.0.1:{self.http.server_port}'+path,headers={'Authorization':'Bearer '+('organizer-token' if user=='demo' else user+'-token'),'Content-Type':'application/json'},data=None if body is None else json.dumps(body).encode())
        try:
            with urlopen(req,timeout=10) as response:return response.status,json.load(response)
        except HTTPError as error:return error.code,json.load(error)
    def bill(self):
        code,b=self.request('demo','/api/splits',{'account_id':'acct-demo','total_amount':10000,'participants':['alice','bob','carol']});self.assertEqual(code,201)
        return self.request('demo','/api/splits/'+b['id']+'/publish',{})[1]
    def test_multi_identity_http_collection_real_credit(self):
        b=self.bill();tasks=[]
        for user in ['alice','bob','carol']:
            code,t=self.request(user,'/api/splits/'+b['id']+'/prepare',{'source_account_id':'acct-'+user});self.assertEqual(code,200);tasks.append(t)
            code,t=self.request(user,'/api/splits/'+b['id']+'/pay',{'action_hash':digest(t['proposed_actions'][0]),'approve':True});self.assertEqual(code,200);self.assertEqual(t['status'],'COMPLETED')
        b=self.request('demo','/api/splits/'+b['id'])[1];self.assertEqual(b['status'],'COMPLETED');self.assertEqual(self.s.gateway.read('transaction','demo','account.get',account_id='acct-demo')['balance'],1010000)
        self.assertEqual(len({t['authorization']['authorization_id'] for t in tasks}),3)
    def test_client_cannot_choose_identity(self):
        code,_=self.request('alice','/api/splits',{'user_id':'demo','account_id':'acct-demo','total_amount':100,'participants':['bob','carol']});self.assertEqual(code,400)
        b=self.bill();code,_=self.request('alice','/api/splits/'+b['id']+'/prepare',{'source_account_id':'acct-bob'});self.assertNotEqual(code,200)
    def test_cross_participant_task_and_action_denied(self):
        b=self.bill();_,t=self.request('alice','/api/splits/'+b['id']+'/prepare',{'source_account_id':'acct-alice'})
        code,_=self.request('bob','/api/tasks/'+t['task_id']);self.assertEqual(code,403)
        code,_=self.request('bob','/api/tasks/'+t['task_id']+'/authorize',{'action_hash':digest(t['proposed_actions'][0]),'approve':True});self.assertEqual(code,403)
    def test_graph_http_and_disabled_model_endpoint(self):
        code,t=self.request('demo','/api/graphs',{'goal':'read report','graph':read_graph(),'inputs':{'input:account':'acct-demo','input:month':'2026-09'}});self.assertEqual(code,201);self.assertEqual(t['status'],'COMPLETED')
        code,t=self.request('demo','/api/graphs/propose',{'goal':'report','inputs':{}});self.assertEqual(code,201);self.assertEqual(t['status'],'BLOCKED')
