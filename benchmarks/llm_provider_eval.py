"""Same-data evaluator, using the unified provider boundary.

Default: replay archived REAL provider responses, zero paid API calls. This
does not claim fresh model capability. No budget reset and no bank approvals.
Historical prompts, datasets, raw responses and scoring are never rewritten.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmarks.glm_evaluation import (Budget, CapturingProvider, PRICES, configuration,
                                     paired_request, request_document, sha, summarize, save)
from benchmarks.final_live_evaluation import evaluate
from backend.domain import canonical
from backend.llm.contracts import InferenceError
from llm.bridge import BackendProviderBridge
from llm.providers import create_provider

ARCHIVE = ROOT / 'data/model-evaluation/glm'
OUT = ROOT / 'data/model-evaluation/provider-agnostic'


def stored_provider(config, frozen_request, stored, captures):
    adapter = create_provider(config.kind)
    transport = adapter.transport_class

    class StoredTransport(transport):
        def infer(self, request):
            request = paired_request(request)
            if sha(request_document(request)) != sha(frozen_request):
                raise InferenceError('EVALUATION_INPUT_DRIFT')
            if sha(self.body(request)) != sha(stored['transport_body']):
                raise InferenceError('EVALUATION_WIRE_DRIFT')
            captures.append(copy.deepcopy(stored))
            if stored['raw_response'] is None:
                raise InferenceError(stored['error'] or 'LLM_PROVIDER_PROTOCOL')
            return self.parse(copy.deepcopy(stored['raw_response']))

    adapter.transport_class = StoredTransport
    return BackendProviderBridge(config, provider=adapter)


def run(provider, archive=ARCHIVE, output=OUT):
    archive, output = Path(archive), Path(output)
    config = configuration(provider)
    rows = []
    source_hashes = {}
    for filename in ('freeze.json', 'context-freeze.json'):
        path = archive / filename
        frozen = json.loads(path.read_text(encoding='utf-8'))
        if sha(frozen['dataset']) != frozen['dataset_hash']:
            raise InferenceError('EVALUATION_DATASET_DRIFT')
        source_hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
        for case in frozen['dataset']:
            call_path = archive / 'calls' / f"{case['id']}-{provider}.json"
            stored = json.loads(call_path.read_text(encoding='utf-8'))
            request = frozen['requests'][case['id']]
            if stored['request_hash'] != sha(request):
                raise InferenceError('EVALUATION_INPUT_DRIFT')
            source_hashes[str(call_path.relative_to(archive))] = hashlib.sha256(call_path.read_bytes()).hexdigest()
            captures = []
            adapter = stored_provider(config, request, stored, captures)
            row = evaluate(case, provider, provider_config=config, provider=adapter,
                           captures=captures, amended_metrics=True)
            if len(captures) != 1:
                raise InferenceError('EVALUATION_REPLAY_NOT_CONSUMED')
            rows.append(row)
    result = {'scope': 'ARCHIVED_REAL_RESPONSE_REPLAY', 'new_api_calls': 0,
              'provider': provider, 'source_sha256': source_hashes,
              'summary': summarize(rows), 'results': rows,
              'limitation': 'Archived single remote response per case; local E2E replay, not a fresh live benchmark.'}
    output.mkdir(parents=True, exist_ok=True)
    target = output / f'{provider}-replay.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return result


def live_preflight(provider, frozen_documents, ledger):
    """Reserve capacity conservatively; dispatch still persists each reservation."""
    state = json.loads(Path(ledger).read_text(encoding='utf-8'))
    cases = [(case['id'], doc['requests'][case['id']]) for doc in frozen_documents for case in doc['dataset']]
    if any(a['case'] == case and a['provider'] == provider
           for case, _ in cases for a in state['attempts']):
        raise InferenceError('EVALUATION_ALREADY_CALLED')
    input_rate, output_rate = PRICES[provider]
    reserve = sum(((len(canonical(req).encode()) + 4096)
                   * input_rate + req['max_output_tokens'] * output_rate) / 1e6 for _, req in cases)
    if (state['requests'] + len(cases) > state['max_requests'] or
            state['reserved_usd'] + reserve > state['max_usd']):
        raise InferenceError('LLM_BUDGET_OR_DUPLICATE_STOP')
    return cases


def live(provider, archive=ARCHIVE, output=OUT, ledger=ARCHIVE / 'budget.json'):
    """Operator opt-in only; never creates, resets or enlarges a fee budget."""
    from scripts.estimate_sepolia_costs import load_local_environment
    archive, output, ledger = Path(archive), Path(output), Path(ledger)
    documents = [json.loads((archive / name).read_text(encoding='utf-8'))
                 for name in ('freeze.json', 'context-freeze.json')]
    for doc in documents:
        if sha(doc['dataset']) != doc['dataset_hash']:
            raise InferenceError('EVALUATION_DATASET_DRIFT')
    target = output / f'{provider}-live.json'
    if target.exists():
        raise InferenceError('PRESERVE_EXISTING_LIVE_RESULTS')
    # Shared with the historical runner, so both cannot spend the same ledger
    # concurrently. Failure/crash reservations remain spent in the old Budget.
    with (ARCHIVE / 'live.lock').open('x', encoding='utf-8') as lock:
        lock.write('unified-provider-evaluation')
    try:
        live_preflight(provider, documents, ledger)
        load_local_environment()
        config = configuration(provider)
        rows = []
        output.mkdir(parents=True, exist_ok=True)
        for doc in documents:
            for case in doc['dataset']:
                captures = []
                capture = CapturingProvider(config, case, doc['requests'][case['id']], Budget(ledger), captures)
                adapter = create_provider(config.kind)

                class CapturedTransport(adapter.transport_class):
                    def parse(self, payload):
                        capture.payload = payload
                        return super().parse(payload)

                adapter.transport_class = CapturedTransport
                capture.delegate = BackendProviderBridge(config, provider=adapter)
                row = evaluate(case, provider, provider_config=config, provider=capture,
                               captures=captures, amended_metrics=True)
                rows.append(row)
                save(target, {'scope': 'LIVE_MODEL_SYNTHETIC_DATA', 'provider': provider,
                              'completed': len(rows), 'summary': summarize(rows), 'results': rows})
        return json.loads(target.read_text(encoding='utf-8'))
    finally:
        (ARCHIVE / 'live.lock').unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', required=True, choices=('deepseek', 'glm', 'gemini'))
    parser.add_argument('--mode', choices=('replay', 'live'), default='replay')
    parser.add_argument('--archive', type=Path, default=ARCHIVE)
    parser.add_argument('--output', type=Path, default=OUT)
    parser.add_argument('--budget', type=Path, default=ARCHIVE / 'budget.json',
                        help='Existing explicitly approved ledger; never created/reset by this command')
    args = parser.parse_args()
    result = (run(args.provider, args.archive, args.output) if args.mode == 'replay' else
              live(args.provider, args.archive, args.output, args.budget))
    print(json.dumps({k: result[k] for k in ('scope', 'provider', 'new_api_calls', 'summary') if k in result},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
