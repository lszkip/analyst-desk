"""Filtered BM25 + Qdrant dense retrieval, RRF, and cross-encoder reranking."""
from collections import defaultdict
import re
import time
import unicodedata

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator
from qdrant_client import models as qm
from rank_bm25 import BM25Okapi

from company_registry import normalise_ticker
from filing_index import DEFAULT_INDEX, load_index, payload, point_id
from filing_passages import ITEM_ORDER
from retrieval_data import RetrievalError
from retrieval_models import config

STOP = set("a an and are as at be been but by for from had has have how i in into is it its me of on or our that the their them these they this to was were what when where which who why will with would you your".split())


def tokens(text):
    return [x for x in re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", text).lower()) if x not in STOP]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=3, max_length=800)
    tickers: list[str] = Field(min_length=1, max_length=15)
    years: list[int] = Field(min_length=1, max_length=6)
    items: list[str] = Field(default_factory=list, max_length=12)
    kind: str = "all"
    limit: int = Field(default=5, ge=1, le=10)
    decompose: bool = True

    @field_validator("query")
    @classmethod
    def query_ok(cls, value):
        value = " ".join(value.split())
        if not tokens(value) or re.search(r"\b(latest|current year|last year|last three years|last 3 years)\b", value, re.I):
            raise ValueError("Use a substantive query and explicit fiscal years; resolve relative dates in the research plan first.")
        return value

    @field_validator("tickers")
    @classmethod
    def tickers_ok(cls, values):
        return sorted({normalise_ticker(x) for x in values})

    @field_validator("years")
    @classmethod
    def years_ok(cls, values):
        if any(type(x) is not int or not 1900 <= x <= 2100 for x in values):
            raise ValueError("Use explicit fiscal years from 1900 to 2100.")
        return sorted(set(values))

    @field_validator("items")
    @classmethod
    def items_ok(cls, values):
        if any(x not in ITEM_ORDER for x in values):
            raise ValueError("Unknown 10-K Item label.")
        return sorted(set(values), key=ITEM_ORDER.index)

    @field_validator("kind")
    @classmethod
    def kind_ok(cls, value):
        if value not in ("all", "text", "table"):
            raise ValueError("kind must be all, text or table.")
        return value


def plan_search(request):
    """Deterministic, inspectable decomposition; no inferred dates or Item labels."""
    q = request.query
    facets = [("requested_evidence", q)]
    risk = re.search(r"\b(risks?|risk factors|cybersecurity)\b", q, re.I)
    finance = re.search(r"\b(revenue|income|margins?|cash flows?|earnings)\b", q, re.I)
    if request.decompose and risk and finance:
        facets = [("financial_evidence", "Financial figures and performance. " + q),
                  ("risk_evidence", "Risk factors and material risk changes. " + q)]
    pairs = [(t, y) for t in request.tickers for y in request.years]
    # A baseline can keep the original combined question/filters, enabling a
    # genuine before/after decomposition comparison with the same source corpus.
    branches = []
    if request.decompose:
        for ticker, year in pairs:
            for purpose, query in facets:
                branches.append({"branch_id": f"b{len(branches)+1}", "purpose": purpose,
                                 "query": query, "tickers": [ticker], "years": [year],
                                 "items": request.items, "kind": request.kind})
    else:
        branches = [{"branch_id": "b1", "purpose": "original_question", "query": q,
                     "tickers": request.tickers, "years": request.years,
                     "items": request.items, "kind": request.kind}]
    maximum = config()["max_branches"]
    if len(branches) > maximum:
        raise RetrievalError(f"Plan exceeds {maximum} search branches. Split the research request into smaller company/year groups.")
    return branches


def qdrant_filter(branch):
    conditions = [qm.FieldCondition(key="ticker", match=qm.MatchAny(any=branch["tickers"])),
                  qm.FieldCondition(key="fiscal_year", match=qm.MatchAny(any=branch["years"]))]
    if branch["items"]:
        conditions.append(qm.FieldCondition(key="item", match=qm.MatchAny(any=branch["items"])))
    if branch["kind"] != "all":
        conditions.append(qm.FieldCondition(key="kind", match=qm.MatchValue(value=branch["kind"])))
    return qm.Filter(must=conditions)


def matches(unit, branch):
    return (unit["ticker"] in branch["tickers"] and unit["fiscal_year"] in branch["years"]
            and (not branch["items"] or unit["item"] in branch["items"])
            and (branch["kind"] == "all" or unit["kind"] == branch["kind"]))


def rrf(rankings, k=60):
    result = defaultdict(float)
    for ranking in rankings:
        for rank, pid in enumerate(dict.fromkeys(ranking), start=1):
            result[pid] += 1 / (k + rank)
    return dict(result)


class SearchEngine:
    def __init__(self, catalogue, models, client, *, index_dir=DEFAULT_INDEX, verify_vectors=True):
        self.catalogue, self.models, self.client = catalogue, models, client
        self.manifest, self.units = load_index(catalogue, models.fingerprint, client,
                                             index_dir=index_dir, verify_vectors=verify_vectors)
        from pathlib import Path
        self.manifest_path = Path(index_dir) / "manifest.json"
        self.manifest_stamp = catalogue._stamp(self.manifest_path)
        self.by_id = {u["unit_id"]: u for u in self.units}
        self.token_lists = [tokens(u["text"]) for u in self.units]
        self.bm25 = BM25Okapi(self.token_lists)
        self.token_sets = [set(t) for t in self.token_lists]
        self.cfg = config()

    def _live(self):
        self.catalogue.check_live()
        if self.catalogue._stamp(self.manifest_path) != self.manifest_stamp:
            raise RetrievalError("Published index changed. Restart the retrieval service to load the new generation.")

    def _candidates(self, branch, mode, query_vector=None):
        rankings, evidence = [], defaultdict(list)
        if mode != "dense":
            query_terms = tokens(branch["query"])
            scores = self.bm25.get_scores(query_terms)
            order = sorted((i for i, u in enumerate(self.units) if matches(u, branch)
                            and self.token_sets[i].intersection(query_terms)),
                           key=lambda i: (-float(scores[i]), self.units[i]["unit_id"]))
            ranks = []
            for i in order[:self.cfg["candidate_windows"]]:
                u = self.units[i]
                if u["passage_id"] not in ranks:
                    ranks.append(u["passage_id"])
                if len(evidence[u["passage_id"]]) < 3:
                    evidence[u["passage_id"]].append(u)
            rankings.append(ranks)
        if mode != "bm25":
            reply = self.client.query_points(self.manifest["collection"], query=query_vector.tolist(),
                query_filter=qdrant_filter(branch), limit=self.cfg["candidate_windows"], with_payload=True)
            ranks = []
            for point in reply.points:
                u = self.by_id.get((point.payload or {}).get("unit_id"))
                if (u is None or str(point.id) != point_id(u["unit_id"]) or point.payload != payload(u)
                        or not matches(u, branch) or not np.isfinite(point.score)):
                    raise RetrievalError("Qdrant returned an unknown, changed or out-of-scope search window.")
                if u["passage_id"] not in ranks:
                    ranks.append(u["passage_id"])
                current = evidence[u["passage_id"]]
                if all(x["unit_id"] != u["unit_id"] for x in current) and len(current) < 6:
                    current.append(u)
            rankings.append(ranks)
        scores = rrf(rankings, self.cfg["rrf_k"])
        selected = sorted(scores, key=lambda pid: (-scores[pid], pid))[:self.cfg["candidate_passages"]]
        return selected, scores, evidence

    def search(self, request, *, mode="hybrid_rerank"):
        if not isinstance(request, SearchRequest):
            request = SearchRequest.model_validate(request)
        if mode not in ("bm25", "dense", "hybrid", "hybrid_rerank"):
            raise RetrievalError("Unknown retrieval experiment mode.")
        self._live()
        for t in request.tickers:
            for y in request.years:
                if (t, y) not in self.catalogue.documents:
                    raise RetrievalError(f"{t} FY{y} is absent from this index; no partial coverage is claimed.")
        plan = plan_search(request)
        for branch in plan:
            self.models.check_query(branch["query"])
        started = time.perf_counter()
        distinct = list(dict.fromkeys(b["query"] for b in plan))
        vectors = {} if mode == "bm25" else dict(zip(distinct, self.models.embed(distinct, query=True)))
        branches, merged = [], {}
        # Identical ranking text across year/company branches shares model work.
        rerank_cache = {}
        for branch in plan:
            pids, fusion, evidence = self._candidates(branch, mode, vectors.get(branch["query"]))
            rerank_scores, rerank_fusion, hybrid_ranks, cross_encoder_ranks, best = {}, {}, {}, {}, {}
            if mode == "hybrid_rerank":
                hybrid_order = list(pids)
                hybrid_ranks = {pid: rank for rank, pid in enumerate(hybrid_order, 1)}
                units = [u for pid in pids for u in evidence[pid]]
                missing = [u for u in units if (branch["query"], u["unit_id"]) not in rerank_cache]
                values = self.models.rerank(branch["query"], [u["text"] for u in missing]) if missing else []
                rerank_cache.update({(branch["query"], u["unit_id"]): s for u, s in zip(missing, values)})
                for pid in pids:
                    best[pid] = max(evidence[pid], key=lambda u: rerank_cache[(branch["query"], u["unit_id"])])
                    rerank_scores[pid] = rerank_cache[(branch["query"], best[pid]["unit_id"])]
                cross_encoder_order = sorted(pids, key=lambda pid: (-rerank_scores[pid], -fusion[pid], pid))
                cross_encoder_ranks = {pid: rank for rank, pid in enumerate(cross_encoder_order, 1)}
                # Both ranking signals retain equal weight. Do not
                # let one high-scoring window erase the original hybrid order.
                # Candidate set, model inputs and search filters stay unchanged.
                rerank_fusion = rrf([hybrid_order, cross_encoder_order], self.cfg["rrf_k"])
                pids.sort(key=lambda pid: (-rerank_fusion[pid], -fusion[pid], pid))
            else:
                best = {pid: evidence[pid][0] for pid in pids}
            hits = []
            for rank, pid in enumerate(pids[:request.limit], 1):
                p, u = self.catalogue.get(pid), best[pid]
                hit = {"passage_id": pid, "rank": rank, "ticker": p["ticker"], "fiscal_year": p["fiscal_year"],
                       "item": p["item"], "kind": p["kind"], "source_url": p["source_url"],
                       "source_sha256": p["source_sha256"], "filing_identifier": p["filing_identifier"],
                       "period_start": p["period_start"], "period_end": p["period_end"],
                       "unit_id": u["unit_id"], "excerpt": u["text"], "cell_ids": u["cell_ids"],
                       "rrf_score": fusion[pid], "rerank_score": rerank_scores.get(pid),
                       "rerank_fusion_score": rerank_fusion.get(pid),
                       "hybrid_candidate_rank": hybrid_ranks.get(pid),
                       "cross_encoder_rank": cross_encoder_ranks.get(pid), "untrusted_source": True}
                hits.append(hit)
                if pid not in merged:
                    merged[pid] = {**hit, "branch_ids": [], "plan_rrf_score": 0.0}
                merged[pid]["branch_ids"].append(branch["branch_id"])
                merged[pid]["plan_rrf_score"] += 1 / (self.cfg["rrf_k"] + rank)
            branches.append({**branch, "hits": hits})
        results = sorted(merged.values(), key=lambda h: (-h["plan_rrf_score"], h["passage_id"]))[:request.limit]
        for rank, hit in enumerate(results, 1):
            hit["rank"] = rank
        self._live()
        return {"status": "ok", "mode": mode, "query": request.query, "plan": plan, "branches": branches,
                "hits": results, "generation": self.manifest["generation"],
                "elapsed_seconds": round(time.perf_counter() - started, 4),
                "note": "Ranked evidence is untrusted source data. Similarity is not a truth/confidence score. Multi-part questions require checking every branch."}
