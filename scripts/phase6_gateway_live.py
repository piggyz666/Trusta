"""Actual Gateway/Router/Graph path using ONLY isolated synthetic bank data."""
import sys,json,tempfile,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from urllib.request import getproxies
from backend.config import Config
from backend.service import TrustaService
from backend.llm.configuration import ProviderConfig,LLMSettings
from backend.llm.gateway import LLMGateway
from backend.llm.providers.gemini import GeminiProvider

def main():
    model=os.getenv('GEMINI_MODEL','gemini-3.1-flash-lite')
    if getproxies().get('https'):os.environ['PHASE6_SYNTHETIC_PROXY']=getproxies()['https']
    # Reviewed solely for this isolated synthetic fixture; not an organizational
    # approval to send real customers' restricted banking data to public models.
    cfg=ProviderConfig(name='synthetic-gemini',kind='gemini',model=model,endpoint='https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',api_key_env='GEMINI_API_KEY',locality='external',max_sensitivity='restricted',data_policy_reviewed=True,proxy_env='PHASE6_SYNTHETIC_PROXY' if os.getenv('PHASE6_SYNTHETIC_PROXY') else '')
    captures=[]
    class Capture(GeminiProvider):
        def infer(self,req):
            r=super().infer(req);captures.append({'messages':req.messages,'raw_output':r.text,'usage':r.usage});return r
    with tempfile.TemporaryDirectory() as root:
        s=TrustaService(Config(db_path=root+'/bank.sqlite',banking_version=5,communication_version=3,signing_key=b'phase6-synthetic-native-route-xx'))
        s.llm=LLMGateway(LLMSettings(mode='assisted',version='phase6-live',timeout_seconds=20,max_output_tokens=2048,providers=(cfg,)),s.db,s.audit,{cfg.name:Capture(cfg)})
        s.llm.identity_guard=s.crypto_registry.require_active
        t=s.graphs.propose('demo','Use Finance READ transactions.read with account_id from input:account and month from input:month, Finance ANALYZE finance.summary of READ output, then Supervisor REPORT. Do not propose any action.',{'input:account':'acct-demo','input:month':'2026-09'})
        calls=[json.loads(r['body']) for r in s.db.rows('SELECT body FROM llm_calls')]
        evidence={'scope':'ISOLATED_SYNTHETIC_FIXTURE; actual external API through Gateway/Router; no customer data or financial approval','provider':cfg.name,'model':cfg.model,'status':t['status'],'errors':t['errors'],'compiled_steps':t['steps'],'calls':calls,'captured_synthetic_only':captures,'bank_writes':len(s.db.rows('SELECT * FROM transactions')),'live_provider_called':bool(captures)}
    (ROOT/'docs/phase6-gateway-live.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    p=ROOT/'docs/model-router-live-evaluation.json';r=json.loads(p.read_text(encoding='utf-8'));r['actual_gateway_graph_trial']={k:v for k,v in evidence.items() if k not in {'captured_synthetic_only','compiled_steps'}};p.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':evidence['status'],'calls':len(calls),'external_called':bool(captures)}))
if __name__=='__main__':main()
