"""Gate saved actual experiment outcomes; never call a model or consume gold.

Requires the public summary, immutable experiment manifest and saved execution
results. Hash/count consistency is evidence hygiene, not independent attestation
that an operator has honestly produced the files. Keep the original run traces.
"""
from __future__ import annotations

# A sibling operator.py must never shadow Python's stdlib operator on direct CLI use.
import sys
if __name__ == "__main__" and not __package__ and sys.path:
    sys.path.pop(0)

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import sys

VERSION = "final-quality-gate-v1"
STATUSES = {"completed", "blocked", "error", "timeout", "interrupted", "skipped", "unrun"}
EXIT_CODES = {"missing_evidence": 10, "invalid_evidence": 11, "non_actual_evidence": 12,
              "insufficient_coverage": 13, "numeric_threshold": 14,
              "policy_limit": 15, "error_limit": 16, "negative_control": 20}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class EvidenceError(ValueError):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Thresholds:
    min_completed: int
    min_numeric_success: Decimal
    max_policy_violations: int
    max_errors: int

    def validate(self):
        if type(self.min_completed) is not int or self.min_completed < 1:
            raise ValueError("min_completed must be a positive count per variant")
        if not isinstance(self.min_numeric_success, Decimal) or not self.min_numeric_success.is_finite() or not 0 <= self.min_numeric_success <= 1:
            raise ValueError("min_numeric_success must be a finite fraction from zero to one")
        for name in ("max_policy_violations", "max_errors"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative count per variant")
        return self

    def as_dict(self):
        return {"min_completed": self.min_completed, "min_numeric_success": str(self.min_numeric_success),
                "max_policy_violations": self.max_policy_violations, "max_errors": self.max_errors,
                "scope": "each variant separately; numeric success uses the full planned denominator"}


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise EvidenceError("invalid_evidence", f"{name} must be an integer >= {minimum}")
    return value


def status_counts(value, name):
    if not isinstance(value, dict) or set(value) - STATUSES:
        raise EvidenceError("invalid_evidence", f"{name} has unknown or missing status counts")
    return {key: integer(number, f"{name}.{key}") for key, number in value.items() if number != 0}


def require_actual(value, name):
    if not isinstance(value, dict):
        raise EvidenceError("invalid_evidence", f"{name} must be a JSON object")
    for key in ("software_test_mode", "synthetic", "dry_run", "negative_control"):
        if value.get(key) is True:
            raise EvidenceError("non_actual_evidence", f"{name} is marked {key}; it is not actual experiment evidence")
    if any(key in value for key in ("numeric_targets", "expected_outcome", "expected_outcomes", "expected_label", "gold", "human_risk_labels")):
        raise EvidenceError("non_actual_evidence", f"{name} contains expected/gold labels, not observed experiment results")


def validate_evidence(summary, manifest, rows):
    """Recompute counts from execution rows and bind them to the frozen queue."""
    require_actual(summary, "summary")
    require_actual(manifest, "manifest")
    if manifest.get("schema_version") != "final-experiments-v1" or manifest.get("software_test_mode") is not False:
        raise EvidenceError("non_actual_evidence", "Require an actual final-experiments-v1 manifest with software_test_mode=false")
    frozen = dict(manifest)
    expected_hash = frozen.pop("manifest_sha256", None)
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash) or digest(frozen) != expected_hash:
        raise EvidenceError("invalid_evidence", "Experiment manifest fingerprint does not verify")
    if summary.get("manifest_sha256") != expected_hash:
        raise EvidenceError("invalid_evidence", "Summary is not bound to the supplied experiment manifest")
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise EvidenceError("insufficient_coverage", "The frozen experiment queue is empty; no zero-denominator pass")
    by_id = {}
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("execution_id"), str) or not isinstance(job.get("variant"), str):
            raise EvidenceError("invalid_evidence", "Invalid frozen execution identity")
        if job["execution_id"] in by_id:
            raise EvidenceError("invalid_evidence", "Duplicate execution in the frozen queue")
        by_id[job["execution_id"]] = job
    if not isinstance(rows, list):
        raise EvidenceError("invalid_evidence", "Execution_Results.json must be a list of actual execution records")
    planned = integer(summary.get("planned_executions"), "planned_executions", 1)
    recorded = integer(summary.get("recorded_executions"), "recorded_executions")
    if planned != len(jobs) or recorded != len(rows):
        raise EvidenceError("invalid_evidence", "Summary denominator does not match the frozen queue and saved execution rows")
    seen, groups = set(), defaultdict(list)
    for row in rows:
        require_actual(row, "execution")
        eid = row.get("execution_id")
        if not isinstance(eid, str) or eid in seen or eid not in by_id:
            raise EvidenceError("invalid_evidence", "Duplicate or out-of-queue saved execution")
        seen.add(eid)
        job = by_id[eid]
        for field in ("case_id", "repeat", "variant", "split"):
            if row.get(field) != job.get(field):
                raise EvidenceError("invalid_evidence", f"Saved execution {eid} changed its frozen {field}")
        if row.get("manifest_sha256") != expected_hash or row.get("status") not in STATUSES:
            raise EvidenceError("invalid_evidence", f"Saved execution {eid} has invalid status or manifest identity")
        evaluation = row.get("evaluation", {})
        require_actual(evaluation, f"{eid}.evaluation")
        for key in ("numeric_success", "task_success"):
            if evaluation.get(key) is not None and type(evaluation[key]) is not bool:
                raise EvidenceError("invalid_evidence", f"{eid}.{key} must be true, false or null")
        if evaluation.get("numeric_success") is True and row["status"] != "completed":
            raise EvidenceError("invalid_evidence", f"{eid} cannot pass numeric outcomes without completion")
        integer(row.get("policy_violation_attempts", 0), f"{eid}.policy_violation_attempts")
        if row["status"] == "completed":
            state = row.get("result")
            if not isinstance(state, dict) or state.get("status") != "completed":
                raise EvidenceError("invalid_evidence", f"{eid} has no completed saved workflow output")
            note = state.get("note")
            if not isinstance(note, dict) or not isinstance(note.get("body_markdown"), str) or not note["body_markdown"].strip():
                raise EvidenceError("invalid_evidence", f"{eid} has no saved final note")
            if not isinstance(note.get("citations"), list) or not note["citations"]:
                raise EvidenceError("invalid_evidence", f"{eid} has no saved evidence citations")
            integer(row.get("tool_calls"), f"{eid}.tool_calls", 1)
            elapsed = row.get("elapsed_seconds")
            if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
                raise EvidenceError("invalid_evidence", f"{eid} has no valid elapsed-time observation")
        groups[row["variant"]].append(row)
    if seen != set(by_id):
        raise EvidenceError("insufficient_coverage", "Some frozen executions have no saved result; omitted jobs cannot improve the denominator")
    expected_top_status = dict(Counter(r["status"] for r in rows))
    if status_counts(summary.get("status_counts"), "summary.status_counts") != expected_top_status:
        raise EvidenceError("invalid_evidence", "Summary statuses disagree with saved execution results")
    variants = summary.get("by_variant")
    if not isinstance(variants, dict) or set(variants) != set(groups):
        raise EvidenceError("invalid_evidence", "Summary variants disagree with the frozen executed queue")
    observations = {}
    for variant, group in sorted(groups.items()):
        item = variants[variant]
        if not isinstance(item, dict):
            raise EvidenceError("invalid_evidence", "Variant summary must be an object")
        counts = dict(Counter(r["status"] for r in group))
        scored = [r.get("evaluation", {}).get("numeric_success") for r in group]
        known = [v for v in scored if type(v) is bool]
        tasks = [r.get("evaluation", {}).get("task_success") for r in group]
        task_known = [v for v in tasks if type(v) is bool]
        actual = {"planned": len(group), "numeric_scored": len(known), "numeric_passes": sum(known),
                  "task_scored": len(task_known), "task_passes": sum(task_known),
                  "unadjudicated_task_outcomes": len(group)-len(task_known),
                  "policy_violation_attempts": sum(r.get("policy_violation_attempts", 0) for r in group)}
        for key, value in actual.items():
            if integer(item.get(key), f"{variant}.{key}") != value:
                raise EvidenceError("invalid_evidence", f"Variant {variant}.{key} disagrees with saved observed outcomes")
        if status_counts(item.get("status_counts"), f"{variant}.status_counts") != counts:
            raise EvidenceError("invalid_evidence", f"Variant {variant} status counts disagree with saved results")
        rate = Decimal(actual["numeric_passes"]) / Decimal(actual["planned"])
        # The runner writes a JSON float; tolerate only its representation noise.
        try:
            summary_rate = Decimal(str(item["numeric_passes_per_planned"]))
        except (KeyError, InvalidOperation):
            raise EvidenceError("invalid_evidence", f"Variant {variant} numeric rate is invalid")
        if not summary_rate.is_finite() or abs(summary_rate-rate) > Decimal("1e-12"):
            raise EvidenceError("invalid_evidence", f"Variant {variant} uses a changed numeric denominator")
        observations[variant] = {**actual, "status_counts": counts, "completed": counts.get("completed", 0),
            "numeric_success_fraction": str(rate),
            "errors": sum(counts.get(status, 0) for status in ("error", "timeout", "interrupted")),
            "unrun_or_skipped": counts.get("unrun", 0) + counts.get("skipped", 0)}
    return observations


