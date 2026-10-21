"""Standalone LLM journal verification CLI; keeps stdlib `trace` import intact.

Run: python trace/planning_trace.py --db <existing SQLite> --tail <retained hash>
No prompt bodies or model output are displayed.
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from llm.trace import LLMTraceStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--tail', required=True)
    args = parser.parse_args()
    if not Path(args.db).is_file() or not re.fullmatch('[0-9a-f]{64}', args.tail):
        parser.error('Existing journal and retained SHA-256 tail required')
    # Bypass initialization: verification cannot create tables or change WAL mode.
    store = LLMTraceStore.__new__(LLMTraceStore)
    store.path = str(Path(args.db).resolve())
    valid = store.verify(args.tail)
    print(json.dumps({'trace_integrity': 'PASS' if valid else 'FAIL',
                      'trusted_tail_required': True, 'scope': 'LLM_METADATA_ONLY'}))
    return 0 if valid else 1


if __name__ == '__main__':
    raise SystemExit(main())
