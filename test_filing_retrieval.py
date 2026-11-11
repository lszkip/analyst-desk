"""Offline correctness tests. Invented data/models are NOT retrieval-quality evidence."""
from copy import deepcopy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
from pydantic import ValidationError
from qdrant_client import QdrantClient, models as qm

import filing_corpus as corpus
import filing_passages as passages
import filing_index as index
from filing_retrieval import SearchEngine, SearchRequest, plan_search, rrf, tokens
from retrieval_data import PassageCatalogue, RetrievalError, search_units, source_path, table_rows
from retrieval_models import verify_models
from evaluate_filing_retrieval import read_cases, recall_at_5, evaluate
from setup_retrieval import setup
from test_api import write_fixture
from test_filing_passages import BODY, report


class FixtureModels:
    """Dependency-injected test double; production never imports this module."""
    dimension = 8
    fingerprint = "f" * 64
    vocabulary = ("cyber", "cloud", "revenue", "supply", "income", "research", "widgets")

    def __init__(self):
        self.embed_calls = 0
        self.rerank_calls = 0

    def windows(self, text, size=300, overlap=40):
        positions = list(re.finditer(r"\S+", text))
        result = []
        for i in range(0, len(positions), size-overlap):
            end = min(i+size, len(positions))
            a, b = positions[i].start(), positions[end-1].end()
            result.append((text[a:b], a, b))
            if end == len(positions):
                break
        return result

    def check_query(self, query):
        if len(query.split()) > 96:
            raise ValueError("Fixture token bound.")

    def embed(self, texts, query=False):
        self.embed_calls += 1
        values = np.asarray([[1.] + [float(t.lower().count(w)) for w in self.vocabulary] for t in texts], dtype=np.float32)
        return values / np.linalg.norm(values, axis=1, keepdims=True)

    def rerank(self, query, texts):
        self.rerank_calls += 1
        terms = set(tokens(query))
        return [float(len(terms.intersection(tokens(t)))) for t in texts]


def build_fixture(root, *, storage=None, large=False):
    root = Path(root)
    raw, originals, extracted = root/"raw", root/"filings", root/"passages"
    raw.mkdir(exist_ok=True)
    for ticker in ("MSFT", "GOOGL"):
        write_fixture(raw, ticker)
    plan = corpus.plan_corpus(["MSFT", "GOOGL"], [2024, 2025], data_dir=raw)
    body = BODY.replace("Supply constraints may reduce revenue.",
                        "Cyber attacks threaten cloud operations. Supply constraints may reduce revenue.")
    if large:
        body = body.replace("Revenue increased.", " ".join("Revenue grew through cloud products." for _ in range(1600)))
    pages = {d["source_url"]: report(d, body) for d in plan["documents"]}
    with patch("filing_corpus.fetch_html", side_effect=lambda url, ua: pages[url]):
        corpus.prepare_corpus(["MSFT", "GOOGL"], [2024, 2025], "tests test@example.invalid", data_dir=raw, corpus_dir=originals)
    passages.prepare_passages(["MSFT", "GOOGL"], [2024, 2025], data_dir=raw, corpus_dir=originals, passages_dir=extracted)
    catalogue = PassageCatalogue(extracted, originals)
    models = FixtureModels()
    client = QdrantClient(path=str(storage)) if storage else QdrantClient(":memory:")
    index.prepare_index(catalogue, models, client, index_dir=root/"index", data_dir=raw, progress=None)
    return catalogue, models, client


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.catalogue, self.models, self.client = build_fixture(self.root)
        self.addCleanup(self.client.close)
        self.engine = SearchEngine(self.catalogue, self.models, self.client, index_dir=self.root/"index")

    def request(self, **changes):
        return SearchRequest(**{"query":"cyber cloud risks", "tickers":["MSFT"], "years":[2025], **changes})

    def prepare(self, **kwargs):
        return index.prepare_index(self.catalogue, self.models, self.client,
            index_dir=self.root/"index", data_dir=self.root/"raw", progress=None, **kwargs)


