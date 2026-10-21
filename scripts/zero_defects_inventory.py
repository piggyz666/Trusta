"""Capture a reproducible, secret-free source/environment inventory."""
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/zero-defects'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    source={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('backend','scripts','tests','benchmarks','contracts')
            for p in (ROOT/folder).rglob('*') if p.suffix in {'.py','.sol'}}
    target=OUT/'source-before.json'
    if target.exists():raise RuntimeError('Inventory already frozen')
    target.write_text(json.dumps(source,indent=2),encoding='utf-8')
    environment={'python':platform.python_version(),'platform':platform.platform(),
        'git_checkout':(ROOT/'.git').exists(),
        'packages':sorted(({'name':d.metadata['Name'],'version':d.version} for d in importlib.metadata.distributions()),key=lambda d:d['name'].lower())}
    (OUT/'environment.json').write_text(json.dumps(environment,indent=2),encoding='utf-8')
    print(json.dumps({'source_files':len(source),'python':environment['python'],'git_checkout':environment['git_checkout']}))

if __name__=='__main__':main()
