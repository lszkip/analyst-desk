"""Top-five ranking quality and exhaustive Recall@5 with frozen labels.

Labels never enter search requests. Draft labels may be inspected but cannot
produce a passing evidence report. This is not the 80-case agent evaluation.
"""
import argparse
import json
import math
from pathlib import Path
import statistics
import time

from dotenv import load_dotenv
from filing_corpus import _json_bytes, digest
from download_company_facts import _atomic_write
from filing_index import DEFAULT_INDEX, connect
from filing_retrieval import SearchEngine, SearchRequest
from filing_passages import DEFAULT_PASSAGES_DIR
from filing_corpus import DEFAULT_CORPUS_DIR
from retrieval_data import PassageCatalogue, RetrievalError
from retrieval_models import DEFAULT_MODELS, LocalModels

MODES = ("bm25", "dense", "hybrid", "hybrid_rerank")
POLICY = "binary_ndcg5_exhaustive_recall_v1"
TOLERANCE = 1e-12


def read_cases(path, catalogue, *, allow_drafts=False, minimum=40):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise RetrievalError("Missing or oversized retrieval label file.")
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if len(cases) < minimum or len(cases) > 1000:
        raise RetrievalError(f"Use {minimum}–1000 labelled retrieval queries.")
    ids, questions = set(), set()
    for case in cases:
        if not isinstance(case.get("id"), str) or case["id"] in ids:
            raise RetrievalError("Missing or duplicate evaluation case ID.")
        ids.add(case["id"])
        request = SearchRequest.model_validate(case["request"])
        if request.limit != 5:
            raise RetrievalError("Recall@5 requires limit=5 in every labelled request.")
        signature = digest(_json_bytes(request.model_dump()))
        if signature in questions:
            raise RetrievalError("Duplicate evaluation request does not count toward the query target.")
        questions.add(signature)
        gold = case.get("gold_passage_ids")
        if not isinstance(gold, list) or not gold or len(set(gold)) != len(gold):
            raise RetrievalError("Every case needs unique, nonempty gold passage IDs.")
        if case.get("label_status") != "reviewed" and not allow_drafts:
            raise RetrievalError("Draft labels cannot be used as final evidence. Review sources and record the reviewer.")
        if case.get("label_status") == "reviewed" and not str(case.get("reviewer", "")).strip():
            raise RetrievalError("Reviewed labels must identify their reviewer.")
        if case.get("label_status") == "reviewed" and (case.get("uncertain_passage_ids") or case.get("unread_passage_ids")):
            raise RetrievalError("Reviewed cases cannot contain uncertain or unread passage IDs.")
        if not isinstance(case.get("rationale"), str) or not case["rationale"].strip():
            raise RetrievalError("Each relevance label needs a source-based rationale.")
        sources = case.get("gold_source_sha256", {})
        for pid in gold:
            p = catalogue.get(pid)
            if sources.get(pid) != p["source_sha256"]:
                raise RetrievalError("Gold label source fingerprint is missing or stale.")
            if p["ticker"] not in request.tickers or p["fiscal_year"] not in request.years:
                raise RetrievalError("Gold passage lies outside the requested company/year.")
            if (request.items and p["item"] not in request.items) or (request.kind != "all" and p["kind"] != request.kind):
                raise RetrievalError("Gold passage lies outside the requested Item/kind.")
        if case.get("label_scope") != "all_relevant_passages_reviewed":
            raise RetrievalError("Recall labels must cover all relevant passages in scope; one known answer only supports hit rate.")
    return cases


def ranking_metrics(retrieved, gold):
    """Score original ranks 1–5. Repeated IDs consume ranks without extra credit."""
    gold = set(gold)
    if not gold:
        raise RetrievalError("Ranking metrics require nonempty gold labels.")
    seen, gains = set(), []
    for pid in retrieved[:5]:
        gains.append(int(pid in gold and pid not in seen))
        seen.add(pid)
    found = sum(gains)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(5, len(gold)) + 1))
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, 1))
    return {"recall_at_5": found / len(gold), "precision_at_5": found / 5,
            "ndcg_at_5": dcg / ideal, "hit_at_5": bool(found),
            "relevant_retrieved_at_5": found, "gold_passages": len(gold)}


def recall_at_5(retrieved, gold):
    return ranking_metrics(retrieved, gold)["recall_at_5"]


