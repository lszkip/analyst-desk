"""Config-driven paired experiments with durable, honest partial-run reporting.

No paid calls occur on import. A caller must supply an approved durable budget
ledger, scoped model factory and read-only real-source backend factory. Gold is
passed only to the post-run evaluator, never the agent adapter or model factory.
"""
import asyncio
from collections import Counter, defaultdict
from decimal import Decimal
import copy
import json
import math
from pathlib import Path
import re
import statistics
import time

from workflow_agents import load_config
from workflow_contracts import ResearchRequest, WorkflowError, canonical, digest
from workflow_store import RunStore
from evaluation.final.adapters import execute_variant

VERSION = "final-experiments-v1"
ARCHITECTURES = {"fixed_workflow", "single_agent", "team"}
POLICY_CODES = {"permission_denied", "policy_block", "scope_denied", "invalid_tool_scope",
                "evaluation_publication_forbidden", "evaluation_tool_denied", "untrusted_source_instruction"}
BUDGET_CODES = {"budget_exhausted", "live_budget_exhausted", "live_accounting_error", "budget_exhausted_global", "batch_time_exhausted", "live_time_limit", "price_verification_required", "approval_required", "live_ledger_identity", "model_http_error", "model_unavailable", "live_response_oversized"}


def read_jsonl(path):
    rows = []
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row {number} must be an object")
            rows.append(row)
    return rows


def load_experiment(path):
    config = json.loads(Path(path).read_text())
    validate_experiment(config)
    return config


def validate_experiment(config):
    if config.get("schema_version") != VERSION:
        raise ValueError("Unknown experiment schema")
    if not re.fullmatch(r"[A-Za-z0-9_]+", config.get("experiment_id", "")):
        raise ValueError("Invalid experiment ID")
    if type(config.get("repeats")) is not int or not 1 <= config["repeats"] <= 3:
        raise ValueError("Use one to three independent repeats")
    if not 1 <= config.get("concurrency", 1) <= 8:
        raise ValueError("Concurrency must be 1..8")
    if not 10 <= config.get("case_timeout_seconds", 900) <= 3600:
        raise ValueError("Case timeout must be bounded")
    variants = config.get("variants", [])
    ids = [row.get("id") for row in variants]
    if not variants or len(ids) != len(set(ids)):
        raise ValueError("Variants need distinct IDs")
    for row in variants:
        if not re.fullmatch(r"[A-Za-z0-9_]+", row.get("id", "")) or row.get("architecture") not in ARCHITECTURES:
            raise ValueError("Invalid variant")
        if type(row.get("critic")) is not bool or type(row.get("lessons")) is not bool:
            raise ValueError("Critic and lessons toggles must be explicit booleans")
        if row.get("retrieval") not in {"production_hybrid", "table_aware_bm25", "plain_text_bm25"}:
            raise ValueError("Declare exact retrieval protocol")
    return config


def workflow_config_for(config):
    base = load_config()
    allowed = {"model", "temperature", "token_budget", "tool_budget", "max_output_tokens", "max_context_bytes", "max_agent_steps", "max_revisions"}
    overrides = config.get("workflow", {})
    if set(overrides) - allowed:
        raise ValueError("Unrecognized workflow override")
    base.update(overrides)
    if base["model"] != "openai/gpt-4.1-mini" or base["temperature"] != 0:
        raise ValueError("Frozen comparison uses the configured task model at temperature zero")
    if not 1000 <= base["token_budget"] <= 2000000 or not 10 <= base["tool_budget"] <= 500:
        raise ValueError("Invalid case token/tool limit")
    if not 1 <= base["max_agent_steps"] <= 15 or base["max_revisions"] != 2:
        raise ValueError("Invalid bounded agent execution settings")
    if not 1 <= base["max_output_tokens"] <= 4000 or not 1000 <= base["max_context_bytes"] <= 100000:
        raise ValueError("Invalid response/context limit")
    return base


