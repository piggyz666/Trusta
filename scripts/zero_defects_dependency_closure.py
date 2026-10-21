"""Audit the installed product dependency closure separately from host tools."""
import importlib.metadata as metadata
import json
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/zero-defects'
def main():
    pending=[('cryptography',set()),('eth-account',set()),('py-solc-x',set()),('eth-tester',{'py-evm'})]
    seen=set();versions={};edges=[]
    while pending:
        name,extras=pending.pop();name=canonicalize_name(name);key=(name,tuple(sorted(extras)))
        if key in seen:continue
        seen.add(key);dist=metadata.distribution(name);versions[name]=dist.version
        for raw in dist.requires or []:
            req=Requirement(raw)
            if req.marker and not any(req.marker.evaluate({'extra':extra}) for extra in extras|{''}):continue
            edges.append({'from':name,'to':canonicalize_name(req.name),'constraint':str(req.specifier)})
            pending.append((req.name,set(req.extras)))
    (OUT/'product-dependency-closure-final.json').write_text(json.dumps({'versions':versions,'edges':edges},indent=2),encoding='utf-8')
    (OUT/'product-dependencies-final.txt').write_text(''.join(f'{k}=={v}\n' for k,v in sorted(versions.items())),encoding='utf-8')
    print(json.dumps({'dependency_count':len(versions),'scope':'installed product optional integration closure'}))
if __name__=='__main__':main()
