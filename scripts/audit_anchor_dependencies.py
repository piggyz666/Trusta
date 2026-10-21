"""Read-only advisory inventory of the newly introduced Java runtime jars.

OSV matches indicate dependency advisories, not automatically exploitable
application vulnerabilities. No package installation or automatic upgrades.
"""
import json
from pathlib import Path
import urllib.request
import zipfile


def main():
    packages={}
    for jar in Path('data/anchor-integration/toolchain/weid-lib').glob('*.jar'):
        with zipfile.ZipFile(jar) as archive:
            for name in archive.namelist():
                if name.startswith('META-INF/maven/') and name.endswith('/pom.properties'):
                    props={}
                    for line in archive.read(name).decode('utf-8',errors='replace').splitlines():
                        if '=' in line and not line.startswith('#'):
                            k,v=line.split('=',1);props[k]=v
                    if {'groupId','artifactId','version'}<=set(props):
                        key=props['groupId']+':'+props['artifactId']
                        packages[(key,props['version'])]={'package':{'ecosystem':'Maven','name':key},'version':props['version']}
    queries=list(packages.values());results=[];errors=[]
    for start in range(0,len(queries),50):
        request=urllib.request.Request('https://api.osv.dev/v1/querybatch',data=json.dumps({'queries':queries[start:start+50]}).encode(),
                                       headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=30) as response:result=json.load(response)
            results.extend(result['results'])
        except (OSError,ValueError,KeyError) as exc:
            errors.append(type(exc).__name__);results.extend([{'not_evaluated':True}]*len(queries[start:start+50]))
    inventory=[query|result for query,result in zip(queries,results)]
    report={'source':'https://api.osv.dev/v1/querybatch','scope':'JAVA_SDK_DEPENDENCY_ADVISORIES_NOT_EXPLOITABILITY_VERDICTS',
            'packages':inventory,'errors':errors,'matched_packages':sum(bool(r.get('vulns')) for r in results),
            'total_packages':len(queries)}
    Path('data/anchor-integration/dependency-advisories.json').write_text(json.dumps(report,sort_keys=True,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='packages'}))


if __name__=='__main__':main()