def select_cases(cases, config):
    seen, selected = set(), []
    for case in cases:
        key = case.get("case_id", "")
        if not re.fullmatch(r"[A-Za-z0-9_]+", key) or key in seen:
            raise ValueError("Case IDs must be unique safe identifiers")
        seen.add(key)
        ResearchRequest.model_validate(case["request"])
        if case.get("split") in config.get("splits", ["heldout"]):
            selected.append(copy.deepcopy(case))
    selected.sort(key=lambda row: row["case_id"])
    limit = config.get("case_limit")
    if limit is not None:
        if type(limit) is not int or limit < 1:
            raise ValueError("Invalid case_limit")
        selected = selected[:limit]
    return selected


def schedule(cases, config):
    """Interleave arms within each case/repeat; never exhaust one arm first."""
    jobs = []
    for repeat in range(1, config["repeats"] + 1):
        for index, case in enumerate(cases):
            variants = config["variants"]
            # Rotate first arm by case to reduce cap/time ordering bias.
            rotate = index % len(variants)
            for variant in variants[rotate:] + variants[:rotate]:
                execution_id = f"{config['experiment_id']}_{variant['id']}_{case['case_id']}_r{repeat}"
                jobs.append({"execution_id": execution_id, "case_id": case["case_id"], "repeat": repeat, "variant": variant["id"], "split": case["split"]})
    return jobs


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def summarize(rows, *, planned_jobs, ledger_stats=None):
    output = {"planned_executions": planned_jobs, "recorded_executions": len(rows), "by_variant": {},
              "status_counts": dict(Counter(row["status"] for row in rows)),
              "budget": ledger_stats, "human_review_claim": False,
              "interpretation": "Numeric correctness is checked against private frozen targets. Risk semantics require independent labels; critic approval and unit-test success are not task gold. Unrun, skipped, error and timeout rows remain in the planned denominator."}
    groups = defaultdict(list)
    for row in rows:
        groups[row["variant"]].append(row)
    for variant, group in sorted(groups.items()):
        elapsed = [row["elapsed_seconds"] for row in group if row.get("elapsed_seconds") is not None]
        numeric = [row.get("evaluation", {}).get("numeric_success") for row in group]
        tasks = [row.get("evaluation", {}).get("task_success") for row in group]
        known_numeric = [v for v in numeric if type(v) is bool]
        known_tasks = [v for v in tasks if type(v) is bool]
        complete_cost = all(row.get("metrics", {}).get("cost_complete") is True for row in group if row["status"] not in {"unrun", "skipped"})
        actual_costs = [row.get("metrics", {}).get("reported_cost_usd") for row in group]
        repeated = defaultdict(list)
        for row in group:
            if type(row.get("evaluation", {}).get("numeric_success")) is bool:
                repeated[row.get("case_id", "unknown")].append(row["evaluation"]["numeric_success"])
        output["by_variant"][variant] = {
            "numeric_repeat_consistency": {"cases_with_three_scored_repeats": sum(len(v)==3 for v in repeated.values()),
                "cases_with_mixed_numeric_outcomes": sum(len(set(v))>1 for v in repeated.values()),
                "all_three_numeric_correct": sum(len(v)==3 and all(v) for v in repeated.values())},
            "planned": len(group), "status_counts": dict(Counter(row["status"] for row in group)),
            "median_elapsed_seconds": statistics.median(elapsed) if elapsed else None, "p95_elapsed_seconds": percentile(elapsed, .95),
            "numeric_scored": len(known_numeric), "numeric_passes": sum(known_numeric), "numeric_passes_per_planned": sum(known_numeric)/len(group),
            "task_scored": len(known_tasks), "task_passes": sum(known_tasks), "task_passes_per_planned": sum(known_tasks)/len(group),
            "unadjudicated_task_outcomes": len(group)-len(known_tasks),
            "observed_reported_cost_usd": str(sum((Decimal(str(v)) for v in actual_costs if v is not None), Decimal(0))),
            "cost_complete": complete_cost,
            "model_requests_started": sum(row.get("metrics", {}).get("llm_requests_started", 0) for row in group),
            "tool_calls": sum(row.get("tool_calls", 0) for row in group),
            "policy_violation_attempts": sum(row.get("policy_violation_attempts", 0) for row in group),
            "model_response_cache_hits": sum(row.get("metrics", {}).get("model_response_cache_hits", 0) for row in group)}
    return output


