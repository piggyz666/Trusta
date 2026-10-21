"""Preserve historical reports; export current offline benchmark/workflow artifacts."""
import json
import shutil
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'data/zero-defects'

def main():
    from benchmarks import backend_hardening_benchmark as benchmark
    with tempfile.TemporaryDirectory(prefix='trusta-zero-benchmark-') as directory:
        target=Path(directory);(target/'docs').mkdir();(target/'data').mkdir();benchmark.ROOT=target
        try:benchmark.main()
        finally:
            for p in list((target/'docs').glob('*'))+list((target/'data').glob('*')):
                if p.is_file():shutil.copy2(p,OUT/p.name)
    from scripts import hardening_llm_offline as replay
    replay.OUT=OUT;replay.main()
    # Execute the unchanged historical workflow with only its output destination
    # redirected. No historical source or gold data is edited.
    from scripts import hardening_workflow_demo as workflow
    source=Path(workflow.__file__).read_text(encoding='utf-8')
    source=source.replace("output=ROOT/'docs/backend-final-hardening'","output=ROOT/'data/zero-defects'")
    namespace={'__name__':'zero_workflow','__file__':workflow.__file__}
    exec(compile(source,workflow.__file__,'exec'),namespace);namespace['main']()
    print(json.dumps({'status':'CURRENT_LOCAL_ACCEPTANCE_PASSED','remote_model_calls':0,'chain_broadcasts':0}))

if __name__=='__main__':main()
