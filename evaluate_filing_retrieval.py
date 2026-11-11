"""Passage-level Recall@5 with frozen labels, failures and before/after runs.

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


def recall_at_5(retrieved, gold):
    return len(set(retrieved[:5]).intersection(gold)) / len(set(gold))


def evaluate(engine, cases, *, minimum_recall=0.70, allow_regression=False):
    rows = []
    for case in cases:
        # Gold IDs/rationale are deliberately excluded from SearchRequest.
        for mode in ("bm25", "dense", "hybrid", "hybrid_rerank"):
            started = time.perf_counter()
            try:
                response = engine.search(SearchRequest.model_validate(case["request"]), mode=mode)
                ids = [h["passage_id"] for h in response["hits"]]
                row = {"case_id": case["id"], "mode": mode, "retrieved_passage_ids": ids,
                       "recall_at_5": recall_at_5(ids, case["gold_passage_ids"]),
                       "hit_at_5": bool(set(ids).intersection(case["gold_passage_ids"])), "error": None}
            except Exception as exc:
                row = {"case_id": case["id"], "mode": mode, "retrieved_passage_ids": [],
                       "recall_at_5": 0.0, "hit_at_5": False, "error": type(exc).__name__}
            row["seconds"] = round(time.perf_counter() - started, 4)
            rows.append(row)
            print(f"{case['id']} {mode}: Recall@5={row['recall_at_5']:.3f}; error={row['error']}", flush=True)
    summary = {}
    for mode in ("bm25", "dense", "hybrid", "hybrid_rerank"):
        subset = [r for r in rows if r["mode"] == mode]
        times = sorted(r["seconds"] for r in subset)
        summary[mode] = {"queries": len(subset), "recall_at_5": statistics.mean(r["recall_at_5"] for r in subset),
                         "hit_rate_at_5": statistics.mean(r["hit_at_5"] for r in subset),
                         "errors": sum(r["error"] is not None for r in subset),
                         "median_seconds": statistics.median(times), "p95_seconds": times[math.ceil(.95*len(times))-1]}
    reviewed = all(c["label_status"] == "reviewed" for c in cases)
    delta = summary["hybrid_rerank"]["recall_at_5"] - summary["hybrid"]["recall_at_5"]
    passed = (reviewed and len(cases) >= 40 and not any(r["error"] for r in rows)
              and summary["hybrid_rerank"]["recall_at_5"] >= minimum_recall
              and (delta >= 0 or allow_regression))
    return {"schema_version": 1, "status": "passed" if passed else "needs_review",
            "label_file_sha256": digest(_json_bytes(cases)), "generation": engine.manifest["generation"],
            "models_sha256": engine.models.fingerprint, "label_status": "reviewed" if reviewed else "draft",
            "queries": len(cases), "summary": summary, "before_after_change": "hybrid versus hybrid plus cross-encoder",
            "reranker_recall_delta": delta, "minimum_recall": minimum_recall,
            "threshold_origin": "project acceptance target; the brief does not specify a numeric Recall threshold",
            "timing_note": "Models/index loaded once before evaluation. Latencies include query inference and search, not startup.",
            "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--output", type=Path, default=Path("LSOutput/RetrievalEvaluation.json"))
    parser.add_argument("--allow-drafts", action="store_true", help="Produces provisional evidence and a non-passing exit status")
    parser.add_argument("--minimum-recall", type=float, default=.70)
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--passages-dir", type=Path, default=DEFAULT_PASSAGES_DIR)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--index-dir", type=Path, default=DEFAULT_INDEX)
    args = parser.parse_args()
    if not 0 <= args.minimum_recall <= 1:
        parser.error("--minimum-recall must be between 0 and 1.")
    load_dotenv(Path(__file__).resolve().parent / ".env")
    client = None
    try:
        catalogue = PassageCatalogue(args.passages_dir, args.corpus_dir)
        cases = read_cases(args.labels, catalogue, allow_drafts=args.allow_drafts)
        client = connect(args.qdrant_url, read_only=True)
        engine = SearchEngine(catalogue, LocalModels(args.models_dir), client, index_dir=args.index_dir)
        report = evaluate(engine, cases, minimum_recall=args.minimum_recall)
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