def make_lesson_context_factory(lesson_store, manifest, cases):
    """Bind E3 to a locally approved immutable procedural playbook."""
    lesson_store.verify_freeze(manifest)
    if not manifest.get("lessons"):
        raise WorkflowError("lesson_freeze", "An empty reviewed playbook is not a lessons-on treatment.")
    requests = {case["case_id"]: copy.deepcopy(case["request"]) for case in cases}
    def factory(case_id, variant):
        request = requests[case_id]
        tags = {"margin", "financial", "source_support", "scope", "tool_use", "numeric_prose"}
        if request["include_risks"]:
            tags.add("risk")
        question = request["question"].lower()
        for tag, pattern in (("competition", r"competit|customer|demand"), ("supplier", r"suppli|provider"), ("cyber", r"cyber|privacy|security")):
            if re.search(pattern, question):
                tags.add(tag)
        tags = sorted(tags.intersection(manifest["tags"]))
        if not tags:
            raise WorkflowError("lesson_freeze", "No frozen procedural tags match this case.")
        return lambda role: lesson_store.context(tags, role, manifest=manifest)
    factory.manifest_sha256 = manifest["manifest_sha256"]
    return factory


def forecast(config, cases, pricing_manifest=None):
    """Structural upper bound, not a promise that every planned run fits the cap."""
    workflow = workflow_config_for(config)
    jobs = schedule(select_cases(cases, config), config)
    usd_upper = None
    if pricing_manifest is not None:
        prices = pricing_manifest["models"][workflow["model"]]
        maximum_price = max(Decimal(prices["prompt_usd_per_million"]), Decimal(prices["completion_usd_per_million"]))
        usd_upper = str(Decimal(len(jobs)*workflow["token_budget"])*maximum_price/Decimal(1000000))
    return {"schema_version": VERSION, "executions": len(jobs), "repeats": config["repeats"],
        "uncapped_queue_conservative_usd_upper_bound": usd_upper,
        "independent_cache_per_execution": True, "same_model": workflow["model"],
        "per_execution_reserved_token_limit": workflow["token_budget"], "per_execution_tool_limit": workflow["tool_budget"],
        "per_response_output_limit": workflow["max_output_tokens"], "case_timeout_seconds": config.get("case_timeout_seconds", 900),
        "all_requested_reserved_tokens_upper_bound": len(jobs)*workflow["token_budget"],
        "pricing_manifest": pricing_manifest,
        "note": "The shared approved durable cost/time/request cap overrides this queue. Execution may be partial; unrun cases remain reported. No model call occurs during forecasting."}


