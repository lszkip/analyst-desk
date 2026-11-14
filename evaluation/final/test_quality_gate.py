"""Synthetic verifier tests, never measured agent-quality or real CI-run evidence."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from evaluation.final.quality_gate import Thresholds, digest, evaluate_gate, run_saved_gate


def fixtures(statuses=("completed", "completed"), numeric=(True, True), *, variants=None):
    """Minimal structurally valid test records, not a real run or saved evidence."""
    variants = variants or ["C2"] * len(statuses)
    jobs = [{"execution_id": f"UNIT_TEST_{i}", "case_id": f"TEST_{i}", "repeat": 1,
             "variant": variant, "split": "dev"} for i, variant in enumerate(variants)]
    manifest = {"schema_version": "final-experiments-v1", "software_test_mode": False,
                "jobs": jobs, "config": {"experiment_id": "UNIT_TEST_ONLY"}}
    manifest["manifest_sha256"] = digest(manifest)
    rows = []
    for job, status, number in zip(jobs, statuses, numeric):
        row = {**job, "manifest_sha256": manifest["manifest_sha256"], "status": status,
               "evaluation": {"numeric_success": number, "task_success": None},
               "policy_violation_attempts": 0, "elapsed_seconds": 1.0, "tool_calls": 4}
        if status == "completed":
            row["result"] = {"status": "completed", "note": {"body_markdown": "Synthetic unit test note", "citations": [{"evidence_id": "synthetic"}]}}
        rows.append(row)
    return make_summary(manifest, rows), manifest, rows


def make_summary(manifest, rows):
    from collections import Counter, defaultdict
    groups = defaultdict(list)
    for row in rows:
        groups[row["variant"]].append(row)
    summary = {"manifest_sha256": manifest["manifest_sha256"], "planned_executions": len(manifest["jobs"]),
               "recorded_executions": len(rows), "status_counts": dict(Counter(r["status"] for r in rows)), "by_variant": {}}
    for variant, group in groups.items():
        numeric = [r["evaluation"].get("numeric_success") for r in group]
        known = [v for v in numeric if type(v) is bool]
        summary["by_variant"][variant] = {
            "planned": len(group), "status_counts": dict(Counter(r["status"] for r in group)),
            "numeric_scored": len(known), "numeric_passes": sum(known), "numeric_passes_per_planned": sum(known)/len(group),
            "task_scored": 0, "task_passes": 0, "unadjudicated_task_outcomes": len(group),
            "policy_violation_attempts": sum(r["policy_violation_attempts"] for r in group)}
    return summary


def thresholds(completed=2, success="1", policy=0, errors=0):
    return Thresholds(completed, Decimal(success), policy, errors)


class QualityGateTests(unittest.TestCase):
    def test_consistent_observed_counts_meet_explicit_thresholds(self):
        score = evaluate_gate(*fixtures(), thresholds())
        self.assertEqual(score["exit_code"], 0)
        self.assertTrue(score["gate_passed"])
        self.assertEqual(score["observations"]["C2"]["unadjudicated_task_outcomes"], 2)
        self.assertIn("not risk-semantic", score["scope"])

    def test_bare_expected_labels_are_rejected(self):
        summary, manifest, rows = fixtures()
        summary["numeric_targets"] = [{"margin": "25.00"}]
        score = evaluate_gate(summary, manifest, rows, thresholds())
        self.assertEqual(score["exit_code"], 12)
        self.assertFalse(score["performance_claim"])

    def test_software_test_runs_cannot_claim_performance(self):
        summary, manifest, rows = fixtures()
        manifest["software_test_mode"] = True
        score = evaluate_gate(summary, manifest, rows, thresholds())
        self.assertEqual(score["exit_code"], 12)

    def test_dry_run_and_synthetic_markers_are_rejected(self):
        for flag in ("dry_run", "synthetic", "negative_control"):
            summary, manifest, rows = fixtures()
            rows[0][flag] = True
            self.assertEqual(evaluate_gate(summary, manifest, rows, thresholds())["exit_code"], 12)

    def test_no_zero_denominator_pass(self):
        summary, manifest, rows = fixtures((), ())
        score = evaluate_gate(summary, manifest, rows, thresholds(1, "0"))
        self.assertEqual(score["exit_code"], 13)
        self.assertFalse(score["gate_passed"])

    def test_changed_manifest_is_rejected(self):
        summary, manifest, rows = fixtures()
        manifest["config"]["experiment_id"] = "changed"
        self.assertEqual(evaluate_gate(summary, manifest, rows, thresholds())["exit_code"], 11)

    def test_summary_passes_cannot_exceed_observed_results(self):
        summary, manifest, rows = fixtures(numeric=(True, False))
        summary["by_variant"]["C2"]["numeric_passes"] = 2
        self.assertEqual(evaluate_gate(summary, manifest, rows, thresholds())["exit_code"], 11)

    def test_numeric_rate_is_per_planned_not_per_scored(self):
        summary, manifest, rows = fixtures(statuses=("completed", "blocked"), numeric=(True, None))
        summary["by_variant"]["C2"]["numeric_passes_per_planned"] = 1
        self.assertEqual(evaluate_gate(summary, manifest, rows, thresholds(1, "0.4"))["exit_code"], 11)

    def test_unrun_and_skipped_fail_even_at_zero_numeric_threshold(self):
        for status in ("unrun", "skipped"):
            score = evaluate_gate(*fixtures(statuses=("completed", status), numeric=(True, None)), thresholds(1, "0"))
            self.assertEqual(score["exit_code"], 13)

    def test_omitted_execution_cannot_improve_coverage(self):
        summary, manifest, rows = fixtures()
        rows.pop()
        summary = make_summary(manifest, rows)
        self.assertEqual(evaluate_gate(summary, manifest, rows, thresholds(1))["exit_code"], 13)

    def test_threshold_applies_to_each_variant_not_pooled_result(self):
        score = evaluate_gate(*fixtures(variants=["C1", "C2"]), thresholds(2))
        self.assertEqual(score["exit_code"], 13)
        self.assertEqual(len(score["failures"]), 2)

    def test_low_numeric_success_fails(self):
        score = evaluate_gate(*fixtures(numeric=(True, False)), thresholds(2, "0.75"))
        self.assertEqual(score["exit_code"], 14)

    def test_policy_violation_attempts_fail(self):
        summary, manifest, rows = fixtures()
        rows[0]["policy_violation_attempts"] = 1
        summary = make_summary(manifest, rows)
        self.assertEqual(evaluate_gate(summary, manifest, rows, thresholds())["exit_code"], 15)

    def test_errors_timeouts_and_interruptions_all_count(self):
        for status in ("error", "timeout", "interrupted"):
            score = evaluate_gate(*fixtures(statuses=("completed", status), numeric=(True, False)), thresholds(1, "0", errors=0))
            self.assertEqual(score["exit_code"], 16)

    def test_completed_without_numeric_score_is_coverage_failure(self):
        score = evaluate_gate(*fixtures(numeric=(True, None)), thresholds(2, "0"))
        self.assertEqual(score["exit_code"], 13)

    def test_success_without_final_note_is_invalid_evidence(self):
        summary, manifest, rows = fixtures()
        del rows[0]["result"]["note"]
        self.assertEqual(evaluate_gate(summary, manifest, rows, thresholds())["exit_code"], 11)

    def test_invalid_thresholds_rejected(self):
        for value in (thresholds(0), thresholds(1, "NaN"), thresholds(1, "1.01"), thresholds(1, "0", policy=-1)):
            with self.assertRaises(ValueError):
                value.validate()

    def test_missing_report_fails_without_fabricating_results(self):
        with tempfile.TemporaryDirectory() as temp:
            report = run_saved_gate(Path(temp)/"Experiment_Summary.json", thresholds())
            self.assertEqual(report["exit_code"], 10)
            self.assertFalse(report["performance_claim"])
            self.assertEqual(report["observations"], {})

    def test_summary_only_is_not_sufficient_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"Experiment_Summary.json"
            path.write_text(json.dumps(fixtures()[0]))
            report = run_saved_gate(path, thresholds())
            self.assertEqual(report["exit_code"], 10)

    def test_saved_sidecar_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            summary, manifest, rows = fixtures()
            for name, value in (("Experiment_Summary.json", summary), ("Experiment_Manifest.json", manifest), ("Execution_Results.json", rows)):
                (Path(temp)/name).write_text(json.dumps(value))
            report = run_saved_gate(Path(temp)/"Experiment_Summary.json", thresholds())
            self.assertEqual(report["exit_code"], 0)

    def test_negative_control_deliberately_exits_nonzero_and_labels_itself(self):
        command = [sys.executable, str(Path(__file__).with_name("quality_gate.py")), "--negative-control"]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 20)
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "controlled_negative_failure")
        self.assertFalse(report["performance_claim"])
        self.assertEqual(report["observations"], {})
        self.assertIn("no experiment performance", report["failures"][0]["message"])


if __name__ == "__main__":
    unittest.main()
