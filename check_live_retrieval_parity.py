"""Check live HTTP MCP integration against the accepted sequence13 rankings.

This is deployment parity, not a new financial-quality or held-out evaluation.
It makes no hosted LLM call and creates or publishes no note.
"""
import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import time

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from check_edgar_mcp import direct_http_client
from download_company_facts import _atomic_write
from evaluate_filing_retrieval import MODES, POLICY, read_cases, validated_hit_ids
from filing_retrieval import SearchRequest
from retrieval_data import PassageCatalogue, RetrievalError

EXPECTED_CASES = 40
LABELS_SHA256 = "1087398d4499fc0096fb5f55280335a8604e7ac7ce7932cf0f8ce19dc0a78454"
REFERENCE_SHA256 = "5d367125a71446c2668940bd3226dca721a0cf4486dfe77466bfaae54e6eefac"
PROVENANCE_FIELDS = ("passage_id", "rank", "rrf_score", "rerank_score", "rerank_fusion_score",
                     "hybrid_candidate_rank", "cross_encoder_rank", "plan_rrf_score")


def require(condition, message):
    if not condition:
        raise RetrievalError(message)


def validate_reference(cases, reference, labels_sha256):
    """Reject incomplete or nonpassing references before contacting the service."""
    case_ids = {case["id"] for case in cases}
    require(len(cases) == len(case_ids) == EXPECTED_CASES, "Exactly 40 unique frozen cases are required.")
    require(reference.get("schema_version") == 2 and reference.get("evaluation_policy") == POLICY,
            "Reference evaluation policy differs.")
    require(reference.get("status") == "passed" and reference.get("failed_gates") == []
            and reference.get("queries") == EXPECTED_CASES
            and reference.get("minimum_ndcg") == .70 and reference.get("minimum_recall") is None
            and reference.get("allow_regression") is False,
            "Use the accepted passing sequence13 reference with the frozen gates.")
    require(reference.get("label_file_bytes_sha256") == labels_sha256, "Label bytes differ from the reference.")
    for name in ("generation", "models_sha256"):
        value = reference.get(name)
        require(isinstance(value, str) and len(value) == 64
                and all(char in "0123456789abcdef" for char in value), "Invalid reference " + name + ".")
    rows = reference.get("rows")
    require(isinstance(rows, list), "Reference rows are missing.")
    indexed = {}
    for row in rows:
        require(isinstance(row, dict), "Malformed reference row.")
        key = (row.get("case_id"), row.get("mode"))
        require(key not in indexed and key[0] in case_ids and key[1] in MODES,
                "Duplicate or unexpected reference case/mode.")
        require(row.get("error") is None, "Reference contains a retrieval error.")
        ids = row.get("retrieved_passage_ids")
        require(isinstance(ids, list) and len(ids) <= 5 and all(isinstance(pid, str) for pid in ids)
                and len(set(ids)) == len(ids), "Malformed reference ranked IDs.")
        indexed[key] = row
    require(set(indexed) == {(case, mode) for case in case_ids for mode in MODES},
            "Reference must contain every case exactly once in each of four modes.")
    return {case: indexed[(case, "hybrid_rerank")]["retrieved_passage_ids"] for case in case_ids}


def unwrap_reply(reply):
    value = getattr(reply, "structured_content", None)
    require(not getattr(reply, "is_error", True) and isinstance(value, dict)
            and value.get("status") == "ok" and isinstance(value.get("data"), dict),
            "MCP returned an error or malformed structured reply.")
    return value["data"]


def validate_coverage(coverage, reference, catalogue):
    for field in ("generation", "models_sha256"):
        require(coverage.get(field) == reference[field], "Live " + field + " differs from sequence13.")
    local = catalogue.summary()
    for field in ("company_years", "pairs", "passages", "passage_manifest_sha256"):
        require(coverage.get(field) == local[field], "Live and local corpus coverage differ: " + field + ".")
    require(coverage.get("coverage_warnings") == [], "Live coverage has unresolved warnings.")


def validate_live_response(response, request, catalogue, reference):
    require(response.get("status") == "ok" and response.get("mode") == "hybrid_rerank",
            "Live search did not use hybrid_rerank.")
    require(response.get("generation") == reference["generation"], "Search index generation changed.")
    ids = validated_hit_ids(response, request, catalogue)
    for rank, hit in enumerate(response["hits"], 1):
        passage = catalogue.get(hit["passage_id"])
        for field in ("ticker", "fiscal_year", "item", "kind"):
            require(hit.get(field) == passage[field], "Returned source metadata differs: " + field + ".")
        require(type(hit.get("rank")) is int and hit["rank"] == rank and hit.get("untrusted_source") is True,
                "Returned rank or untrusted-source flag is invalid.")
        for field in ("rrf_score", "rerank_score", "rerank_fusion_score", "plan_rrf_score"):
            value = hit.get(field)
            require(type(value) in (int, float) and math.isfinite(value), "Invalid reranking provenance: " + field + ".")
            if field != "rerank_score":
                require(value > 0, "Nonpositive rank-fusion provenance: " + field + ".")
        for field in ("hybrid_candidate_rank", "cross_encoder_rank"):
            require(type(hit.get(field)) is int and 1 <= hit[field] <= 30,
                    "Missing or invalid sequence13 candidate rank: " + field + ".")
        expected_fusion = 1 / (60 + hit["hybrid_candidate_rank"]) + 1 / (60 + hit["cross_encoder_rank"])
        require(math.isclose(hit["rerank_fusion_score"], expected_fusion, rel_tol=0, abs_tol=1e-12),
                "Returned fusion provenance does not match the accepted equal-weight policy.")
    return ids


