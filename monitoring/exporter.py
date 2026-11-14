"""Read-only monitoring of a validated saved development-run export.

This module does not call models, read credentials, open workflow databases,
change notes, or contact the research portal. It is not live agent tracing.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
import statistics
import time
from zipfile import ZipFile, BadZipFile

DATASET = "google_meta_sequence25"
RUN_ID = "S22_Google_Meta_FY2024_FY2025"
NOTE_HASH = "1aae0e100911d32c7cac58788f24a93af778ad166848f716e48da968d7923f10"
BASELINE_HASH = "d34db63ea43b9f6d413427c94558543cb5e1e29cddf1302204255301497359dd"
CLASSIFICATION = "assisted_development"
TELEMETRY_KIND = "saved_snapshot"
KINDS = frozenset({"llm_start", "llm_end", "llm_error", "llm_cache_hit", "tool_start", "tool_end", "tool_error", "agent_correction", "agent_response_limit", "agent_step_limit", "critic_rejected", "plan_created", "replan", "research_section_prefetch", "deterministic_financial_narrative", "diagnostic_continuation_fork", "diagnostic_note_edit", "deterministic_writer", "development_budget_extended", "duplicate_tool_skipped"})
ROLES = frozenset({"supervisor", "researcher", "quant", "risk_diff", "writer", "critic", "publisher", "operator", "human"})
SERVICES = frozenset({"edgar", "kb", "sandbox", "portal"})
TOOLS = frozenset({"list_companies", "list_annual_filings", "get_annual_financial_fact", "get_section", "get_coverage", "search_filings", "get_passage", "get_table_rows", "plan_comparison", "execute_python", "prepare_note", "get_proposal", "publish_note", "get_note", "search_notes"})
ERROR_KINDS = ("llm_error", "tool_error", "agent_response_limit", "agent_step_limit")
LIMITATIONS = [
    "Saved-run evidence, not live agent telemetry. Snapshot age is shown explicitly.",
    "Real-data, AI-assisted development note with recorded human approval; not held-out or autonomous quality evidence.",
    "Verified-number share covers code-rendered financial table values only; qualitative correctness and numbers in prose are not measured by this metric.",
    "Some critic sentence receipts remain incomplete. Zero critic rejections does not establish semantic accuracy.",
    "Tool latency covers successful broker-observed calls only. Failed-call latency and direct operator reads are not fully instrumented.",
    "EDGAR cache-hit telemetry is unavailable. Model-response cache hits are a separate measure.",
    "Events are an ordered allowlisted timeline. Tool spans are not reconstructed where the source lacks unique operation IDs.",
    "Cumulative S22–S25 history is counted once. Predecessor archive totals are not added together.",
    "Publication and source checks are validated from the uploaded records; this importer does not independently query the live portal or re-read SEC files.",
]
DYNAMIC_METRICS = frozenset({"analyst_desk_snapshot_age_seconds"})
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
HEX = re.compile(r"[a-f0-9]{64}\Z")


def require(condition, message="Invalid monitoring evidence."):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj, "Duplicate JSON field.")
        obj[key] = value
    return obj


def parse_json(raw):
    require(len(raw) <= MAX_JSON_BYTES, "JSON file exceeds the monitoring limit.")
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON value.")))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Unreadable monitoring JSON.") from None


def _integer(value, maximum=10**12):
    return type(value) is int and 0 <= value <= maximum


def _number(value, maximum=10**12):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum


def _cost(value):
    require(type(value) in (str, int, float), "Invalid reported cost.")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Invalid reported cost.") from None
    require(result.is_finite() and 0 <= result <= 10**6, "Invalid reported cost.")
    return result


def safe_read(path, maximum=MAX_JSON_BYTES):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in [path, *path.parents]), "Symbolic links are not valid monitoring input.")
    require(path.is_file() and path.stat().st_size <= maximum, "Monitoring input is missing or too large.")
    with path.open("rb") as handle:
        raw = handle.read(maximum + 1)
    require(len(raw) <= maximum, "Monitoring input is too large.")
    return raw


def derive_metrics(execution, verification):
    """Recompute reported totals from events; incomplete cost remains unknown."""
    require(isinstance(execution, dict) and set(execution) == {"budget", "metrics", "events"})
    events, saved, budget = execution["events"], execution["metrics"], execution["budget"]
    require(isinstance(events, list) and 0 < len(events) <= 20000)
    require(isinstance(saved, dict) and isinstance(budget, dict))
    counts = Counter()
    tools = defaultdict(lambda: {"calls": Counter(), "latencies": []})
    sanitized = []
    total_tokens, reservations, starts, ends = 0, 0, 0, 0
    costs, costs_complete = [], True
    last_seq = 0
    for event in events:
        require(isinstance(event, dict) and set(event) == {"seq", "kind", "role", "details", "at"})
        seq, kind, role, at, detail = (event[k] for k in ("seq", "kind", "role", "at", "details"))
        require(_integer(seq) and seq > last_seq and kind in KINDS and role in ROLES and _number(at, 10**11) and isinstance(detail, dict))
        last_seq = seq
        counts[kind] += 1
        clean = {"seq": seq, "kind": kind, "role": role, "at": at}
        if kind == "llm_start":
            reserve = detail.get("reserved_tokens")
            require(_integer(reserve))
            reservations += reserve
            starts += 1
            clean["reserved_tokens"] = reserve
        elif kind == "llm_end":
            usage = detail.get("usage")
            require(isinstance(usage, dict) and all(_integer(usage.get(k)) for k in ("prompt_tokens", "completion_tokens", "total_tokens")))
            require(usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"])
            elapsed = detail.get("elapsed_seconds")
            require(_number(elapsed, 10**8))
            total_tokens += usage["total_tokens"]
            ends += 1
            clean.update(total_tokens=usage["total_tokens"], elapsed_seconds=elapsed)
            if usage.get("cost") is None:
                costs_complete = False
            else:
                cost = _cost(usage["cost"])
                costs.append(cost)
                clean["reported_cost_usd"] = str(cost)
        elif kind in {"tool_start", "tool_end", "tool_error"}:
            service, tool = detail.get("service"), detail.get("tool")
            require(service in SERVICES and tool in TOOLS)
            clean.update(service=service, tool=tool)
            if kind == "tool_end":
                status = detail.get("status")
                require(status in {"ok", "error", "not_found", "conflict", "unsupported_period"})
                elapsed = detail.get("elapsed_seconds")
                require(_number(elapsed, 10**8))
                tools[(service, tool)]["calls"][status] += 1
                clean.update(status=status, elapsed_seconds=elapsed)
                if status == "ok":
                    tools[(service, tool)]["latencies"].append(elapsed)
            elif kind == "tool_error":
                tools[(service, tool)]["calls"]["error"] += 1
                clean["status"] = "error"
        # Unknown free text, identifiers, hashes, provider data, and prompts are
        # deliberately absent from this explicit field allowlist.
        sanitized.append(clean)
    costs_complete = costs_complete and starts == ends
    cost_total = str(sum(costs, Decimal(0))) if costs_complete else None
    expected = {"llm_requests_started": starts, "llm_responses_received": ends,
                "reported_total_tokens": total_tokens, "cost_complete": costs_complete,
                "critic_rejections": counts["critic_rejected"], "model_response_cache_hits": counts["llm_cache_hit"],
                "development_budget_extensions": counts["development_budget_extended"],
                "budget_protocol": "operator_extended_development" if counts["development_budget_extended"] else "original_fixed_limit"}
    require(all(saved.get(k) == v for k, v in expected.items()), "Reported model totals disagree with the event ledger.")
    if costs_complete:
        require(saved.get("reported_cost_usd") is not None and _cost(saved["reported_cost_usd"]) == _cost(cost_total), "Reported cost disagrees with the event ledger.")
    else:
        require(saved.get("reported_cost_usd") is None, "Incomplete model cost must be unknown, not zero.")
    for key in ("token_limit", "tool_limit", "reserved_tokens", "tool_calls"):
        require(_integer(budget.get(key)))
    require(budget["reserved_tokens"] == reservations, "Token reservation total disagrees with the event ledger.")
    require(budget["tool_calls"] == counts["tool_start"], "Tool reservation total disagrees with the event ledger.")
    require(budget["reserved_tokens"] <= budget["token_limit"] and budget["tool_calls"] <= budget["tool_limit"])
    require(isinstance(verification, dict))
    total, verified = verification.get("rendered_financial_figures"), verification.get("verified_financial_figures")
    require(_integer(total) and _integer(verified) and verified <= total)
    share = verified / total if total else None
    if total:
        require(_number(verification.get("verified_share"), 1) and math.isclose(verification["verified_share"], share, rel_tol=0, abs_tol=1e-12))
    else:
        require(verification.get("verified_share") in (None, 0), "An empty denominator cannot mean 100 percent verified.")
    stats = []
    for (service, tool), row in sorted(tools.items()):
        vals = sorted(row["latencies"])
        stats.append({"service": service, "tool": tool, "calls": dict(sorted(row["calls"].items())),
                      "successful_latency_count": len(vals), "median_seconds": statistics.median(vals) if vals else None,
                      "p95_seconds": vals[max(0, math.ceil(len(vals)*0.95)-1)] if vals else None})
    metrics = {"llm_requests": starts, "llm_responses": ends, "reported_tokens": total_tokens,
               "reserved_tokens": reservations, "token_limit": budget["token_limit"], "tool_limit": budget["tool_limit"],
               "tool_calls": budget["tool_calls"], "reported_cost_usd": cost_total, "cost_complete": costs_complete,
               "critic_rejections": counts["critic_rejected"], "model_response_cache_hits": counts["llm_cache_hit"],
               "event_errors": {k: counts[k] for k in ERROR_KINDS}, "event_counts": dict(sorted(counts.items())),
               "financial_figures": total, "verified_financial_figures": verified, "verified_financial_share": share,
               "published_notes": 1, "edgar_cache_hits_available": False, "tool_stats": stats,
               "unpaired_llm_requests": max(0, starts - ends - counts["llm_error"]),
               "unpaired_tool_requests": max(0, counts["tool_start"] - counts["tool_end"] - counts["tool_error"])}
    return metrics, sanitized


def build_snapshot(zip_path):
    """Import exactly the S25 evidence lineage, never predecessor cumulative sums."""
    raw_zip = safe_read(zip_path, MAX_ARCHIVE_BYTES)
    from io import BytesIO
    try:
        with ZipFile(BytesIO(raw_zip)) as archive:
            infos = archive.infolist()
            require(0 < len(infos) <= 256 and sum(i.file_size for i in infos) <= MAX_TOTAL_BYTES)
            names = [i.filename for i in infos]
            require(len(names) == len(set(names)), "Duplicate ZIP member.")
            for info in infos:
                name = info.filename
                path = PurePosixPath(name)
                require(not path.is_absolute() and not any(p in {"..", ".", ""} for p in path.parts)
                        and "\\" not in name and "\x00" not in name and not name.endswith("/")
                        and str(path) == name and not (info.flag_bits & 1) and not stat.S_ISLNK(info.external_attr >> 16)
                        and info.file_size <= MAX_JSON_BYTES, "Unsafe ZIP member.")
            def raw(name):
                require(name in names, "Required publication evidence is missing.")
                return archive.read(name)
            def get(name):
                return parse_json(raw(name))
            prefix = RUN_ID + "/"
            case_raw, base_raw = raw(prefix + "Case_State_And_Evidence.json"), raw("Baseline_Case_State_And_Evidence.json")
            case, base = parse_json(case_raw), parse_json(base_raw)
            spec, bind, decision = get("Batch_Specification.json"), get("Publication_Approval_Binding.json"), get("Operator_Publication_Decision.json")
            receipt, note, readiness = get(prefix + "Publication_Receipt.json"), get(prefix + "Published_Note.json"), get("Publication_Readiness_Review.json")
            summary, progress, fork = get("25_Publication_Summary.json"), get("Run_Progress.json"), get("Publication_Fork.json")
            require(spec.get("sequence") == 25 and spec.get("source_run_sequence") == 24)
            require(hashlib.sha256(base_raw).hexdigest() == BASELINE_HASH == spec.get("baseline_record_sha256"))
            require(raw("Prior_Sequence24/" + prefix + "Case_State_And_Evidence.json") == base_raw)
            inventory = spec.get("prior_result_files")
            require(isinstance(inventory, dict) and len(inventory) == 60)
            require(set(n for n in names if n.startswith("Prior_Sequence24/")) == {"Prior_Sequence24/" + n for n in inventory})
            for name, expected in inventory.items():
                require(HEX.fullmatch(expected) and hashlib.sha256(raw("Prior_Sequence24/" + name)).hexdigest() == expected, "Historical evidence changed.")
            require(digest(bind.get("note")) == NOTE_HASH == bind.get("content_hash") == receipt.get("content_hash"))
            require(bind.get("binding_sha256") == digest({k: v for k, v in bind.items() if k != "binding_sha256"}))
            require(bind.get("sequence") == 25 and bind.get("run_id") == RUN_ID and bind.get("destination_kind") == "localhost_mock_research_portal")
            require(bind.get("baseline_record_sha256") == BASELINE_HASH and bind.get("evidence_sha256") == digest(base["evidence"]))
            require(bind.get("source_sha256") == spec.get("source_sha256") and bind.get("prior_metrics") == base["execution"]["metrics"] and bind.get("prior_budget") == base["execution"]["budget"])
            require(hashlib.sha256(raw("Publication_Readiness_Review.json")).hexdigest() == spec.get("readiness_review_sha256") == bind.get("readiness_review_sha256"))
            require(readiness.get("ai_assisted_correction") is True and readiness.get("held_out_evaluation") is False and readiness.get("autonomous_semantic_quality_pass") is False)
            require(bind.get("cache_manifest_sha256") == fork["cache"]["sha256"] and bind.get("source_database_manifest_sha256") == digest(fork["source_manifest"]))
            require(decision.get("publication_attempt_started") is True)
            op = decision.get("operator_approval")
            require(isinstance(op, dict) and op.get("action") == "approve" and op.get("source") == "interactive_terminal")
            require(op.get("binding_sha256") == bind["binding_sha256"] and op.get("content_hash") == NOTE_HASH)
            reviewer = op.get("reviewer")
            require(isinstance(reviewer, str) and reviewer.strip() == reviewer and 1 <= len(reviewer) <= 100 and all(ord(c) >= 32 and ord(c) != 127 for c in reviewer))
            require(op.get("revision") == bind.get("revision") == receipt.get("revision") == base["state"]["proposal"]["revision"])
            require(note == bind["note"] == base["state"]["note"] == case["state"]["note"] and digest(note) == NOTE_HASH)
            require(case["state"]["status"] == "published" and case.get("next_nodes") == [] and case.get("gate") is None)
            require(case["state"]["approval_hash"] == NOTE_HASH and receipt.get("published") is True and receipt.get("two_reads_identical") is True and receipt.get("portal_state") == "published")
            require(receipt.get("reviewer") == reviewer and _number(receipt.get("published_at"), 10**11))
            require(receipt.get("proposal_id") == bind.get("proposal_id") == base["gate"]["proposal_id"])
            require(case["state"]["publication"].get("note_id") == receipt.get("note_id") and case["state"]["publication"].get("content_hash") == NOTE_HASH and case["state"]["publication"].get("recovered") is False)
            require(isinstance(receipt.get("note_id"), str) and re.fullmatch(r"note_[a-f0-9]{32}", receipt["note_id"]))
            require(case["request"] == base["request"] and case["evidence"] == base["evidence"])
            for key in set(case["state"]) | set(base["state"]):
                if key not in {"status", "approval_hash", "publication"}:
                    require(case["state"].get(key) == base["state"].get(key), "Saved research state changed.")
            old_events = base["execution"]["events"]
            require(case["execution"]["events"][:len(old_events)] == old_events)
            added = case["execution"]["events"][len(old_events):]
            require(len(added) == 32 and Counter(e["kind"] for e in added) == {"tool_start": 16, "tool_end": 16})
            require(all(e["details"].get("status") == "ok" for e in added if e["kind"] == "tool_end"))
            require(Counter(e["details"]["tool"] for e in added if e["kind"] == "tool_end") == {"get_proposal": 1, "get_annual_financial_fact": 8, "get_passage": 6, "publish_note": 1})
            require(case["execution"]["metrics"] == base["execution"]["metrics"])
            for key, value in base["execution"]["budget"].items():
                require(case["execution"]["budget"].get(key) == (value + 16 if key == "tool_calls" else value))
            require(summary.get("batch_mechanical_passed") is True and summary.get("global_errors") == [] and summary.get("original_source_unchanged") is True)
            require(summary.get("source_before") == summary.get("source_after") and summary["source_after"].get("matches") is True)
            require(summary.get("fresh_end_to_end_evaluation") is False and summary.get("held_out_evaluation") is False and summary.get("autonomous_semantic_quality_pass") is False)
            entry = progress["cases"][RUN_ID]
            require(entry == summary["cases"][0] and entry.get("outcome") == "published_verified" and entry.get("operator_approval") == op and entry.get("publication_calls_this_invocation") == 1)
            metrics, timeline = derive_metrics(case["execution"], case["state"]["verification"])
            require(case["execution"]["budget"].get("id") == RUN_ID)
            snapshot = {"schema_version": 1, "dataset": DATASET, "evidence_class": CLASSIFICATION, "telemetry_kind": TELEMETRY_KIND,
                        "source_sequence": 25, "source_archive_sha256": hashlib.sha256(raw_zip).hexdigest(),
                        "case_sha256": hashlib.sha256(case_raw).hexdigest(), "note_content_sha256": NOTE_HASH,
                        "approval_binding_sha256": bind["binding_sha256"], "snapshot_timestamp": receipt["published_at"],
                        "publication": {"status": "published", "note_id_sha256": hashlib.sha256(receipt["note_id"].encode()).hexdigest(),
                                        "proposal_id_sha256": hashlib.sha256(receipt["proposal_id"].encode()).hexdigest(),
                                        "revision": receipt["revision"], "human_approved": True},
                        "metrics": metrics, "events": timeline, "limitations": LIMITATIONS,
                        "lineage": {"prior_events": len(old_events), "total_events": len(timeline), "new_native_tools": 16,
                                    "new_llm_requests": 0, "preserved_prior_records": len(inventory)}}
            snapshot["integrity_sha256"] = digest(snapshot)
            return validate_snapshot(snapshot)
    except (BadZipFile, KeyError, TypeError, IndexError, RuntimeError):
        raise ValueError("The saved publication evidence is incomplete or inconsistent.") from None


SNAPSHOT_KEYS = {"schema_version", "dataset", "evidence_class", "telemetry_kind", "source_sequence", "source_archive_sha256", "case_sha256", "note_content_sha256", "approval_binding_sha256", "snapshot_timestamp", "publication", "metrics", "events", "limitations", "lineage", "integrity_sha256"}
METRIC_KEYS = {"llm_requests", "llm_responses", "reported_tokens", "reserved_tokens", "token_limit", "tool_limit", "tool_calls", "reported_cost_usd", "cost_complete", "critic_rejections", "model_response_cache_hits", "event_errors", "event_counts", "financial_figures", "verified_financial_figures", "verified_financial_share", "published_notes", "edgar_cache_hits_available", "tool_stats", "unpaired_llm_requests", "unpaired_tool_requests"}


def validate_snapshot(obj):
    """Reject malformed or altered snapshots before serving any data."""
    require(isinstance(obj, dict) and set(obj) == SNAPSHOT_KEYS)
    require(obj["schema_version"] == 1 and obj["dataset"] == DATASET and obj["evidence_class"] == CLASSIFICATION and obj["telemetry_kind"] == TELEMETRY_KIND and obj["source_sequence"] == 25)
    for key in ("source_archive_sha256", "case_sha256", "note_content_sha256", "approval_binding_sha256", "integrity_sha256"):
        require(isinstance(obj[key], str) and HEX.fullmatch(obj[key]))
    require(obj["note_content_sha256"] == NOTE_HASH and obj["integrity_sha256"] == digest({k: v for k, v in obj.items() if k != "integrity_sha256"}))
    require(_number(obj["snapshot_timestamp"], 10**11) and obj["limitations"] == LIMITATIONS)
    publication = obj["publication"]
    require(isinstance(publication, dict) and set(publication) == {"status", "note_id_sha256", "proposal_id_sha256", "revision", "human_approved"})
    require(publication["status"] == "published" and publication["human_approved"] is True and _integer(publication["revision"]))
    require(all(isinstance(publication[k], str) and HEX.fullmatch(publication[k]) for k in ("note_id_sha256", "proposal_id_sha256")))
    m = obj["metrics"]
    require(isinstance(m, dict) and set(m) == METRIC_KEYS)
    for key in {"llm_requests", "llm_responses", "reported_tokens", "reserved_tokens", "token_limit", "tool_limit", "tool_calls", "critic_rejections", "model_response_cache_hits", "financial_figures", "verified_financial_figures", "published_notes", "unpaired_llm_requests", "unpaired_tool_requests"}:
        require(_integer(m[key]))
    require(type(m["cost_complete"]) is bool and m["edgar_cache_hits_available"] is False and m["published_notes"] == 1)
    require(m["llm_responses"] <= m["llm_requests"] and m["reserved_tokens"] <= m["token_limit"] and m["tool_calls"] <= m["tool_limit"])
    if m["cost_complete"]:
        require(isinstance(m["reported_cost_usd"], str))
        _cost(m["reported_cost_usd"])
    else:
        require(m["reported_cost_usd"] is None)
    require(m["verified_financial_figures"] <= m["financial_figures"])
    require(m["verified_financial_share"] == (m["verified_financial_figures"] / m["financial_figures"] if m["financial_figures"] else None))
    require(isinstance(m["event_errors"], dict) and set(m["event_errors"]) == set(ERROR_KINDS) and all(_integer(v) for v in m["event_errors"].values()))
    require(isinstance(m["event_counts"], dict) and set(m["event_counts"]) <= KINDS and all(_integer(v) for v in m["event_counts"].values()))
    require(isinstance(m["tool_stats"], list) and len(m["tool_stats"]) <= 100)
    tool_keys = set()
    for row in m["tool_stats"]:
        require(isinstance(row, dict) and set(row) == {"service", "tool", "calls", "successful_latency_count", "median_seconds", "p95_seconds"})
        require(row["service"] in SERVICES and row["tool"] in TOOLS and (row["service"], row["tool"]) not in tool_keys)
        tool_keys.add((row["service"], row["tool"]))
        require(isinstance(row["calls"], dict) and set(row["calls"]) <= {"ok", "error", "not_found", "conflict", "unsupported_period"} and all(_integer(v) for v in row["calls"].values()))
        require(_integer(row["successful_latency_count"]) and row["successful_latency_count"] == row["calls"].get("ok", 0))
        require(all(_number(row[k], 10**8) if row["successful_latency_count"] else row[k] is None for k in ("median_seconds", "p95_seconds")))
    events = obj["events"]
    require(isinstance(events, list) and 0 < len(events) <= 20000)
    previous = 0
    actual_counts = Counter()
    for row in events:
        require(isinstance(row, dict) and {"seq", "kind", "role", "at"} <= set(row) <= {"seq", "kind", "role", "at", "reserved_tokens", "total_tokens", "elapsed_seconds", "reported_cost_usd", "service", "tool", "status"})
        require(_integer(row["seq"]) and row["seq"] > previous and row["kind"] in KINDS and row["role"] in ROLES and _number(row["at"], 10**11))
        previous = row["seq"]
        actual_counts[row["kind"]] += 1
        for key in ("reserved_tokens", "total_tokens"):
            if key in row: require(_integer(row[key]))
        if "elapsed_seconds" in row: require(_number(row["elapsed_seconds"], 10**8))
        if "reported_cost_usd" in row: require(isinstance(row["reported_cost_usd"], str)); _cost(row["reported_cost_usd"])
        if "service" in row: require(row["service"] in SERVICES)
        if "tool" in row: require(row["tool"] in TOOLS)
        if "status" in row: require(row["status"] in {"ok", "error", "not_found", "conflict", "unsupported_period"})
    require(dict(actual_counts) == m["event_counts"] and m["event_errors"] == {k: actual_counts[k] for k in ERROR_KINDS})
    require(m["llm_requests"] == actual_counts["llm_start"] and m["llm_responses"] == actual_counts["llm_end"] and m["tool_calls"] == actual_counts["tool_start"])
    require(m["reported_tokens"] == sum(e.get("total_tokens", 0) for e in events if e["kind"] == "llm_end") and m["reserved_tokens"] == sum(e.get("reserved_tokens", 0) for e in events if e["kind"] == "llm_start"))
    # Recompute every recoverable aggregate at serve time. The self-digest
    # detects accidental changes; these checks also reject rehashed but
    # contradictory summaries. It is not an authentication signature.
    require(m["critic_rejections"] == actual_counts["critic_rejected"] and m["model_response_cache_hits"] == actual_counts["llm_cache_hit"])
    finished = [e for e in events if e["kind"] == "llm_end"]
    complete = actual_counts["llm_start"] == len(finished) and all("reported_cost_usd" in e for e in finished)
    require(m["cost_complete"] == complete)
    if complete:
        require(_cost(m["reported_cost_usd"]) == sum((_cost(e["reported_cost_usd"]) for e in finished), Decimal(0)))
    require(m["unpaired_llm_requests"] == max(0, actual_counts["llm_start"] - actual_counts["llm_end"] - actual_counts["llm_error"]))
    require(m["unpaired_tool_requests"] == max(0, actual_counts["tool_start"] - actual_counts["tool_end"] - actual_counts["tool_error"]))
    recalculated = defaultdict(lambda: {"calls": Counter(), "latencies": []})
    for event in events:
        if event["kind"] in {"tool_start", "tool_end", "tool_error"}:
            require({"service", "tool"} <= set(event))
        if event["kind"] == "llm_start": require("reserved_tokens" in event)
        if event["kind"] == "llm_end": require({"total_tokens", "elapsed_seconds"} <= set(event))
        if event["kind"] == "tool_end":
            require({"status", "elapsed_seconds"} <= set(event))
            rec = recalculated[(event["service"], event["tool"])]
            rec["calls"][event["status"]] += 1
            if event["status"] == "ok": rec["latencies"].append(event["elapsed_seconds"])
        elif event["kind"] == "tool_error":
            require(event.get("status") == "error")
            recalculated[(event["service"], event["tool"])]["calls"]["error"] += 1
    expected_stats = []
    for (service, tool), row in sorted(recalculated.items()):
        values = sorted(row["latencies"])
        expected_stats.append({"service": service, "tool": tool, "calls": dict(sorted(row["calls"].items())),
            "successful_latency_count": len(values), "median_seconds": statistics.median(values) if values else None,
            "p95_seconds": values[max(0, math.ceil(len(values)*.95)-1)] if values else None})
    require(m["tool_stats"] == expected_stats)
    lineage = obj["lineage"]
    require(isinstance(lineage, dict) and set(lineage) == {"prior_events", "total_events", "new_native_tools", "new_llm_requests", "preserved_prior_records"} and all(_integer(v) for v in lineage.values()))
    require(lineage["total_events"] == len(events) and lineage["prior_events"] + 32 == len(events) and lineage["new_native_tools"] == 16 and lineage["new_llm_requests"] == 0 and lineage["preserved_prior_records"] == 60)
    return obj


def validate_probe(probe, now=None):
    now = time.time() if now is None else now
    require(_number(now, 10**11))
    require(isinstance(probe, dict) and set(probe) == {"enabled", "verified", "total", "expires_at"}, "Invalid synthetic alert probe.")
    require(type(probe["enabled"]) is bool and _integer(probe["verified"], 1000) and _integer(probe["total"], 1000) and probe["verified"] <= probe["total"], "Invalid synthetic alert probe.")
    expires = probe["expires_at"]
    require(expires is None or _number(expires, 10**11), "Invalid synthetic alert expiry.")
    if probe["enabled"]:
        require(expires is not None and expires <= now + 600, "Synthetic alert expiry exceeds ten minutes.")
    return probe


def probe_active(probe, now):
    return bool(probe["enabled"] and probe["expires_at"] > now)


def metrics_text(snapshot, probe=None, now=None):
    snapshot = validate_snapshot(snapshot)
    now = time.time() if now is None else now
    require(_number(now, 10**11))
    probe = validate_probe(probe, now) if probe is not None else {"enabled": False, "verified": 0, "total": 0, "expires_at": None}
    labels = {"dataset": DATASET, "evidence_class": CLASSIFICATION, "telemetry_kind": TELEMETRY_KIND}
    lines, declared = [], set()
    def emit(name, value, extra=None, common=None):
        name = "analyst_desk_" + name
        if name not in declared:
            lines.extend(["# HELP " + name + " Saved evidence value; see snapshot scope and limitations.", "# TYPE " + name + " gauge"])
            declared.add(name)
        fields = {**(labels if common is None else common), **(extra or {})}
        series = ",".join(k + "=" + json.dumps(v, ensure_ascii=True) for k, v in sorted(fields.items()))
        lines.append(name + "{" + series + "} " + str(int(value) if type(value) is bool else value))
    m = snapshot["metrics"]
    emit("snapshot_valid", 1)
    emit("snapshot_timestamp_seconds", snapshot["snapshot_timestamp"])
    emit("snapshot_age_seconds", max(0, now - snapshot["snapshot_timestamp"]))
    for key in ("llm_requests", "llm_responses", "reported_tokens", "reserved_tokens", "token_limit", "tool_limit", "critic_rejections", "model_response_cache_hits", "financial_figures", "verified_financial_figures", "published_notes", "cost_complete", "unpaired_llm_requests", "unpaired_tool_requests"):
        emit(key, m[key])
    if m["reported_cost_usd"] is not None: emit("reported_cost_usd", m["reported_cost_usd"])
    if m["verified_financial_share"] is not None: emit("verified_financial_share", m["verified_financial_share"])
    emit("metric_available", 0, {"metric": "edgar_cache_hits"})
    for kind, count in m["event_errors"].items(): emit("event_errors", count, {"kind": kind})
    for kind, count in m["event_counts"].items(): emit("events", count, {"kind": kind})
    for row in m["tool_stats"]:
        for status, count in row["calls"].items(): emit("tool_calls", count, {"service": row["service"], "tool": row["tool"], "status": status})
        if row["successful_latency_count"]:
            for stat_name, key in (("median", "median_seconds"), ("p95", "p95_seconds")):
                emit("tool_latency_seconds", row[key], {"service": row["service"], "tool": row["tool"], "stat": stat_name})
    services = defaultdict(list)
    for event in snapshot["events"]:
        if event["kind"] == "tool_end" and event.get("status") == "ok":
            services[event["service"]].append(event["elapsed_seconds"])
    for service, values in sorted(services.items()):
        values.sort()
        emit("tool_latency_seconds", statistics.median(values), {"service": service, "tool": "__all__", "stat": "median"})
        emit("tool_latency_seconds", values[max(0, math.ceil(len(values)*0.95)-1)], {"service": service, "tool": "__all__", "stat": "p95"})
    if probe_active(probe, now):
        synthetic = {"dataset": "synthetic_alert_probe", "evidence_class": "synthetic", "telemetry_kind": "synthetic_test"}
        emit("financial_figures", probe["total"], common=synthetic)
        emit("verified_financial_figures", probe["verified"], common=synthetic)
        if probe["total"]: emit("verified_financial_share", probe["verified"] / probe["total"], common=synthetic)
    return "\n".join(lines) + "\n"


def read_snapshot(data_dir):
    return validate_snapshot(parse_json(safe_read(Path(data_dir) / "snapshot.json")))


def read_probe(data_dir):
    path = Path(data_dir) / "alert_probe.json"
    if not path.exists() and not path.is_symlink():
        return {"enabled": False, "verified": 0, "total": 0, "expires_at": None}
    return validate_probe(parse_json(safe_read(path, 2048)))


def page(snapshot):
    e = html.escape
    notes = "".join("<li>" + e(value) + "</li>" for value in snapshot["limitations"])
    rows = "".join("<tr><td>" + str(row["seq"]) + "</td><td>" + e(row["kind"]) + "</td><td>" + e(row["role"]) + "</td><td><code>" + e(canonical({k:v for k,v in row.items() if k not in {"seq","kind","role"}})) + "</code></td></tr>" for row in snapshot["events"])
    return ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Analyst Desk saved-run evidence</title>"
            "<style>body{font:16px system-ui,sans-serif;max-width:1200px;margin:32px auto;padding:0 24px;color:#132238;background:#f5f8fc}h1{font-size:32px}a{color:#174ea6}section{padding:20px;background:white;border:1px solid #d6e0ec;border-radius:10px;margin:20px 0}li{margin:8px 0}table{width:100%;border-collapse:collapse}td,th{padding:9px;text-align:left;border-bottom:1px solid #d6e0ec;vertical-align:top}code{font-size:12px;overflow-wrap:anywhere}</style>"
            "<h1>Analyst Desk · saved-run evidence</h1><p>Google / Meta · Sequence 25 · AI-assisted development</p>"
            "<p><a href='/snapshot'>Sanitized snapshot JSON</a> · <a href='/events'>Event JSON</a> · <a href='/metrics'>Prometheus metrics</a></p>"
            "<section><h2>What this view shows</h2><ul>" + notes + "</ul></section>"
            "<section><h2>Recorded events</h2><p>Chronological event records, not reconstructed distributed spans. The values below were recorded during the saved run.</p>"
            "<table><thead><tr><th>Sequence</th><th>Event</th><th>Role</th><th>Allowlisted metadata</th></tr></thead><tbody>" + rows + "</tbody></table></section></html>")


def make_server(data_dir, host="127.0.0.1", port=8085):
    data_dir = Path(data_dir).absolute()
    require(not any(p.is_symlink() for p in [data_dir, *data_dir.parents]) and data_dir.is_dir(), "Monitoring data directory is unavailable.")
    class Handler(BaseHTTPRequestHandler):
        server_version = "AnalystDeskSnapshot/1"
        def log_message(self, format, *args):
            # Request paths/headers may be attacker supplied. Do not log them.
            pass
        def send(self, status, body, content_type="application/json; charset=utf-8"):
            raw = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(raw)
        def do_GET(self):
            if self.path not in {"/", "/health", "/metrics", "/snapshot", "/events"}:
                self.send(404, '{"error":"not_found"}')
                return
            try:
                snapshot = read_snapshot(data_dir)
                if self.path == "/health":
                    self.send(200, canonical({"status": "ok", "dataset": DATASET, "telemetry_kind": TELEMETRY_KIND, "probe_active": probe_active(read_probe(data_dir), time.time())}))
                elif self.path == "/metrics":
                    self.send(200, metrics_text(snapshot, read_probe(data_dir)), "text/plain; version=0.0.4; charset=utf-8")
                elif self.path == "/snapshot": self.send(200, canonical(snapshot))
                elif self.path == "/events": self.send(200, canonical(snapshot["events"]))
                else: self.send(200, page(snapshot), "text/html; charset=utf-8")
            except (ValueError, OSError, TypeError, KeyError, RecursionError):
                self.send(503, '{"status":"unavailable"}')
        def deny(self): self.send(405, '{"error":"read_only"}')
        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = do_HEAD = deny
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).parent / "data")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8085)
    args = parser.parse_args(argv)
    require(args.host in {"127.0.0.1", "0.0.0.0"} and 1 <= args.port <= 65535)
    server = make_server(args.data_dir, args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
