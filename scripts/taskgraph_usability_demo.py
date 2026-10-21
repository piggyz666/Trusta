"""Offline provider fixture, actual Gateway/Router/Core, temporary Virtual Bank."""
import json
from pathlib import Path
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.config import Config
from backend.domain import canonical
from backend.llm.configuration import LLMSettings, ProviderConfig
from backend.llm.contracts import ProviderResponse
from backend.llm.gateway import LLMGateway
from backend.runtime.taskgraph import node
from backend.service import TrustaService


def demo(directory):
    s=TrustaService(Config(db_path=directory+'/bank.sqlite',banking_version=5,
        communication_version=3,signing_key=b'usability-demo-synthetic-local-only'))
    class Fixture:
        output={}
        def infer(self,request):return ProviderResponse(canonical(self.output))
    p=Fixture()
    s.llm=LLMGateway(LLMSettings(mode='assisted',providers=(ProviderConfig('fixture',max_sensitivity='restricted'),)),s.db,s.audit,{'fixture':p})
    s.llm.identity_guard=s.crypto_registry.require_active
    p.output={'schema_version':1,'nodes':[],'clarification':{'reason':'MISSING_INPUT','missing_parameters':['month']}}
    question=s.graphs.propose('demo','分析账单',{'input:account':'acct-demo'})
    p.output={'schema_version':1,'nodes':[node('Read','READ','finance','transactions.read',parameters={'account_id':'input:account','month':'input:month'}),
        node('Analyze','ANALYZE','finance','finance.summary',['Read'],{'records':'Read:result'}),
        node('Report','REPORT','supervisor','graph.report',['Analyze'])],'clarification':None}
    report=s.graphs.propose('demo','已补充月份，分析账单',{'input:account':'acct-demo','input:month':'2026-09'})
    p.output={'schema_version':1,'nodes':[node('Transfer','PROPOSE_ACTION','transaction','transfer.propose',parameters={
        'account_id':'input:account','recipient_name':'input:recipient','amount':'input:amount','currency':'input:currency'}),
        node('Report','REPORT','supervisor','graph.report',['Transfer'])],'clarification':None}
    proposal=s.graphs.propose('demo','提出转账方案，等待审批',{'input:account':'acct-demo','input:recipient':'张三','input:amount':1100,'input:currency':'CNY'})
    writes=len(s.db.rows('SELECT * FROM transactions'))+len(s.db.rows('SELECT * FROM bank_operations'))
    assert question['result']['needs_clarification'] and not question['steps']
    assert report['status']=='COMPLETED' and proposal['status']=='WAITING_USER' and writes==0
    return {'scope':'OFFLINE_PROVIDER_FIXTURE; actual Gateway/Router/Core; temporary Virtual Bank',
        'real_model_called':False,'clarification':question['result'],'readonly_status':report['status'],
        'readonly_result':report['result'],'financial_proposal_status':proposal['status'],
        'financial_stages':[step['name'] for step in proposal['steps']],'bank_writes':writes,'passed':True}


def main():
    with tempfile.TemporaryDirectory() as directory:result=demo(directory)
    (ROOT/'docs/phase6-taskgraph-usability-demo.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:result[key] for key in ('passed','real_model_called','readonly_status','financial_proposal_status','bank_writes')}))


if __name__=='__main__':main()
