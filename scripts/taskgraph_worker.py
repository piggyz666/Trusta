"""Explicit local operator polling of bounded WAIT_TIME graphs. Never supplies approval."""
import argparse,time
from backend.config import Config
from backend.service import TrustaService
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--watch',action='store_true');parser.add_argument('--interval',type=int,default=5);args=parser.parse_args()
    if not 1<=args.interval<=60:parser.error('interval must be 1..60')
    s=TrustaService(Config.local())
    while True:
        import json
        for row in s.db.rows('SELECT body FROM tasks'):
            t=json.loads(row['body'])
            if t['task_type']=='GENERAL_GRAPH' and t['status']=='WAITING_TIME':s.graphs.advance(t['task_id'],t['user_id'])
        if not args.watch:return
        time.sleep(args.interval)
if __name__=='__main__':main()