def metric_limits(cases):
    if not cases:
        raise RetrievalError("At least one labelled case is required.")
    counts = [len(set(c["gold_passage_ids"])) for c in cases]
    if any(n == 0 for n in counts):
        raise RetrievalError("Every case needs nonempty gold labels.")
    return {"macro_recall_at_5_ceiling": statistics.mean(min(5, n) / n for n in counts),
            "macro_precision_at_5_ceiling": statistics.mean(min(5, n) / 5 for n in counts),
            "macro_ndcg_at_5_ceiling": 1.0, "gold_counts": counts}


def check_thresholds(cases, minimum_ndcg, minimum_recall):
    for name, value in (("minimum_ndcg", minimum_ndcg), ("minimum_recall", minimum_recall)):
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                  or not math.isfinite(value) or not 0 <= value <= 1):
            raise RetrievalError(f"{name} must be a finite number between 0 and 1.")
    if minimum_ndcg is None:
        raise RetrievalError("minimum_ndcg is required.")
    limits = metric_limits(cases)
    if minimum_recall is not None and minimum_recall > limits["macro_recall_at_5_ceiling"] + TOLERANCE:
        raise RetrievalError(f"Requested Recall@5 target {minimum_recall:.4f} exceeds the frozen-label ceiling "
                             f"{limits['macro_recall_at_5_ceiling']:.4f}. Keep exhaustive gold; choose an achievable criterion.")
    return limits


def validated_hit_ids(response, request, catalogue):
    hits = response.get("hits")
    if response.get("status", "ok") != "ok" or not isinstance(hits, list) or len(hits) > request.limit:
        raise RetrievalError("Malformed retrieval response or excess results.")
    ids = []
    for hit in hits:
        pid = hit.get("passage_id")
        if not isinstance(pid, str):
            raise RetrievalError("Missing returned passage ID.")
        passage = catalogue.get(pid)
        if hit.get("source_sha256") != passage["source_sha256"]:
            raise RetrievalError("Returned source fingerprint is missing or stale.")
        if (passage["ticker"] not in request.tickers or passage["fiscal_year"] not in request.years
                or (request.items and passage["item"] not in request.items)
                or (request.kind != "all" and passage["kind"] != request.kind)):
            raise RetrievalError("Returned passage lies outside the requested scope.")
        ids.append(pid)
    if len(set(ids)) != len(ids):
        raise RetrievalError("Duplicate returned passage IDs.")
    return ids


