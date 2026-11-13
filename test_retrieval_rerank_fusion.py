"""Invented ranking fixtures verify software behavior, not retrieval quality."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from test_filing_retrieval import FixtureCase, FixtureModels


class ScoredFixtureModels(FixtureModels):
    """Supply explicit window scores without involving a production model."""

    def __init__(self, scores):
        super().__init__()
        self.scores = scores
        self.scored_texts = []

    def rerank(self, query, texts):
        self.rerank_calls += 1
        self.scored_texts.extend(texts)
        return [self.scores[text] for text in texts]


class RerankFusionTests(FixtureCase):
    def pool(self, count):
        """Create distinct fictional passage windows using fixture metadata."""
        ids = [f"p_{i:064x}" for i in range(1, count + 1)]
        evidence = {
            pid: [{"unit_id": f"window-{i}", "text": f"Fictional passage {i}",
                   "cell_ids": [f"cell-{i}"]}]
            for i, pid in enumerate(ids, 1)
        }
        return ids, evidence

    def controlled_search(self, ids, evidence, scores, *, fusion=None,
                          mode="hybrid_rerank", limit=5):
        # Candidate generation has its own integration tests. Keep its output
        # fixed here so a known ranking disagreement isolates the new stage.
        metadata = next(p for p in self.catalogue.passages.values()
                        if p["ticker"] == "MSFT" and p["fiscal_year"] == 2025)
        records = {pid: {**deepcopy(metadata), "passage_id": pid} for pid in ids}
        if fusion is None:
            fusion = {pid: 1 / (60 + rank) for rank, pid in enumerate(ids, 1)}
        models = ScoredFixtureModels(scores)
        with patch.object(self.engine, "models", models), \
                patch.object(self.engine, "_candidates",
                             return_value=(list(ids), dict(fusion), deepcopy(evidence))), \
                patch.object(self.catalogue, "get", side_effect=records.__getitem__):
            result = self.engine.search(self.request(limit=limit), mode=mode)
        return result, models

    def test_consensus_uses_all_thirty_candidates_before_top_five_cutoff(self):
        ids, evidence = self.pool(30)
        # The two individual winners disagree. Passage 2 is second in both
        # orders; passage 7 must remain eligible despite lying outside top 5.
        cross_order = [ids[6], ids[1]] + [
            pid for pid in reversed(ids) if pid not in (ids[6], ids[1])]
        scores = {evidence[pid][0]["text"]: float(100 - rank)
                  for rank, pid in enumerate(cross_order, 1)}
        result, models = self.controlled_search(ids, evidence, scores)
        hits = result["hits"]
        self.assertEqual(len(models.scored_texts), 30)
        self.assertEqual(set(models.scored_texts), set(scores))
        self.assertEqual(models.rerank_calls, 1)
        self.assertEqual(len(hits), 5)
        self.assertEqual([h["passage_id"] for h in hits[:2]], [ids[1], ids[6]])
        self.assertEqual(hits[0]["hybrid_candidate_rank"], 2)
        self.assertEqual(hits[0]["cross_encoder_rank"], 2)
        self.assertAlmostEqual(hits[0]["rerank_fusion_score"], 2 / 62)
        self.assertEqual(hits[1]["hybrid_candidate_rank"], 7)
        self.assertEqual(hits[1]["cross_encoder_rank"], 1)

    def test_raw_scores_best_window_and_both_ranks_survive_fusion(self):
        ids, evidence = self.pool(2)
        evidence[ids[0]].append({"unit_id": "better-window", "text": "Better excerpt",
                                 "cell_ids": ["better-cell"]})
        scores = {evidence[ids[0]][0]["text"]: -12.0,
                  "Better excerpt": 3.0, evidence[ids[1]][0]["text"]: 100.0}
        original_fusion = {ids[0]: 0.04, ids[1]: 0.02}
        result, _ = self.controlled_search(ids, evidence, scores, fusion=original_fusion)
        first, second = result["hits"]
        self.assertEqual(first["passage_id"], ids[0])
        self.assertEqual(first["rerank_score"], 3.0)
        self.assertEqual(second["rerank_score"], 100.0)
        self.assertEqual(first["unit_id"], "better-window")
        self.assertEqual(first["excerpt"], "Better excerpt")
        self.assertEqual(first["cell_ids"], ["better-cell"])
        self.assertEqual((first["hybrid_candidate_rank"], first["cross_encoder_rank"]), (1, 2))
        self.assertEqual((second["hybrid_candidate_rank"], second["cross_encoder_rank"]), (2, 1))
        for hit in result["hits"]:
            self.assertEqual(hit["rrf_score"], original_fusion[hit["passage_id"]])
            self.assertAlmostEqual(hit["rerank_fusion_score"], 1 / 61 + 1 / 62)
            branch_hit = next(h for h in result["branches"][0]["hits"]
                              if h["passage_id"] == hit["passage_id"])
            for key in ("rerank_score", "rrf_score", "rerank_fusion_score",
                        "hybrid_candidate_rank", "cross_encoder_rank", "excerpt"):
                self.assertEqual(hit[key], branch_hit[key])

    def test_hybrid_mode_preserves_candidate_order_scores_and_first_excerpt(self):
        ids, evidence = self.pool(8)
        evidence[ids[0]].append({"unit_id": "unused-window", "text": "Unused excerpt",
                                 "cell_ids": []})
        original_fusion = {pid: 0.5 - rank * 0.01 for rank, pid in enumerate(ids)}
        result, models = self.controlled_search(
            ids, evidence, {}, fusion=original_fusion, mode="hybrid")
        self.assertEqual(models.rerank_calls, 0)
        self.assertEqual([h["passage_id"] for h in result["hits"]], ids[:5])
        for rank, hit in enumerate(result["hits"], 1):
            pid = hit["passage_id"]
            self.assertEqual(hit["rank"], rank)
            self.assertEqual(hit["rrf_score"], original_fusion[pid])
            self.assertEqual(hit["excerpt"], evidence[pid][0]["text"])
            self.assertAlmostEqual(hit["plan_rrf_score"], 1 / (60 + rank))
            for key in ("rerank_score", "rerank_fusion_score",
                        "hybrid_candidate_rank", "cross_encoder_rank"):
                self.assertIsNone(hit[key])

    def test_fused_ties_prefer_original_hybrid_score_then_passage_id(self):
        ids, evidence = self.pool(2)
        # Opposite ranks (1, 2) and (2, 1) have identical fused scores.
        # First let the larger ID lead on hybrid score, so ID-only tie-breaking
        # would fail. Then equalize hybrid scores to exercise the ID fallback.
        cases = [(list(reversed(ids)), {ids[1]: 0.04, ids[0]: 0.03}),
                 (ids, {ids[0]: 0.04, ids[1]: 0.04})]
        for hybrid_order, original_fusion in cases:
            scores = {evidence[hybrid_order[0]][0]["text"]: 1.0,
                      evidence[hybrid_order[1]][0]["text"]: 2.0}
            for _ in range(2):
                with self.subTest(original_fusion=original_fusion):
                    result, _ = self.controlled_search(
                        hybrid_order, evidence, scores, fusion=original_fusion)
                    self.assertEqual([h["passage_id"] for h in result["hits"]], hybrid_order)
                    self.assertEqual(result["hits"][0]["rerank_fusion_score"],
                                     result["hits"][1]["rerank_fusion_score"])

    def test_score_magnitude_changes_do_not_change_rank_fusion(self):
        ids, evidence = self.pool(8)
        cross_order = [ids[5], ids[1], ids[7], ids[3], ids[0], ids[6], ids[2], ids[4]]
        scores = {evidence[pid][0]["text"]: float(10 - rank)
                  for rank, pid in enumerate(cross_order, 1)}
        shifted = {text: 1000 * score - 20000 for text, score in scores.items()}
        before, _ = self.controlled_search(ids, evidence, scores, limit=8)
        after, _ = self.controlled_search(ids, evidence, shifted, limit=8)
        self.assertEqual([h["passage_id"] for h in before["hits"]],
                         [h["passage_id"] for h in after["hits"]])
        for old, new in zip(before["hits"], after["hits"]):
            self.assertEqual(old["rerank_fusion_score"], new["rerank_fusion_score"])
            self.assertEqual(old["cross_encoder_rank"], new["cross_encoder_rank"])
            self.assertEqual(new["rerank_score"], 1000 * old["rerank_score"] - 20000)

    def test_empty_candidate_pool_returns_no_hits_without_reranking(self):
        result, models = self.controlled_search([], {}, {})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["hits"], [])
        self.assertEqual(result["branches"][0]["hits"], [])
        self.assertEqual(models.rerank_calls, 0)
        self.assertEqual(models.scored_texts, [])


if __name__ == "__main__":
    unittest.main()
