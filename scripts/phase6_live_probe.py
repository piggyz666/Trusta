import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import os,time,json
from backend.llm.configuration import ProviderConfig
from backend.llm.providers.gemini import GeminiProvider
from backend.llm.contracts import ProviderRequest,InferenceError
model=os.getenv('GEMINI_MODEL','gemini-2.5-flash-lite')
provider=GeminiProvider(ProviderConfig(name='gemini-live',kind='gemini',model=model,endpoint='https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',api_key_env='GEMINI_API_KEY',locality='external',proxy_env='HTTPS_PROXY' if os.getenv('HTTPS_PROXY') else ''))
start=time.time();result={'provider':'gemini','model':model,'synthetic_data_only':True}
try:
    r=provider.infer(ProviderRequest(({'role':'system','content':'Return JSON only. Translate a banking request into task_type, needs_clarification, parameters. Never execute.'},{'role':'user','content':'从 acct-demo 给张三转账100元人民币'}),{},512,20))
    result.update(status='SUCCEEDED',raw_output=r.text,usage=r.usage)
except InferenceError as e: result.update(status='FAILED',error_code=e.code)
result['latency_ms']=round((time.time()-start)*1000)
Path('data/phase6-live-probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='raw_output'},ensure_ascii=True))
