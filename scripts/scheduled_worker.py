"""Persistent TIME trigger maintenance. Never retries uncertain financial writes."""
import argparse
import json
import time
from backend.config import Config
from backend.domain import Rejected
from backend.service import TrustaService


def run_once(service):
    scheduled=service.banking.tick()
    triggered=0
    for row in service.db.rows("SELECT DISTINCT owner FROM event_plans WHERE status='PENDING' AND execute_at<=?",(time.time(),)):
        triggered+=service.events.tick(row['owner'])['processed']
    # No event parameters, financial facts or authentication material in worker output.
    return {'scheduled':scheduled,'events_processed':triggered}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--watch",action="store_true")
    parser.add_argument("--interval",type=int,default=5)
    args=parser.parse_args()
    if not 1<=args.interval<=60:
        parser.error("interval must be 1..60 seconds")
    config=Config.local()
    if config.banking_version!=5 or config.communication_version!=3:
        raise Rejected("PHASE5_DISABLED")
    # An independent worker must not mark the server's live workflows as interrupted.
    service=TrustaService(config,recover_workflows=False)
    try:
        while True:
            print(json.dumps(run_once(service)),flush=True)
            if not args.watch:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__=="__main__":
    main()
