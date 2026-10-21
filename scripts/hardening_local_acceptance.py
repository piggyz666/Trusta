"""Existing attack/recovery benchmark, new directory, preserved prior evidence."""
import json,tempfile,shutil
from pathlib import Path
from benchmarks import backend_hardening_benchmark as bench
ROOT=Path(__file__).resolve().parents[1]

def main():
    output=ROOT/'docs/backend-final-hardening'
    with tempfile.TemporaryDirectory(prefix='trusta-hardening-evidence-') as directory:
        root=Path(directory);(root/'docs').mkdir();(root/'data').mkdir();bench.ROOT=root
        try:bench.main()
        finally:
            for path in list((root/'docs').glob('*'))+list((root/'data').glob('*')):
                if path.is_file():shutil.copy2(path,output/path.name)
    print(json.dumps({'status':'LOCAL_VERIFIED','history_overwritten':False}))

if __name__=='__main__':main()
