"""Compile only the separate FISCO provenance contract, never AuditAnchor."""
import hashlib
import json
from pathlib import Path
import solcx


def main():
    source=Path('contracts/CollaborationAnchor.sol').read_text(encoding='utf-8')
    compiled=solcx.compile_source(source,output_values=['abi','bin','bin-runtime'],solc_version='0.4.25',
                                 optimize=True)['<stdin>:CollaborationAnchor']
    artifact={'version':1,'compiler':'solc-0.4.25','source_sha256':hashlib.sha256(source.encode()).hexdigest(),
              'abi':compiled['abi'],'bytecode':compiled['bin'],'runtime':compiled['bin-runtime']}
    output=Path('data/anchor-integration/weidentity-lab/conf/roundanchor-artifact.json')
    output.write_text(json.dumps(artifact,sort_keys=True,indent=2),encoding='utf-8')
    print(json.dumps({'artifact_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
                      'runtime_bytes':len(compiled['bin-runtime'])//2}))


if __name__=='__main__':main()