class IndexTests(FixtureCase):
    def test_real_qdrant_local_stores_every_normalized_vector(self):
        manifest, units = index.load_index(self.catalogue, self.models.fingerprint, self.client,
            index_dir=self.root/"index", verify_vectors=True)
        self.assertEqual(self.client.count(manifest["collection"], exact=True).count, len(units))
        self.assertGreater(len(units), 0)

    def test_repeat_build_reuses_embeddings(self):
        with patch.object(self.models, "embed", side_effect=AssertionError("must reuse")):
            result = self.prepare()
        self.assertEqual(result["embedded_windows"], 0)
        self.assertEqual(result["reused_windows"], result["search_windows"])

    def test_missing_qdrant_point_repairs_from_verified_cache(self):
        name = self.engine.manifest["collection"]
        self.client.delete(name, qm.PointIdsList(points=[index.point_id(self.engine.units[0]["unit_id"])]), wait=True)
        with patch.object(self.models, "embed", side_effect=AssertionError("must reuse")):
            self.prepare()
        index.load_index(self.catalogue, self.models.fingerprint, self.client,index_dir=self.root/"index",verify_vectors=True)

    def test_model_change_requires_refresh_and_preserves_old_manifest(self):
        old = (self.root/"index/manifest.json").read_bytes()
        self.models.fingerprint = "e"*64
        with self.assertRaisesRegex(RetrievalError, "refresh"):
            self.prepare()
        self.assertEqual(old, (self.root/"index/manifest.json").read_bytes())

    def test_failed_new_generation_does_not_publish(self):
        old = (self.root/"index/manifest.json").read_bytes()
        self.models.fingerprint = "e"*64
        with patch.object(self.client, "upsert", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                self.prepare(refresh=True)
        self.assertEqual(old, (self.root/"index/manifest.json").read_bytes())
        result = self.prepare(refresh=True)
        self.assertGreater(result["reused_windows"], 0)

    def test_corrupt_vector_cache_is_rejected(self):
        file = next((self.root/"index/generations").glob("*/vectors_*.npy"))
        file.write_bytes(file.read_bytes()+b"changed")
        with self.assertRaisesRegex(RetrievalError, "cache changed"):
            self.prepare()

    def test_wrong_dimension_or_nan_vectors_are_rejected(self):
        for values in [np.zeros((1,8),dtype=np.float32), np.full((1,8),np.nan,dtype=np.float32), np.ones((1,7),dtype=np.float32)]:
            with self.subTest(shape=values.shape), self.assertRaises(RetrievalError):
                index.checked_vectors(index.vector_bytes(values),1,8)

    def test_modified_qdrant_vector_is_detected_by_verification(self):
        u = self.engine.units[0]
        self.client.upsert(self.engine.manifest["collection"], [qm.PointStruct(id=index.point_id(u["unit_id"]),
            vector=[1.,0.,0.,0.,0.,0.,0.,0.], payload=index.payload(u))],wait=True)
        with self.assertRaisesRegex(RetrievalError,"vector differs"):
            index.load_index(self.catalogue,self.models.fingerprint,self.client,index_dir=self.root/"index",verify_vectors=True)

    def test_unknown_extra_point_cannot_pass_verification(self):
        self.client.upsert(self.engine.manifest["collection"],[qm.PointStruct(id=999,vector=[1.,0.,0.,0.,0.,0.,0.,0.],payload={})],wait=True)
        with self.assertRaisesRegex(RetrievalError,"point count"):
            index.load_index(self.catalogue,self.models.fingerprint,self.client,index_dir=self.root/"index")

    def test_modified_original_stops_search(self):
        path = next(iter(self.catalogue.source_files))
        path.write_bytes(path.read_bytes()+b" ")
        with self.assertRaisesRegex(RetrievalError,"Corpus changed"):
            self.engine.search(self.request())

    def test_source_change_during_build_prevents_publication(self):
        old = (self.root/"index/manifest.json").read_bytes()
        self.models.fingerprint = "d"*64
        original_embed = self.models.embed
        def changed(texts, **kwargs):
            path = next(iter(self.catalogue.source_files))
            path.write_bytes(path.read_bytes()+b" ")
            return original_embed(texts,**kwargs)
        with patch.object(self.models,"embed",side_effect=changed),self.assertRaises(RetrievalError):
            self.prepare(refresh=True)
        self.assertEqual(old,(self.root/"index/manifest.json").read_bytes())

    def test_missing_pair_is_not_silently_partial_coverage(self):
        with self.assertRaisesRegex(RetrievalError,"absent"):
            self.engine.search(self.request(years=[2025,2023]))

    def test_index_generation_mismatch_is_rejected(self):
        with self.assertRaisesRegex(RetrievalError,"does not match"):
            index.load_index(self.catalogue,"a"*64,self.client,index_dir=self.root/"index")

    def test_changed_search_representations_are_rejected(self):
        file=next((self.root/"index/generations").glob("*/units.json"))
        file.write_bytes(file.read_bytes()+b" ")
        with self.assertRaisesRegex(RetrievalError,"representations differ"):
            index.load_index(self.catalogue,self.models.fingerprint,self.client,index_dir=self.root/"index")

    def test_duplicate_manifest_pairs_rejected_before_indexing(self):
        file=self.root/"passages/manifest.json"
        v=json.loads(file.read_bytes());v["documents"].append(v["documents"][0]);file.write_text(json.dumps(v))
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            PassageCatalogue(self.root/"passages",self.root/"filings")

    def test_paths_cannot_escape_corpus(self):
        for relative in ("../../.env","objects/other.html","/etc/passwd"):
            with self.subTest(relative=relative),self.assertRaises(RetrievalError):
                source_path(self.root/"filings","a"*64,relative)


class SearchTests(FixtureCase):
    def test_hybrid_calls_vector_search_and_cross_encoder(self):
        with patch.object(self.client,"query_points",wraps=self.client.query_points) as dense:
            result=self.engine.search(self.request())
        self.assertTrue(dense.called)
        self.assertGreater(self.models.rerank_calls,0)
        self.assertEqual(result["hits"][0]["item"],"1A")
        self.assertIsNotNone(result["hits"][0]["rerank_score"])

    def test_company_year_item_kind_filters_apply_to_both_legs(self):
        for mode in ("bm25","dense","hybrid","hybrid_rerank"):
            with self.subTest(mode=mode):
                r=self.engine.search(self.request(query="Revenue",items=["8"],kind="table"),mode=mode)
                self.assertTrue(r["hits"])
                self.assertTrue(all((h["ticker"],h["fiscal_year"],h["item"],h["kind"])==("MSFT",2025,"8","table") for h in r["hits"]))

    def test_keyword_baseline_does_not_call_models_or_vectors(self):
        with patch.object(self.models,"embed",side_effect=AssertionError),patch.object(self.models,"rerank",side_effect=AssertionError),patch.object(self.client,"query_points",side_effect=AssertionError):
            result=self.engine.search(self.request(),mode="bm25")
        self.assertTrue(result["hits"])

    def test_hybrid_without_rerank_is_a_distinct_ablation(self):
        with patch.object(self.models,"rerank",side_effect=AssertionError):
            result=self.engine.search(self.request(),mode="hybrid")
        self.assertTrue(all(h["rerank_score"] is None for h in result["hits"]))

    def test_vector_failure_does_not_silently_fallback(self):
        with patch.object(self.client,"query_points",side_effect=ConnectionError("offline")),self.assertRaises(ConnectionError):
            self.engine.search(self.request())

    def test_unknown_vector_payload_is_rejected(self):
        point=qm.ScoredPoint(id=1,version=0,score=1.0,payload={"unit_id":"unknown"})
        with patch.object(self.client,"query_points",return_value=SimpleNamespace(points=[point])),self.assertRaises(RetrievalError):
            self.engine.search(self.request())

    def test_source_link_and_complete_table_survive_search(self):
        result=self.engine.search(self.request(query="Revenue",items=["8"],kind="table"))
        hit=result["hits"][0]
        p=self.catalogue.get(hit["passage_id"])
        self.assertEqual(hit["source_sha256"],p["source_sha256"])
        self.assertEqual(p["table"]["rows"][1][1]["text"],"100")
        self.assertTrue(hit["cell_ids"])
        self.assertTrue(hit["untrusted_source"])

    def test_empty_filter_result_does_not_invent_evidence(self):
        result=self.engine.search(self.request(items=["16"]))
        self.assertEqual(result["hits"],[])

    def test_duplicate_windows_do_not_repeat_parent_in_results(self):
        result=self.engine.search(self.request(query="Revenue"))
        ids=[h["passage_id"] for h in result["hits"]]
        self.assertEqual(len(ids),len(set(ids)))

    def test_query_plan_covers_each_explicit_company_year_and_facet(self):
        request=self.request(query="Compare operating margins and risk factors",tickers=["MSFT","GOOGL"],years=[2024,2025])
        plan=plan_search(request)
        self.assertEqual(len(plan),8)
        result=self.engine.search(request)
        self.assertEqual(len(result["branches"]),8)
        self.assertTrue(all(b["hits"] for b in result["branches"]))

    def test_single_query_baseline_does_not_decompose(self):
        plan=plan_search(self.request(tickers=["MSFT","GOOGL"],years=[2024,2025],decompose=False))
        self.assertEqual(len(plan),1)
        self.assertEqual(plan[0]["years"],[2024,2025])

    def test_aliases_are_explicitly_normalized(self):
        self.assertEqual(self.request(tickers=["Microsoft","MSFT"]).tickers,["MSFT"])

    def test_invalid_limits_types_paths_and_relative_dates_rejected(self):
        for change in ({"limit":True},{"limit":0},{"limit":11},{"years":[True]},{"years":["2025"]},
                       {"tickers":["../../.env"]},{"items":["99"]},{"query":"latest revenue"},{"query":"and the"}):
            with self.subTest(change=change),self.assertRaises(ValidationError):self.request(**change)

    def test_unbounded_decomposition_is_rejected(self):
        from company_registry import COMPANIES
        with self.assertRaisesRegex(RetrievalError,"18"):
            plan_search(self.request(tickers=list(COMPANIES),years=[2024,2025]))

    def test_unknown_field_does_not_reach_models(self):
        with self.assertRaises(ValidationError):
            SearchRequest.model_validate({**self.request().model_dump(),"data_dir":"/etc"})

    def test_rrf_combines_ranks_and_deduplicates_each_list(self):
        value=rrf([["a","a","b"],["b","c"]])
        self.assertAlmostEqual(value["a"],1/61)
        self.assertAlmostEqual(value["b"],1/62+1/61)

    def test_token_normalization_preserves_identifiers_and_numbers(self):
        self.assertEqual(tokens("THE cloud-based 2025 revenue"),["cloud","based","2025","revenue"])


class RepresentationTests(FixtureCase):
    def test_all_table_cells_have_search_window_links(self):
        units=search_units(self.catalogue,self.models)
        represented=set(c for u in units for c in u["cell_ids"])
        expected=set(c for p in self.catalogue.passages.values() if p["kind"]=="table" for _,ids in table_rows(p["table"]) for c in ids)
        self.assertEqual(represented,expected)

    def test_plain_text_ablation_retains_original_tables(self):
        before=deepcopy(self.catalogue.passages)
        units=search_units(self.catalogue,self.models,plain_text=True)
        self.assertTrue(all(not u["cell_ids"] for u in units))
        self.assertEqual(before,self.catalogue.passages)

    def test_blank_merged_and_nested_cells_are_retained(self):
        cell={"cell_id":"c1","row":0,"column":0,"rowspan":2,"colspan":2,"text":"","nested_tables":[]}
        nested={"cell_id":"c2","row":0,"column":0,"rowspan":1,"colspan":1,"text":"Nested cash","nested_tables":[]}
        cell["nested_tables"]=[{"rows":[[nested]]}]
        rows=list(table_rows({"rows":[[cell]]}))
        self.assertIn("[blank cells]",rows[0][0]);self.assertEqual(cell["rowspan"],2);self.assertEqual(rows[1][1],["c2"])

    def test_unknown_passage_id_and_path_are_rejected(self):
        for pid in ("../../.env","p_"+"a"*64):
            with self.subTest(pid=pid),self.assertRaises(RetrievalError):self.catalogue.get(pid)

    def test_section_pagination_matches_original_passage_order(self):
        result=self.catalogue.section("MSFT",2025,"8",0,1)
        self.assertEqual(result["total"],2)
        self.assertEqual(result["next_offset"],1)
        following=self.catalogue.section("MSFT",2025,"8",1,1)
        self.assertEqual(following["passages"][0]["kind"],"table")


class EvaluationTests(FixtureCase):
    def case(self):
        pid=next(p["passage_id"] for p in self.catalogue.passages.values() if p["ticker"]=="MSFT" and p["fiscal_year"]==2025 and p["item"]=="1A")
        return {"id":"case1","request":self.request().model_dump(),"gold_passage_ids":[pid],
                "gold_source_sha256":{pid:self.catalogue.get(pid)["source_sha256"]},"label_status":"reviewed",
                "reviewer":"fictional test reviewer","rationale":"Invented cyber evidence.","label_scope":"all_relevant_passages_reviewed"}

    def write_cases(self,cases):
        file=self.root/"labels.jsonl";file.write_text("\n".join(json.dumps(c) for c in cases));return file

    def test_recall_is_not_hit_rate(self):
        self.assertEqual(recall_at_5(["a"],["a","b"]),.5)
        self.assertEqual(recall_at_5(["x","x","x","x","x","a"],["a"]),0)

    def test_minimum_requires_forty_queries(self):
        with self.assertRaisesRegex(RetrievalError,"40"):
            read_cases(self.write_cases([self.case()]),self.catalogue)

    def test_draft_labels_cannot_pass_as_reviewed(self):
        case=self.case();case["label_status"]="draft"
        with self.assertRaisesRegex(RetrievalError,"Draft"):
            read_cases(self.write_cases([case]),self.catalogue,minimum=1)

    def test_missing_fingerprint_or_out_of_scope_gold_rejected(self):
        for change in ({"gold_source_sha256":{}},{"request":self.request(tickers=["GOOGL"]).model_dump()}):
            case={**self.case(),**change}
            with self.subTest(change=change),self.assertRaises(RetrievalError):
                read_cases(self.write_cases([case]),self.catalogue,minimum=1)

    def test_duplicate_queries_do_not_count_twice(self):
        a=self.case();b={**a,"id":"case2"}
        with self.assertRaisesRegex(RetrievalError,"Duplicate evaluation request"):
            read_cases(self.write_cases([a,b]),self.catalogue,minimum=1)

    def test_labels_never_enter_search_request_and_failures_count_zero(self):
        seen=[]
        def failed(request,mode):
            seen.append(request.model_dump());raise ConnectionError("unavailable")
        with patch.object(self.engine,"search",side_effect=failed),redirect_stdout(StringIO()):
            result=evaluate(self.engine,[self.case()])
        self.assertEqual(result["status"],"needs_review")
        self.assertEqual(result["summary"]["hybrid_rerank"]["errors"],1)
        self.assertTrue(all("gold_passage_ids" not in r for r in seen))


class ModelAndSetupTests(unittest.TestCase):
    def test_missing_models_fail_without_downloading(self):
        with tempfile.TemporaryDirectory() as d,patch("huggingface_hub.snapshot_download",side_effect=AssertionError),self.assertRaisesRegex(ValueError,"Missing or changed"):
            verify_models(d)

    def test_setup_preserves_existing_env_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(StringIO()):
            root=Path(d);env=root/".env";env.write_text("OPENROUTER_API_KEY=fictional-test-key\n")
            setup(root);first=env.read_bytes();setup(root)
            self.assertEqual(first,env.read_bytes())
            self.assertTrue(first.startswith(b"OPENROUTER_API_KEY=fictional-test-key\n"))
            self.assertEqual(first.count(b"QDRANT_API_KEY="),1)
            self.assertEqual(env.stat().st_mode&0o777,0o600)

    def test_setup_rejects_same_read_and_write_keys(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);root.joinpath(".env").write_text("QDRANT_API_KEY="+"a"*40+"\nQDRANT_READ_ONLY_API_KEY="+"a"*40+"\n")
            with self.assertRaisesRegex(ValueError,"must differ"):setup(root)


if __name__ == "__main__":
    unittest.main()
