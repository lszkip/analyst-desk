"""Completion runner: the existing durable runner with an explicit adapter seam.

This copy is intentionally staged outside frozen S40. Its only execution change
is a supplied executor callable, allowing production memory context injection
before cache identity, context checks and token reservations. All private gold
remains solely in the post-run evaluator. No model calls occur on import.
"""
import asyncio
import copy
import json
from pathlib import Path
import re
import sqlite3
import time
from workflow_contracts import WorkflowError, digest
from workflow_store import RunStore
from evaluation.final.adapters import execute_variant
from evaluation.final.experiment_runner import (VERSION, POLICY_CODES, BUDGET_CODES,
    validate_experiment, workflow_config_for, read_jsonl, select_cases, schedule as historical_schedule,
    atomic_json, summarize)
BUDGET_CODES = BUDGET_CODES | {'reviewed_software_changed','completion_identity','source_integrity_stop'}

SCHEDULING = {"policy":"balanced_case_blocks_v1", "seed":"analyst-desk-completion43-prespecified", "case_balance":"numeric_then_risk_alternating", "arm_rotation":"case_index_plus_repeat_index"}


def schedule(cases, config):
    """Prespecified paired blocks; ordering reads request metadata, never results."""
    policy=config.get("scheduling")
    if policy is None:
        return historical_schedule(cases, config)
    if policy != SCHEDULING or config.get("splits") != ["heldout"] or config.get("repeats") != 3:
        raise ValueError("Unsupported completion scheduling protocol")
    groups=[]
    for risky in (False, True):
        group=[c for c in cases if c["request"]["include_risks"] is risky]
        group.sort(key=lambda c:(digest([policy["seed"],c["case_id"]]),c["case_id"]))
        groups.append(group)
    ordered=[]
    for index in range(max(map(len,groups),default=0)):
        for group in groups:
            if index<len(group):ordered.append(group[index])
    jobs=[]
    for index,case in enumerate(ordered):
        for repeat in range(1,config["repeats"]+1):
            variants=config["variants"];rotate=(index+repeat-1)%len(variants)
            for variant in variants[rotate:]+variants[:rotate]:
                execution_id=f"{config['experiment_id']}_{variant['id']}_{case['case_id']}_r{repeat}"
                jobs.append({"execution_id":execution_id,"case_id":case["case_id"],"repeat":repeat,
                    "variant":variant["id"],"split":case["split"]})
    return jobs


def shared_accounting_ready(stats, active_execution_ids=()):
    if (not isinstance(stats,dict) or stats.get('poisoned') is not False
            or stats.get('accounting_unavailable') or stats.get('stop_code')):return False
    unknown=stats.get('requests_without_actual_cost');inflight=stats.get('in_flight_requests')
    if type(unknown) is not int or type(inflight) is not int or unknown!=inflight or inflight<0:return False
    if inflight==0:return True
    pending=[r for r in stats.get('calls',[]) if r.get('status')=='reserved']
    return (len(pending)==inflight and all(r.get('case_id') in active_execution_ids
        and r.get('actual_nano_usd') is None for r in pending))


def verify_case_local_budget_failure(row, execution_dir, workflow_config, request, variant):
    """Read-only proof of a real case-local limit, never a missing-run error."""
    if row.get('error',{}).get('code')!='budget_exhausted':return None
    path=(Path(execution_dir)/'journal.sqlite3').absolute()
    if not path.is_file() or any(p.is_symlink() for p in (path,*path.parents)):return None
    expected=digest({**workflow_config,'request':request,'evaluation_variant':variant})
    try:
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON')
            found=db.execute('SELECT * FROM runs WHERE id=?',(row['execution_id'],)).fetchone()
            if found is None:return None
            budget=dict(found)
            events=[(r['kind'],json.loads(r['details'])) for r in db.execute('SELECT kind,details FROM events WHERE run=? ORDER BY seq',(row['execution_id'],))]
        finally:db.close()
        if (budget['id']!=row['execution_id'] or budget['config_hash']!=expected
                or budget['token_limit']!=workflow_config['token_budget'] or budget['tool_limit']!=workflow_config['tool_budget']
                or any(type(budget[k]) is not int for k in ('token_limit','tool_limit','reserved_tokens','tool_calls'))
                or not 0<=budget['reserved_tokens']<=budget['token_limit']
                or not 0<=budget['tool_calls']<=budget['tool_limit']):return None
        # A bounded next reservation must plausibly exhaust an existing limit.
        max_next_tokens=workflow_config['max_context_bytes']+workflow_config['max_output_tokens']+2048
        if budget['tool_calls']<budget['tool_limit'] and budget['token_limit']-budget['reserved_tokens']>=max_next_tokens:return None
        starts=sum(k=='llm_start' for k,d in events);ends=[d['usage'] for k,d in events if k=='llm_end']
        if (starts!=len(ends) or any(u.get('cost') is None for u in ends)
                or any(k in {'llm_error','tool_error','development_budget_extended'} for k,d in events)
                or row.get('metrics',{}).get('cost_complete') is not True
                or row['metrics'].get('llm_requests_started')!=starts):return None
        proof={'classification':'verified_case_local_budget_exhaustion','execution_id':row['execution_id'],
            'frozen_config_sha256':expected,'original_budget':budget,'fully_costed_model_calls':starts,
            'failed_outcome_retained':True,'retry_permitted':False,'limit_extension_permitted':False}
        proof['proof_sha256']=digest(proof);return proof
    except (OSError,sqlite3.Error,ValueError,TypeError,KeyError):return None


async def run_experiment(cases, config, output_dir, *, model_factory, backend_factory, ledger=None,
                         evaluate=None, private_gold=None, lesson_context_factory=None,
                         on_progress=None, software_test_mode=False, executor=None):
    """Run or resume a frozen queue. Interrupted attempts are never auto-retried.

    factories receive only execution metadata/config, never private gold.
    evaluate(case_id,result,records,private_gold) runs only after execution.
    """
    validate_experiment(config)
    executor = executor or execute_variant
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
    active_execution_ids=set()
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
        active_execution_ids.add(job["execution_id"])
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
                state = await executor(copy.deepcopy(by_case[job["case_id"]]["request"]), copy.deepcopy(variant),
                    workflow_config, model, backend, execution_dir, job["execution_id"], lesson_context=lessons)
            result.update(status=state["status"], result=state)
        except asyncio.TimeoutError:
            result.update(status="timeout", error={"code": "case_timeout", "message": "Execution exceeded its fixed wall-clock allowance."})
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            result.update(status="error", error={"code": code, "message": str(exc)[:1200]})
            if code != "budget_exhausted" and (code in BUDGET_CODES or code.startswith("budget_") or code.startswith("live_budget") or code in {"live_accounting_error", "pricing_expired", "batch_deadline"}):
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
            if result.get('error',{}).get('code')=='budget_exhausted':
                proof=verify_case_local_budget_failure(result,execution_dir,workflow_config,
                    by_case[job['case_id']]['request'],variant)
                healthy=software_test_mode if ledger is None else shared_accounting_ready(ledger_stats(),active_execution_ids-{job["execution_id"]})
                if proof and healthy:
                    result['case_local_budget_review']=proof
                else:
                    stopped.set();stop_reason['code']='unverified_budget_exhaustion'
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
        active_execution_ids.discard(job["execution_id"])
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
