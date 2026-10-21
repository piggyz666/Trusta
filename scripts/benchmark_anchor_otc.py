"""Replay measured round telemetry; counterfactual deadlines are labeled.

No new network/model requests and no future data in online policy selection.
The oracle is computed only after the online replay is complete.
"""
from dataclasses import asdict
import json
from pathlib import Path
import statistics
import time
from backend.config import Config
from backend.service import TrustaService
from backend.collaboration.rounds import CollaborationRounds
from backend.collaboration.telemetry import CollaborationTelemetry
from backend.collaboration.otc import AdaptiveOTC,DeadlinePolicy


def main():
    root=Path('data/anchor-integration');b=root/'business'
    s=TrustaService(Config(db_path=str(b/'business.sqlite3'),signing_key=(b/'local-seal.key').read_bytes(),
                          banking_version=5,communication_version=3,strong_auth_secret='isolated-anchor-no-financial-writes'))
    history=CollaborationTelemetry(CollaborationRounds(s,None)).history()
    policy=DeadlinePolicy(1000000000,120000000000,120000000000,1,120,
                          (1000000000,30000000000,60000000000,120000000000))
    controller=AdaptiveOTC(policy);rows=[];runtimes=[]
    for index,current in enumerate(history):
        past=[r for r in history[:index] if r.configuration_hash==current.configuration_hash]
        started=time.perf_counter_ns()
        decision=controller.select(past,next_sequence=current.sequence,configuration_hash=current.configuration_hash)
        runtimes.append(time.perf_counter_ns()-started)
        static=controller.p95(past[:1]) if past else policy.safe_default_ns
        rolling=controller.p95(past[-policy.history_window:]) if past else policy.safe_default_ns
        y,valid=current.quorum()
        def cost(t):return controller.cost(t,int(not valid or y>t))
        rows.append({'sequence':current.sequence,'history_sequences':[r.sequence for r in past],
                     'actual_observed_quorum_ns':y,'observed_quorum':valid,'adaptive':decision,
                     'counterfactual_static_p95_ns':static,'counterfactual_rolling_p95_ns':rolling,
                     'full_window_cost':{'adaptive':cost(decision['deadline_ns']),'static_p95':cost(static),'rolling_p95':cost(rolling)}})
    output={'scope':'MEASURED_LOCAL_COORDINATOR_TELEMETRY_COUNTERFACTUAL_DEADLINE_REPLAY',
            'sample_size':len(history),'new_model_calls':0,'policy':asdict(policy),'rows':rows,
            'runtime_ns':{'median':statistics.median(runtimes),'max':max(runtimes)},
            'oracle':controller.oracle_offline(history),'limitations':[
                'Very small sample; no statistical performance or IID claim.',
                'Latency includes SDK identity verification and coordinator overhead, not LLM inference.',
                'P95 counterfactuals are replayed from measured out-of-band telemetry, not new live executions.']}
    (root/'otc-benchmark.json').write_text(json.dumps(output,sort_keys=True,indent=2),encoding='utf-8')
    print(json.dumps({'sample_size':len(history),'oracle':output['oracle'],'runtime_ns':output['runtime_ns']}))


if __name__=='__main__':main()