def base_report(thresholds=None):
    return {"schema_version": VERSION, "performance_claim": False, "gate_passed": False,
            "thresholds": thresholds.as_dict() if thresholds else None,
            "scope": "Saved numeric outcome gate, not risk-semantic completion or independent audit of provider execution",
            "failures": [], "observations": {}}


def failure_report(kind, message, thresholds=None):
    report = base_report(thresholds)
    report.update(status="failed", exit_code=EXIT_CODES[kind], failures=[{"code": kind, "message": message}])
    return report


def evaluate_gate(summary, manifest, rows, thresholds):
    thresholds.validate()
    report = base_report(thresholds)
    try:
        report["observations"] = validate_evidence(summary, manifest, rows)
    except EvidenceError as exc:
        return failure_report(exc.kind, str(exc), thresholds)
    for variant, values in report["observations"].items():
        def fail(code, message):
            report["failures"].append({"code": code, "variant": variant, "message": message})
        if values["unrun_or_skipped"] or values["completed"] < thresholds.min_completed or values["numeric_scored"] < values["completed"]:
            fail("insufficient_coverage", "Unrun/skipped jobs, insufficient completed cases or completed outputs without numeric adjudication")
        if Decimal(values["numeric_success_fraction"]) < thresholds.min_numeric_success:
            fail("numeric_threshold", "Numeric passes divided by all planned executions are below the configured minimum")
        if values["policy_violation_attempts"] > thresholds.max_policy_violations:
            fail("policy_limit", "Observed policy violation attempts exceed the configured maximum")
        if values["errors"] > thresholds.max_errors:
            fail("error_limit", "Observed error, timeout and interrupted execution count exceeds the configured maximum")
    passed = not report["failures"]
    report.update(status="passed" if passed else "failed", gate_passed=passed, performance_claim=passed,
                  exit_code=0 if passed else min(EXIT_CODES[f["code"]] for f in report["failures"]))
    return report


