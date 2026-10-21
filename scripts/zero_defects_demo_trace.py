"""Local synthetic demo diagnostic; fixed error labels and source lines only."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.domain import Rejected
def trace(frame,event,arg):
    if event=='exception' and isinstance(arg[1],Rejected) and arg[1].code=='REJECTED_SCHEMA':
        print(Path(frame.f_code.co_filename).name,frame.f_lineno,frame.f_code.co_name,flush=True)
    return trace
if __name__=='__main__':
    from scripts.backend_complete_demo import run
    sys.settrace(trace)
    try:run()
    finally:sys.settrace(None)
