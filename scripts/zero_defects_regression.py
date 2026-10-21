"""Keep every test; isolate module processes to bound Windows socket usage."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/zero-defects'

def run(tag):
    before=json.loads((OUT/'source-before.json').read_text(encoding='utf-8'))
    changed=[name for name,sha in before.items() if name.startswith('tests\\') or name.startswith('tests/')
             if not (ROOT/name).exists() or hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=sha]
    if changed:raise RuntimeError('Original tests were changed: '+str(changed))
    started=time.monotonic();logs=OUT/(tag+'-modules');logs.mkdir(exist_ok=True)
    files=sorted((ROOT/'tests').glob('test_*.py'));results=[]
    def module(path):
        log=logs/(path.stem+'.txt');timed_out=False
        with log.open('w',encoding='utf-8') as stream:
            try:
                result=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p',path.name,'-v'],
                    cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,
                    env=dict(os.environ,PYTHONIOENCODING='utf-8'),timeout=600)
                code=result.returncode
            except subprocess.TimeoutExpired:code=124;timed_out=True
        text=log.read_text(encoding='utf-8')
        match=re.search(r'Ran (\d+) tests? in ([\d.]+)s',text)
        return {'module':path.name,'exit_code':code,'tests':int(match[1]) if match else 0,
            'skipped':len(re.findall(r'\.\.\. skipped',text)),'timed_out':timed_out,'log':str(log)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(module,path) for path in files]):
            results.append(future.result())
            (OUT/(tag+'-progress.json')).write_text(json.dumps({'modules_finished':len(results),'total_modules':len(files),
                'tests_finished':sum(r['tests'] for r in results),'failures':[r['module'] for r in results if r['exit_code']]}),encoding='utf-8')
    report={'method':'two concurrent isolated unittest module processes','workers':2,'modules':len(files),
        'tests':sum(r['tests'] for r in results),'skipped':sum(r['skipped'] for r in results),
        'duration_seconds':round(time.monotonic()-started,3),'original_test_files_unchanged':True,
        'passed':all(r['exit_code']==0 and r['tests']>0 for r in results),'results':sorted(results,key=lambda r:r['module'])}
    (OUT/(tag+'-result.json')).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    with (OUT/(tag+'-tests.txt')).open('w',encoding='utf-8') as stream:
        for r in report['results']:stream.write('\nMODULE '+r['module']+'\n'+Path(r['log']).read_text(encoding='utf-8'))
        stream.write('\nAGGREGATE '+json.dumps({k:v for k,v in report.items() if k!='results'})+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='results'}));return 0 if report['passed'] else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--tag',default='final');args=parser.parse_args()
    if not re.fullmatch('[a-z0-9-]{1,40}',args.tag):raise SystemExit('Invalid evidence tag')
    raise SystemExit(run(args.tag))