async def run_experiment(cases, config, output_dir, *, model_factory, backend_factory, ledger=None,
                         evaluate=None, private_gold=None, lesson_context_factory=None,
                         on_progress=None, software_test_mode=False):
    """Run or resume a frozen queue. Interrupted attempts are never auto-retried.

    factories receive only execution metadata/config, never private gold.
    evaluate(case_id,result,records,private_gold) runs only after execution.
    """
    validate_experiment(config)
    if ledger is None and not software_test_mode:
        raise ValueError("Live experiments require the approved durable shared budget ledger")
    workflow_config = workflow_config_for(config)
    if isinstance(private_gold, (str, Path)):
        private_gold = read_jsonl(private_gold)
    if lesson_context_factory is not None and not re.fullmatch(r"[a-f0-9]{64}", getattr(lesson_context_factory, "manifest_sha256", "")):
        raise ValueError("Use make_lesson_context_factory to bind the locally approved frozen playbook")
    selected = select_cases(cases, config)
    jobs = schedule(selected, config)
    if not jobs:
        raise ValueError("No cases selected")
    by_case = {case["case_id"]: case for case in selected}
    variants = {variant["id"]: variant for variant in config["variants"]}
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": VERSION, "config": config, "public_cases_sha256": digest(selected),
                "private_gold_sha256": digest(private_gold) if private_gold is not None else None,
                "lesson_manifest_sha256": getattr(lesson_context_factory, "manifest_sha256", None),
                "jobs": jobs, "software_test_mode": software_test_mode, "publication": "disabled", "plan_approval": "simulated_evaluation_only", "human_reviewed": False}
    manifest["manifest_sha256"] = digest(manifest)
    manifest_path = folder / "Experiment_Manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text()) != manifest:
            raise ValueError("Frozen experiment identity changed; do not resume in this directory")
    else:
        atomic_json(manifest_path, manifest)
    def ledger_stats():
        if ledger is None:
            return None
        try:
            return ledger.stats()
        except Exception as exc:
            return {"accounting_unavailable": True, "error_code": getattr(exc, "code", type(exc).__name__)}
    rows, pending = [], []
    for job in jobs:
        result_path = folder / "executions" / job["execution_id"] / "Result.json"
        if result_path.exists():
            row = json.loads(result_path.read_text())
            if row.get("execution_id") != job["execution_id"] or row.get("manifest_sha256") != manifest["manifest_sha256"]:
                raise ValueError("Saved execution identity mismatch")
            rows.append(row)
        else:
            pending.append(job)
    queue = asyncio.Queue()
    for job in pending:
        queue.put_nowait(job)
    stopped = asyncio.Event()
    stop_reason = {"code": None}

    async def one(job):
        variant = variants[job["variant"]]
        execution_dir = folder / "executions" / job["execution_id"]
        execution_dir.mkdir(parents=True, exist_ok=True)
        result = {**job, "manifest_sha256": manifest["manifest_sha256"], "status": "unrun", "elapsed_seconds": None,
                  "evaluation_only": True, "human_reviewed": False, "publication": None,
                  "shared_semantic_validator": bool(variant["critic"]), "independent_model_cache": True}
        if stopped.is_set():
            result["reason"] = stop_reason["code"] or "shared_budget_stopped"
            return result
        if variant["lessons"] and lesson_context_factory is None:
            result.update(status="skipped", reason="frozen_human_reviewed_lessons_unavailable")
            return result
        started = time.monotonic()
        begin_path = execution_dir / "Started.json"
        if begin_path.exists():
            result.update(status="interrupted", reason="earlier_attempt_has_no_final_result_no_automatic_retry")
            return result
        # Durable claim precedes every model/backend call. Resume cannot erase it.
        atomic_json(begin_path, {"execution_id": job["execution_id"], "manifest_sha256": manifest["manifest_sha256"]})
        began_ledger = False
        try:
            if ledger is not None:
                began_ledger = ledger.begin_case(job["execution_id"])
                if not began_ledger:
                    raise WorkflowError("live_interrupted_case", "Ledger already finalized this execution without its saved result; no retry.")
            model = model_factory(job["execution_id"], copy.deepcopy(workflow_config))
            backend = backend_factory(copy.deepcopy(variant))
            result["retrieval_protocol"] = copy.deepcopy(getattr(backend, "protocol", {"ranking": "operator_production_hybrid_service", "configuration_attestation": "external_preflight_required"}))
            if variant["retrieval"] in {"table_aware_bm25", "plain_text_bm25"}:
                expected_representation = "plain_text" if variant["retrieval"] == "plain_text_bm25" else "table_aware"
                if result["retrieval_protocol"].get("ranking") != "fixed_bm25" or result["retrieval_protocol"].get("representation") != expected_representation:
                    raise WorkflowError("evaluation_backend_mismatch", "Representation ablations require the actual matching fixed-BM25 backend.")
            lessons = lesson_context_factory(job["case_id"], copy.deepcopy(variant)) if variant["lessons"] else None
            async with asyncio.timeout(config.get("case_timeout_seconds", 900)):
                state = await execute_variant(copy.deepcopy(by_case[job["case_id"]]["request"]), copy.deepcopy(variant),
                    workflow_config, model, backend, execution_dir, job["execution_id"], lesson_context=lessons)
            result.update(status=state["status"], result=state)
        except asyncio.TimeoutError:
            result.update(status="timeout", error={"code": "case_timeout", "message": "Execution exceeded its fixed wall-clock allowance."})
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            result.update(status="error", error={"code": code, "message": str(exc)[:1200]})
            if code in BUDGET_CODES or code.startswith("budget_") or code.startswith("live_budget") or code in {"live_accounting_error", "pricing_expired", "batch_deadline"}:
                stop_reason["code"] = code
                stopped.set()
        finally:
            result["elapsed_seconds"] = round(time.monotonic()-started, 6)
            journal_path = execution_dir / "journal.sqlite3"
            records = {}
            if journal_path.exists():
                store = RunStore(journal_path)
                report = store.report(job["execution_id"])
                records = store.evidence(job["execution_id"])
                atomic_json(execution_dir / "Evidence.json", records)
                atomic_json(execution_dir / "Journal_Report.json", report)
                with store.db() as db:
                    responses = [{"cache_key": row["id"], "response": json.loads(row["content"])}
                                 for row in db.execute("SELECT id,content FROM responses WHERE run=? ORDER BY id", (job["execution_id"],))]
                atomic_json(execution_dir / "Model_Responses.json", responses)
                result["metrics"] = report["metrics"]
                result["tool_calls"] = report["budget"]["tool_calls"]
                result["policy_violation_attempts"] = sum(event["kind"] == "tool_denied" for event in report["events"])
                if result.get("error", {}).get("code") in POLICY_CODES:
                    result["policy_violation_attempts"] += 1
                result["tool_errors"] = sum(event["kind"] == "tool_error" for event in report["events"])
                result["model_errors"] = sum(event["kind"] == "llm_error" for event in report["events"])
            if evaluate is not None:
                try:
                    result["evaluation"] = evaluate(job["case_id"], result.get("result", {"status": result["status"]}), records, private_gold)
                except Exception as exc:
                    result["evaluation"] = {"numeric_success": None, "task_success": None, "evaluator_error": type(exc).__name__ + ": " + str(exc)[:500]}
            if ledger is not None and began_ledger:
                try:
                    ledger.finish_case(job["execution_id"], result["status"])
                except Exception as exc:
                    result["accounting_finalize_error"] = getattr(exc, "code", type(exc).__name__)
                    stopped.set()
                    stop_reason["code"] = result["accounting_finalize_error"]
        return result

    async def worker():
        while not queue.empty():
            try:
                job = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            row = await one(job)
            rows.append(row)
            # An unrun row is deliberately not saved as a terminal result: a
            # later explicit cap extension/new approved execution may resume it.
            if row["status"] != "unrun":
                atomic_json(folder / "executions" / job["execution_id"] / "Result.json", row)
            atomic_json(folder / "Progress.json", summarize(rows, planned_jobs=len(jobs), ledger_stats=ledger_stats()))
            if on_progress is not None:
                on_progress(row)
            queue.task_done()
    await asyncio.gather(*(worker() for _ in range(config.get("concurrency", 1))))
    rows.sort(key=lambda row: row["execution_id"])
    summary = summarize(rows, planned_jobs=len(jobs), ledger_stats=ledger_stats())
    summary["manifest_sha256"] = manifest["manifest_sha256"]
    atomic_json(folder / "Execution_Results.json", rows)
    atomic_json(folder / "Experiment_Summary.json", summary)
    return summary
