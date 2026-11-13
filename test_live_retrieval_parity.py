"""Fictional HTTP-reply fixtures verify parity checks, not retrieval quality."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from check_live_retrieval_parity import (EXPECTED_CASES, MODES, POLICY, check_client,
                                       validate_live_response, validate_reference)
from filing_retrieval import SearchRequest
from retrieval_data import RetrievalError


class FakeCatalogue:
    def __init__(self):
        self.passages = {pid: {"passage_id": pid, "ticker": "MSFT", "fiscal_year": 2025,
                              "item": "1A", "kind": "text", "source_sha256": "a" * 64}
                         for pid in ("p_first", "p_second")}

    def get(self, pid):
        if pid not in self.passages:
            raise RetrievalError("Unknown fictional passage.")
        return self.passages[pid]

    def check_live(self):
        pass

    def summary(self):
        return {"company_years": 1, "pairs": [["MSFT", 2025]], "passages": 2,
                "passage_manifest_sha256": "d" * 64}


def fixtures():
    cases = [{"id": f"R{i:03d}", "request": {"query": f"Cyber risk question {i}", "tickers": ["MSFT"],
                                              "years": [2025], "items": ["1A"], "kind": "text", "limit": 5,
                                              "decompose": False}} for i in range(1, EXPECTED_CASES + 1)]
    reference = {"schema_version": 2, "evaluation_policy": POLICY, "status": "passed", "failed_gates": [],
                 "queries": 40, "minimum_ndcg": .70, "minimum_recall": None, "allow_regression": False,
                 "label_file_bytes_sha256": "e" * 64, "generation": "b" * 64, "models_sha256": "c" * 64,
                 "rows": [{"case_id": case["id"], "mode": mode, "error": None,
                           "retrieved_passage_ids": ["p_first", "p_second"]} for case in cases for mode in MODES]}
    catalogue = FakeCatalogue()
    response = {"status": "ok", "mode": "hybrid_rerank", "generation": reference["generation"], "hits": []}
    for rank, pid in enumerate(("p_first", "p_second"), 1):
        response["hits"].append({**catalogue.get(pid), "rank": rank, "untrusted_source": True,
                                  "rrf_score": 2 / (60 + rank), "rerank_score": -float(rank),
                                  "hybrid_candidate_rank": rank, "cross_encoder_rank": rank,
                                  "rerank_fusion_score": 2 / (60 + rank), "plan_rrf_score": 1 / (60 + rank)})
    return cases, reference, catalogue, response


class FakeClient:
    def __init__(self, reference, catalogue, response, *, fail_query=None, wrong_generation=False):
        self.coverage = {**catalogue.summary(), "generation": reference["generation"],
                         "models_sha256": reference["models_sha256"], "coverage_warnings": []}
        if wrong_generation:
            self.coverage["generation"] = "f" * 64
        self.response, self.fail_query, self.requests = response, fail_query, []

    async def call_tool(self, name, args, **kwargs):
        if name == "get_coverage":
            data = self.coverage
        else:
            self.requests.append(copy.deepcopy(args))
            if len(self.requests) == self.fail_query:
                raise TimeoutError("secret transport detail must not enter reports")
            data = copy.deepcopy(self.response)
        return SimpleNamespace(is_error=False, structured_content={"status": "ok", "data": data})


class ParityValidationTests(unittest.TestCase):
    def setUp(self):
        self.cases, self.reference, self.catalogue, self.response = fixtures()
        self.request = SearchRequest.model_validate(self.cases[0]["request"])

    def test_reference_requires_same_labels_complete_rows_and_passing_fixed_policy(self):
        expected = validate_reference(self.cases, self.reference, "e" * 64)
        self.assertEqual(len(expected), 40)
        variants = []
        for field, value in (("label_file_bytes_sha256", "f" * 64), ("status", "needs_review"),
                             ("allow_regression", True), ("minimum_ndcg", .60)):
            altered = copy.deepcopy(self.reference); altered[field] = value; variants.append(altered)
        missing = copy.deepcopy(self.reference); missing["rows"].pop(); variants.append(missing)
        duplicate = copy.deepcopy(self.reference); duplicate["rows"].append(duplicate["rows"][0]); variants.append(duplicate)
        for altered in variants:
            with self.subTest(altered=altered.get("status")), self.assertRaises(RetrievalError):
                validate_reference(self.cases, altered, "e" * 64)

    def test_raw_negative_model_scores_are_valid_with_finite_complete_provenance(self):
        self.assertEqual(validate_live_response(self.response, self.request, self.catalogue, self.reference),
                         ["p_first", "p_second"])

    def test_stale_source_out_of_scope_metadata_and_duplicate_ids_fail(self):
        variants = []
        for field, value in (("source_sha256", "f" * 64), ("ticker", "AAPL"), ("untrusted_source", False)):
            altered = copy.deepcopy(self.response); altered["hits"][0][field] = value; variants.append(altered)
        duplicate = copy.deepcopy(self.response); duplicate["hits"][1] = duplicate["hits"][0]; variants.append(duplicate)
        for altered in variants:
            with self.subTest(hit=altered["hits"][0]), self.assertRaises(RetrievalError):
                validate_live_response(altered, self.request, self.catalogue, self.reference)
        self.catalogue.passages["p_first"]["fiscal_year"] = 2024
        with self.assertRaises(RetrievalError):
            validate_live_response(self.response, self.request, self.catalogue, self.reference)

    def test_missing_invalid_and_inconsistent_fusion_provenance_fail(self):
        changes = [("rerank_fusion_score", None), ("rerank_score", float("nan")),
                   ("rrf_score", float("inf")), ("hybrid_candidate_rank", True),
                   ("cross_encoder_rank", 31), ("rank", 2), ("rerank_fusion_score", .9)]
        for field, value in changes:
            altered = copy.deepcopy(self.response); altered["hits"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RetrievalError):
                validate_live_response(altered, self.request, self.catalogue, self.reference)


class ParityExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch("builtins.print"))

    async def test_all_forty_queries_are_checked_without_sending_labels(self):
        cases, reference, catalogue, response = fixtures()
        expected = validate_reference(cases, reference, "e" * 64)
        client = FakeClient(reference, catalogue, response)
        report = await check_client(client, cases, reference, catalogue, expected)
        self.assertEqual((report["status"], report["passed_cases"], report["attempted_queries"]), ("passed", 40, 40))
        self.assertEqual(client.requests, [{"request": case["request"]} for case in cases])
        self.assertFalse(report["new_quality_evaluation"])

    async def test_failed_query_stays_in_denominator_is_not_retried_and_does_not_leak_error_details(self):
        cases, reference, catalogue, response = fixtures()
        client = FakeClient(reference, catalogue, response, fail_query=7)
        report = await check_client(client, cases, reference, catalogue,
                                    validate_reference(cases, reference, "e" * 64))
        self.assertEqual((report["status"], report["passed_cases"], report["failed_cases"], len(client.requests)),
                         ("failed", 39, 1, 40))
        self.assertEqual(report["rows"][6]["error"]["type"], "TimeoutError")
        self.assertNotIn("secret", str(report))

    async def test_reordered_ids_fail_even_when_all_sources_are_valid(self):
        cases, reference, catalogue, response = fixtures()
        response["hits"].reverse()
        for rank, hit in enumerate(response["hits"], 1):
            hit["rank"] = rank
        client = FakeClient(reference, catalogue, response)
        report = await check_client(client, cases, reference, catalogue,
                                    validate_reference(cases, reference, "e" * 64))
        self.assertEqual((report["status"], report["passed_cases"], report["failed_cases"]), ("failed", 0, 40))
        self.assertEqual(report["rows"][0]["actual_passage_ids"], ["p_second", "p_first"])

    async def test_stale_service_rejects_before_queries_and_retains_all_unattempted_rows(self):
        cases, reference, catalogue, response = fixtures()
        client = FakeClient(reference, catalogue, response, wrong_generation=True)
        report = await check_client(client, cases, reference, catalogue,
                                    validate_reference(cases, reference, "e" * 64))
        self.assertEqual((report["status"], report["attempted_queries"], report["failed_cases"]), ("failed", 0, 40))
        self.assertEqual(len(report["rows"]), 40)
        self.assertFalse(client.requests)


if __name__ == "__main__":
    unittest.main()