def load_json(path):
    path = Path(path)
    if not path.is_file():
        raise EvidenceError("missing_evidence", f"Missing actual saved evidence: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, UnicodeError, ValueError) as exc:
        raise EvidenceError("invalid_evidence", f"Unreadable or invalid JSON evidence: {path}") from exc


def run_saved_gate(summary_path, thresholds, manifest_path=None, results_path=None):
    thresholds.validate()
    path = Path(summary_path)
    manifest_path = Path(manifest_path) if manifest_path else path.with_name("Experiment_Manifest.json")
    results_path = Path(results_path) if results_path else path.with_name("Execution_Results.json")
    try:
        summary, manifest, results = (load_json(p) for p in (path, manifest_path, results_path))
        report = evaluate_gate(summary, manifest, results, thresholds)
    except EvidenceError as exc:
        report = failure_report(exc.kind, str(exc), thresholds)
    report["input_files"] = {"summary": str(path), "manifest": str(manifest_path), "results": str(results_path)}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--min-completed", type=int)
    parser.add_argument("--min-numeric-success", type=Decimal)
    parser.add_argument("--max-policy-violations", type=int)
    parser.add_argument("--max-errors", type=int)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--negative-control", action="store_true", help="Deliberately fail; this proves only that the configured gate can fail")
    args = parser.parse_args(argv)
    if args.negative_control:
        report = failure_report("negative_control", "CONTROLLED NEGATIVE CI CHECK: deliberately failed; no experiment performance was assessed")
        report.update(status="controlled_negative_failure", negative_control=True, performance_claim=False)
    else:
        required = ("summary", "min_completed", "min_numeric_success", "max_policy_violations", "max_errors")
        if any(getattr(args, key) is None for key in required):
            parser.error("Actual outcome gate requires --summary and all four explicit threshold arguments")
        try:
            thresholds = Thresholds(args.min_completed, args.min_numeric_success, args.max_policy_violations, args.max_errors).validate()
        except ValueError as exc:
            parser.error(str(exc))
        report = run_saved_gate(args.summary, thresholds, args.manifest, args.results)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
