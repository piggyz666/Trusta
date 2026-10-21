import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.phase6_llm_metrics import metric, summarize, validate_summary, write_artifacts


def report_fixture():
    rows = []
    for index, status in enumerate(("COMPLETED", "WAITING_USER", "BLOCKED", None)):
        rows.append({"id": f"case-{index}", "category": "transfer",
                     "status": "SUCCEEDED" if status else "FAILED",
                     "raw_result": {"parameters": {}} if status else None,
                     "intent_correct": bool(status), "parameters_correct": bool(status),
                     "clarification_correct": bool(status), "raw_unsafe": False if status else None,
                     "system_intent_correct": bool(status), "system_parameters_correct": bool(status),
                     "system_clarification_correct": bool(status),
                     "validator_result": "ACCEPT" if status else "REJECT",
                     "system_rejected": status is None, "core_status": status,
                     "task_completion": status == "COMPLETED" if status else None})
    return {"dataset_size": 4, "evaluated": 4, "results": rows, "core_bank_writes": 0,
            "detailed_metrics": {"unsafe_system_execution": {"numerator": 0, "denominator": 4, "rate": 0}}}


class MetricEvidenceTests(unittest.TestCase):
    def test_real_runner_timeout_counts_failure_and_does_not_invent_safe_output(self):
        from benchmarks.phase6_llm_evaluation import run_one
        from backend.llm.contracts import InferenceError
        class TimeoutProvider:
            def infer(self, request):
                raise InferenceError("LLM_TIMEOUT", True)
        case = {"id": "timeout", "category": "transfer", "prompt": "synthetic",
                "gold": {"task_type": "TRANSFER", "needs_clarification": False, "parameters": {}}}
        row = run_one(TimeoutProvider(), case)
        result = summarize({"dataset_size": 1, "evaluated": 1, "results": [row]})
        self.assertEqual(result["metrics"]["intent_correct"], 0)
        self.assertEqual(result["metrics"]["system_rejected"], 1)
        self.assertIsNone(result["metrics"]["raw_unsafe"])
        self.assertIsNone(row["raw_output"])

    def test_model_reply_with_invalid_envelope_is_not_a_transport_failure(self):
        from benchmarks.phase6_llm_evaluation import run_one
        from backend.llm.contracts import ProviderResponse
        class Provider:
            def infer(self, request):
                return ProviderResponse('{}')
        case = {"id": "bad-envelope", "category": "bill", "prompt": "synthetic",
                "gold": {"task_type": "BILL_ANALYSIS", "needs_clarification": False, "parameters": {}}}
        row = run_one(Provider(), case)
        self.assertEqual(row["status"], "SUCCEEDED")
        self.assertFalse(row["intent_correct"])
        self.assertTrue(row["system_rejected"])

    def test_failures_remain_in_accuracy_denominator(self):
        source = report_fixture()
        del source["results"][-1]["intent_correct"]
        result = summarize(source)
        self.assertEqual(result["metrics"]["intent_correct"], .75)
        self.assertEqual(result["detailed_metrics"]["raw_model"]["intent_correct"]["denominator"], 4)
        self.assertEqual(result["detailed_metrics"]["raw_model"]["intent_correct"]["unknown"], 1)

    def test_unknown_safety_is_not_reported_as_safe(self):
        result = summarize(report_fixture())
        check = result["detailed_metrics"]["raw_model"]["envelope_unsafe"]
        self.assertEqual(check, {"numerator": 0, "denominator": 3, "rate": 0, "unknown": 1})

    def test_waiting_and_business_blocked_are_not_completed(self):
        result = summarize(report_fixture())
        self.assertEqual(result["outcomes"]["accepted_waiting_user"], 1)
        self.assertEqual(result["outcomes"]["accepted_business_blocked"], 1)
        self.assertEqual(result["completion_rate"]["denominator"], 3)
        self.assertEqual(result["completion_rate"]["numerator"], 1)
        self.assertEqual(result["detailed_metrics"]["system_final"]["task_completion_all_cases"]["rate"], .25)

    def test_resource_flag_does_not_change_pure_model_safety(self):
        source = report_fixture()
        source["results"][0]["raw_unsafe"] = True
        source["results"][0]["resource_grounding"] = False
        result = summarize(source)
        self.assertEqual(result["detailed_metrics"]["raw_model"]["envelope_unsafe"]["numerator"], 0)
        self.assertEqual(result["detailed_metrics"]["candidate_validation"]["raw_unsafe"]["numerator"], 1)
        self.assertEqual(result["metrics"]["raw_unsafe"], 1 / 3)

    def test_stale_summary_is_rejected_and_repair_is_idempotent(self):
        result = summarize(report_fixture())
        self.assertEqual(summarize(result), result)
        result["metrics"]["system_rejected"] = 0
        with self.assertRaisesRegex(ValueError, "stale or inconsistent"):
            validate_summary(result)

    def test_inference_only_is_not_labelled_core_completion(self):
        source = report_fixture()
        for row in source["results"]:
            row.pop("validator_result")
            row["task_completion"] = None
        result = summarize(source)
        self.assertEqual(result["metric_contract"]["stage"], "INFERENCE_ONLY")
        self.assertIsNone(result["outcomes"]["accepted_candidates"])
        self.assertIsNone(result["detailed_metrics"]["system_final"]["task_completion"]["rate"])

    def test_partial_replay_and_duplicate_cases_are_rejected(self):
        source = report_fixture()
        source["results"][0].pop("validator_result")
        with self.assertRaises(ValueError):
            summarize(source)
        source = report_fixture()
        source["results"][1]["id"] = source["results"][0]["id"]
        with self.assertRaises(ValueError):
            summarize(source)

    def test_non_boolean_counts_and_wrong_evaluated_count_rejected(self):
        with self.assertRaises(ValueError):
            metric([{"flag": "false"}], "flag")
        source = report_fixture()
        source["evaluated"] = 3
        with self.assertRaises(ValueError):
            summarize(source)

    def test_gold_prompts_raw_outputs_and_decisions_are_preserved(self):
        source = report_fixture()
        source["results"][0].update(gold={"task_type": "TRANSFER"}, raw_prompt="original",
                                   raw_output="captured output", usage={"input_tokens": 9})
        before = copy.deepcopy(source)
        result = summarize(source)
        self.assertEqual(source, before)
        for row, repaired in zip(before["results"], result["results"]):
            self.assertEqual(row, {key: repaired[key] for key in row})

    def test_json_and_csv_are_derived_from_the_same_cases(self):
        result = summarize(report_fixture())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evaluation.json"
            write_artifacts(result, path)
            stored = json.loads(path.read_text(encoding="utf-8"))
            with path.with_suffix(".csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            validate_summary(stored)
            self.assertEqual(len(rows), 4)
            self.assertEqual(sum(row["system_rejected"] == "True" for row in rows), 1)
            self.assertEqual(sum(row["core_status"] == "WAITING_USER" for row in rows), 1)


if __name__ == "__main__":
    unittest.main()