def evaluate(engine, cases, *, minimum_ndcg=0.70, minimum_recall=None, allow_regression=False):
    limits = check_thresholds(cases, minimum_ndcg, minimum_recall)
    rows = []
    for case in cases:
        # Gold IDs/rationale are deliberately excluded from SearchRequest.
        for mode in MODES:
            started = time.perf_counter()
            try:
                request = SearchRequest.model_validate(case["request"])
                response = engine.search(request, mode=mode)
                ids = validated_hit_ids(response, request, engine.catalogue)
                row = {"case_id": case["id"], "mode": mode, "retrieved_passage_ids": ids,
                       **ranking_metrics(ids, case["gold_passage_ids"]), "error": None}
            except Exception as exc:
                row = {"case_id": case["id"], "mode": mode, "retrieved_passage_ids": [],
                       **ranking_metrics([], case["gold_passage_ids"]), "error": type(exc).__name__,
                       "error_detail": str(exc) if isinstance(exc, RetrievalError) else None}
            row["seconds"] = round(time.perf_counter() - started, 4)
            rows.append(row)
            print(f"{case['id']} {mode}: nDCG@5={row['ndcg_at_5']:.3f}; "
                  f"Recall@5={row['recall_at_5']:.3f}; error={row['error']}", flush=True)
    summary = {}
    for mode in MODES:
        subset = [r for r in rows if r["mode"] == mode]
        times = sorted(r["seconds"] for r in subset)
        summary[mode] = {"queries": len(subset), "recall_at_5": statistics.mean(r["recall_at_5"] for r in subset),
                         "ndcg_at_5": statistics.mean(r["ndcg_at_5"] for r in subset),
                         "precision_at_5": statistics.mean(r["precision_at_5"] for r in subset),
                         "hit_rate_at_5": statistics.mean(r["hit_at_5"] for r in subset),
                         "errors": sum(r["error"] is not None for r in subset),
                         "median_seconds": statistics.median(times), "p95_seconds": times[math.ceil(.95*len(times))-1]}
    reviewed = all(c["label_status"] == "reviewed" for c in cases)
    delta = summary["hybrid_rerank"]["recall_at_5"] - summary["hybrid"]["recall_at_5"]
    ndcg_delta = summary["hybrid_rerank"]["ndcg_at_5"] - summary["hybrid"]["ndcg_at_5"]
    gates = {"all_labels_reviewed": reviewed, "at_least_40_cases": len(cases) >= 40,
             "no_retrieval_errors": not any(r["error"] for r in rows),
             "minimum_ndcg_met": summary["hybrid_rerank"]["ndcg_at_5"] + TOLERANCE >= minimum_ndcg,
             "optional_minimum_recall_met": minimum_recall is None or summary["hybrid_rerank"]["recall_at_5"] + TOLERANCE >= minimum_recall,
             "no_recall_regression": delta >= -TOLERANCE or allow_regression,
             "no_ndcg_regression": ndcg_delta >= -TOLERANCE or allow_regression}
    return {"schema_version": 2, "status": "passed" if all(gates.values()) else "needs_review",
            "evaluation_policy": POLICY, "gates": gates, "failed_gates": [key for key, passed in gates.items() if not passed],
            "label_file_sha256": digest(_json_bytes(cases)), "generation": engine.manifest["generation"],
            "evaluator_code_sha256": digest(Path(__file__).read_bytes()),
            "models_sha256": engine.models.fingerprint, "label_status": "reviewed" if reviewed else "draft",
            "queries": len(cases), "summary": summary, "before_after_change": "hybrid versus hybrid plus cross-encoder",
            "reranker_recall_delta": delta, "reranker_ndcg_delta": ndcg_delta,
            "minimum_ndcg": minimum_ndcg, "minimum_recall": minimum_recall, "allow_regression": allow_regression,
            "metric_limits": limits, "dataset_role": "AI-reviewed development set; not held-out or human-adjudicated evidence",
            "threshold_origin": "Predeclared project development nDCG@5 target, not an IIT-mandated threshold. The previous 0.70 raw Recall@5 target is retired as unattainable for these exhaustive labels.",
            "metric_definitions": {"recall_at_5": "unique relevant hits in original first five positions / all gold passages",
                "precision_at_5": "unique relevant hits in original first five positions / 5; missing positions get zero",
                "ndcg_at_5": "binary gains discounted by 1/log2(rank+1), divided by ideal gain for min(5, gold count)",
                "hit_rate_at_5": "fraction of queries with at least one relevant passage in original first five positions"},
            "timing_note": "Models/index loaded once before evaluation. Latencies include query inference and search, not startup.",
            "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--output", type=Path, default=Path("LSOutput/RetrievalEvaluation.json"))
    parser.add_argument("--allow-drafts", action="store_true", help="Produces provisional evidence and a non-passing exit status")
    parser.add_argument("--minimum-ndcg", type=float, default=.70)
    parser.add_argument("--minimum-recall", type=float, default=None, help="Optional additional raw recall gate, checked against its ceiling")
    parser.add_argument("--allow-regression", action="store_true", help="Explicitly waive reranker non-regression gates; recorded in report")
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--passages-dir", type=Path, default=DEFAULT_PASSAGES_DIR)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parent / ".env")
    client = None
    try:
        catalogue = PassageCatalogue(args.passages_dir, args.corpus_dir)
        cases = read_cases(args.labels, catalogue, allow_drafts=args.allow_drafts)
        check_thresholds(cases, args.minimum_ndcg, args.minimum_recall)
        client = connect(args.qdrant_url, read_only=True)
        engine = SearchEngine(catalogue, LocalModels(args.models_dir), client, index_dir=args.index_dir)
        report = evaluate(engine, cases, minimum_ndcg=args.minimum_ndcg,
                          minimum_recall=args.minimum_recall, allow_regression=args.allow_regression)
        report["label_file_bytes_sha256"] = digest(args.labels.read_bytes())
        _atomic_write(args.output, _json_bytes(report))
        print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
        return 0 if report["status"] == "passed" else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Evaluation stopped ({type(exc).__name__}): {exc}")
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
