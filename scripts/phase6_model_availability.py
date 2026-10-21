import os,json,requests
from urllib.request import getproxies
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
def probe(model):
    try:
        response=requests.post('https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',headers={'x-goog-api-key':os.environ['GEMINI_API_KEY']},json={'contents':[{'parts':[{'text':'Return JSON containing ok:true.'}]}]},proxies=getproxies(),timeout=15)
        raw=response.json();error=raw.get('error',{});text=error.get('message','').lower()
        return {'model':model,'http_status':response.status_code,'error_status':error.get('status'),'returned_content':bool(raw.get('candidates')),'quota_exhausted':'quota' in text,'region_restricted':'location' in text or 'country' in text,'credential_invalid':'key not valid' in text,'usage':raw.get('usageMetadata')}
    except Exception:return {'model':model,'status':'SERVICE_UNAVAILABLE'}
rows=list(ThreadPoolExecutor(3).map(probe,['gemini-2.5-flash','gemini-3.1-flash-lite','gemini-3.5-flash']))
Path('data/phase6-model-availability.json').write_text(json.dumps(rows,indent=2),encoding='utf-8');print(rows)