def safe_error(exc):
    # Transport/provider errors can contain request details; report their type only.
    return {"type": type(exc).__name__, "message": str(exc) if isinstance(exc, RetrievalError)
            else "The live check failed; no request was retried."}


def empty_report(cases):
    return {"schema_version": 1, "check": "sequence13_live_mcp_deployment_parity", "status": "failed",
            "expected_queries": EXPECTED_CASES, "attempted_queries": 0, "passed_cases": 0,
            "failed_cases": EXPECTED_CASES, "integration_parity_only": True,
            "new_quality_evaluation": False, "hosted_llm_called": False,
            "note_created_or_published": False, "global_errors": [], "rows": [
                {"case_id": case["id"], "attempted": False, "passed": False,
                 "actual_passage_ids": [], "error": {"type": "NotAttempted", "message": "Preflight did not complete."}}
                for case in cases]}


async def check_client(client, cases, reference, catalogue, expected):
    """Each search is attempted once. Failed and unattempted cases remain visible."""
    report = empty_report(cases)
    try:
        coverage = unwrap_reply(await client.call_tool("get_coverage", {}, raise_on_error=False))
        validate_coverage(coverage, reference, catalogue)
        report["coverage_before"] = coverage
    except Exception as exc:
        report["global_errors"].append(safe_error(exc))
        return report
    rows = []
    for case in cases:
        started = time.monotonic()
        row = {"case_id": case["id"], "attempted": True, "passed": False,
               "expected_passage_ids": expected[case["id"]], "actual_passage_ids": [], "error": None}
        try:
            request = SearchRequest.model_validate(case["request"])
            reply = await client.call_tool("search_filings", {"request": case["request"]}, raise_on_error=False)
            response = unwrap_reply(reply)
            if isinstance(response.get("hits"), list):
                row["actual_passage_ids"] = [hit.get("passage_id") if isinstance(hit, dict)
                                             and isinstance(hit.get("passage_id"), str) else None
                                             for hit in response["hits"]]
            actual = validate_live_response(response, request, catalogue, reference)
            row["provenance"] = [{field: hit.get(field) for field in PROVENANCE_FIELDS} for hit in response["hits"]]
            require(actual == row["expected_passage_ids"], "Live ranked IDs differ from the accepted sequence13 order.")
            row["passed"] = True
        except Exception as exc:
            row["error"] = safe_error(exc)
        row["seconds"] = round(time.monotonic() - started, 4)
        rows.append(row)
        print(case["id"] + ": live MCP parity " + ("passed" if row["passed"] else "FAILED"), flush=True)
    report["rows"] = rows
    report["attempted_queries"] = len(rows)
    report["passed_cases"] = sum(row["passed"] for row in rows)
    report["failed_cases"] = EXPECTED_CASES - report["passed_cases"]
    try:
        coverage = unwrap_reply(await client.call_tool("get_coverage", {}, raise_on_error=False))
        validate_coverage(coverage, reference, catalogue)
        catalogue.check_live()
        report["coverage_after"] = coverage
    except Exception as exc:
        report["global_errors"].append(safe_error(exc))
    if report["passed_cases"] == EXPECTED_CASES and not report["global_errors"]:
        report["status"] = "passed"
    return report


async def check_http(url, cases, reference, catalogue, expected):
    transport = StreamableHttpTransport(url, httpx_client_factory=direct_http_client)
    async with Client(transport, timeout=300, init_timeout=30) as client:
        return await check_client(client, cases, reference, catalogue, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8002/mcp")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    cases = []
    report = empty_report(cases)
    input_hashes = {}
    try:
        label_bytes, reference_bytes = args.labels.read_bytes(), args.reference.read_bytes()
        input_hashes = {"labels_sha256": hashlib.sha256(label_bytes).hexdigest(),
                        "reference_sha256": hashlib.sha256(reference_bytes).hexdigest()}
        require(input_hashes["labels_sha256"] == LABELS_SHA256, "Frozen label file hash differs.")
        require(input_hashes["reference_sha256"] == REFERENCE_SHA256, "Accepted sequence13 reference hash differs.")
        # Retain case IDs for a preflight/connection failure before the full catalogue loads.
        cases = [json.loads(line) for line in label_bytes.decode().splitlines() if line.strip()]
        reference = json.loads(reference_bytes)
        catalogue = PassageCatalogue()
        cases = read_cases(args.labels, catalogue)
        expected = validate_reference(cases, reference, LABELS_SHA256)
        report = asyncio.run(check_http(args.url, cases, reference, catalogue, expected))
    except Exception as exc:
        report = empty_report(cases)
        report["global_errors"].append(safe_error(exc))
    report.update({**input_hashes, "expected_labels_sha256": LABELS_SHA256, "expected_reference_sha256": REFERENCE_SHA256,
                   "url": args.url, "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    _atomic_write(args.output, (json.dumps(report, indent=2, allow_nan=False) + "\n").encode())
    print(json.dumps({key: report[key] for key in ("status", "expected_queries", "attempted_queries", "passed_cases", "failed_cases", "global_errors")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
