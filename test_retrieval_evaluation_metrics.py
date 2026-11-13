"""Invented deterministic fixtures test evaluator logic, never retrieval quality."""
import copy
from contextlib import redirect_stdout
from io import StringIO
import json
import math
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from evaluate_filing_retrieval import (
    MODES, check_thresholds, evaluate, metric_limits, ranking_metrics, read_cases,
    validated_hit_ids,
)
from filing_retrieval import SearchRequest
from retrieval_data import RetrievalError


SOURCE_HASH = 'a' * 64
GOLD = [f'fictional_gold_{i:02}' for i in range(20)]
NEGATIVE = [f'fictional_irrelevant_{i:02}' for i in range(5)]


class InventedCatalogue:
    def __init__(self):
        self.passages = {
            pid: {'passage_id': pid, 'source_sha256': SOURCE_HASH, 'ticker': 'MSFT',
                  'fiscal_year': 2025, 'item': '1A', 'kind': 'text'}
            for pid in GOLD + NEGATIVE
        }
        for key, changes in {
            'wrong_company': {'ticker': 'AAPL'},
            'wrong_year': {'fiscal_year': 2024},
            'wrong_item': {'item': '8'},
            'wrong_kind': {'kind': 'table'},
        }.items():
            self.passages[key] = {**self.passages[GOLD[0]], 'passage_id': key, **changes}

    def get(self, pid):
        if pid not in self.passages:
            raise RetrievalError('Unknown invented passage ID.')
        return self.passages[pid]


def invented_case(number=0, gold=None):
    selected = GOLD if gold is None else list(gold)
    return {
        'id': f'fictional_case_{number:02}',
        'request': {'query': f'Fictional product evidence case {number:02}', 'tickers': ['MSFT'],
                    'years': [2025], 'items': ['1A'], 'kind': 'text', 'limit': 5, 'decompose': True},
        'gold_passage_ids': selected,
        'gold_source_sha256': {pid: SOURCE_HASH for pid in selected},
        'label_status': 'reviewed', 'reviewer': 'Fictional unit-test reviewer; not actual review evidence',
        'rationale': 'Invented metric fixture, not a measured retrieval-quality result.',
        'label_scope': 'all_relevant_passages_reviewed', 'uncertain_passage_ids': [], 'unread_passage_ids': [],
    }


def response(ids):
    return {'status': 'ok', 'hits': [{'passage_id': pid, 'source_sha256': SOURCE_HASH} for pid in ids]}


class InventedEngine:
    def __init__(self, mode_ids=None, builder=None):
        self.catalogue = InventedCatalogue()
        self.manifest = {'generation': 'fictional-fixture-generation'}
        self.models = SimpleNamespace(fingerprint='fictional-fixture-models')
        self.mode_ids = mode_ids or {}
        self.builder = builder
        self.requests = []

    def search(self, request, *, mode):
        self.requests.append((mode, request.model_dump()))
        if self.builder is not None:
            return self.builder(request, mode)
        return response(self.mode_ids.get(mode, GOLD[:5]))


class RankingMetricTests(unittest.TestCase):
    def test_perfect_top_five_does_not_cap_exhaustive_gold(self):
        result = ranking_metrics(GOLD[:5], GOLD)
        self.assertEqual(result['recall_at_5'], .25)
        self.assertEqual(result['precision_at_5'], 1)
        self.assertEqual(result['ndcg_at_5'], 1)
        self.assertEqual(result['gold_passages'], 20)
        self.assertEqual(result['relevant_retrieved_at_5'], 5)

    def test_single_relevant_rank_changes_ndcg_without_changing_recall(self):
        first = ranking_metrics([GOLD[0]], GOLD[:1])
        fifth = ranking_metrics(NEGATIVE[:4] + GOLD[:1], GOLD[:1])
        self.assertEqual(first['recall_at_5'], fifth['recall_at_5'])
        self.assertEqual(first['ndcg_at_5'], 1)
        self.assertAlmostEqual(fifth['ndcg_at_5'], 1 / math.log2(6))
        self.assertLess(fifth['ndcg_at_5'], first['ndcg_at_5'])

    def test_small_gold_set_uses_attainable_ideal_but_precision_denominator_five(self):
        result = ranking_metrics(GOLD[:2], GOLD[:2])
        self.assertEqual(result['recall_at_5'], 1)
        self.assertEqual(result['precision_at_5'], .4)
        self.assertEqual(result['ndcg_at_5'], 1)

    def test_duplicate_relevant_ids_consume_positions_without_extra_credit(self):
        result = ranking_metrics([GOLD[0], GOLD[0], GOLD[1]], GOLD[:2])
        self.assertEqual(result['relevant_retrieved_at_5'], 2)
        self.assertEqual(result['precision_at_5'], .4)
        self.assertAlmostEqual(result['ndcg_at_5'], 1.5 / (1 + 1 / math.log2(3)))

    def test_duplicate_ids_never_promote_rank_six(self):
        result = ranking_metrics([GOLD[0]] * 5 + [GOLD[1]], GOLD[:2])
        self.assertEqual(result['recall_at_5'], .5)
        self.assertEqual(result['relevant_retrieved_at_5'], 1)

    def test_only_rank_six_relevant_scores_zero_in_every_top_five_metric(self):
        result = ranking_metrics(NEGATIVE + GOLD[:1], GOLD[:1])
        for metric in ['recall_at_5', 'precision_at_5', 'ndcg_at_5', 'hit_at_5', 'relevant_retrieved_at_5']:
            self.assertEqual(result[metric], 0)

    def test_empty_results_score_zero_and_empty_gold_is_rejected(self):
        result = ranking_metrics([], GOLD[:1])
        self.assertEqual(result['ndcg_at_5'], 0)
        self.assertEqual(result['precision_at_5'], 0)
        self.assertFalse(result['hit_at_5'])
        with self.assertRaises(RetrievalError):
            ranking_metrics(GOLD[:1], [])


class LimitsAndThresholdTests(unittest.TestCase):
    def test_ceiling_is_macro_mean_per_case_not_pooled_gold(self):
        limits = metric_limits([invented_case(gold=GOLD[:1]), invented_case(1)])
        self.assertEqual(limits['macro_recall_at_5_ceiling'], .625)
        self.assertEqual(limits['macro_precision_at_5_ceiling'], .6)
        self.assertEqual(limits['macro_ndcg_at_5_ceiling'], 1)

    def test_empty_cases_or_gold_rejected(self):
        for cases in [[], [invented_case(gold=[])]]:
            with self.subTest(cases=cases), self.assertRaises(RetrievalError):
                metric_limits(cases)

    def test_impossible_optional_recall_target_rejected(self):
        with self.assertRaisesRegex(RetrievalError, 'ceiling'):
            check_thresholds([invented_case()], .7, .7)
        self.assertEqual(check_thresholds([invented_case()], .7, .25)['macro_recall_at_5_ceiling'], .25)
        check_thresholds([invented_case()], .7, None)

    def test_thresholds_must_be_finite_bounded_numbers(self):
        for value in [float('nan'), float('inf'), -float('inf'), -.01, 1.01, True, '0.7']:
            with self.subTest(value=value, metric='ndcg'), self.assertRaises(RetrievalError):
                check_thresholds([invented_case()], value, None)
            with self.subTest(value=value, metric='recall'), self.assertRaises(RetrievalError):
                check_thresholds([invented_case()], .7, value)
        with self.assertRaises(RetrievalError):
            check_thresholds([invented_case()], None, None)


class ReturnedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.catalogue = InventedCatalogue()
        self.request = SearchRequest.model_validate(invented_case()['request'])

    def test_valid_hits_keep_original_order(self):
        expected = [GOLD[2], NEGATIVE[0], GOLD[0]]
        self.assertEqual(validated_hit_ids(response(expected), self.request, self.catalogue), expected)

    def test_unknown_stale_missing_or_duplicate_evidence_rejected(self):
        stale = response(GOLD[:1]); stale['hits'][0]['source_sha256'] = 'b' * 64
        missing_hash = response(GOLD[:1]); del missing_hash['hits'][0]['source_sha256']
        missing_id = {'hits': [{'source_sha256': SOURCE_HASH}]}
        for invalid in [response(['unknown']), stale, missing_hash, missing_id, response([GOLD[0]] * 2)]:
            with self.subTest(invalid=invalid), self.assertRaises(RetrievalError):
                validated_hit_ids(invalid, self.request, self.catalogue)

    def test_each_requested_scope_dimension_is_enforced(self):
        for pid in ['wrong_company', 'wrong_year', 'wrong_item', 'wrong_kind']:
            with self.subTest(pid=pid), self.assertRaisesRegex(RetrievalError, 'scope'):
                validated_hit_ids(response([pid]), self.request, self.catalogue)

    def test_malformed_status_hits_and_excess_results_are_rejected(self):
        for invalid in [{}, {'hits': {}}, {'status': 'error', 'hits': []}, response(GOLD[:6])]:
            with self.subTest(invalid=invalid), self.assertRaises(RetrievalError):
                validated_hit_ids(invalid, self.request, self.catalogue)


class LabelIntegrityTests(unittest.TestCase):
    def load_case(self, case, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invented_labels.jsonl'
            path.write_text(json.dumps(case) + '\n')
            return read_cases(path, InventedCatalogue(), minimum=1, **kwargs)

    def test_reviewed_cases_cannot_hide_uncertain_or_unread_passages(self):
        for field in ['uncertain_passage_ids', 'unread_passage_ids']:
            case = invented_case(); case[field] = [GOLD[0]]
            with self.subTest(field=field), self.assertRaisesRegex(RetrievalError, 'uncertain or unread'):
                self.load_case(case)

    def test_drafts_require_explicit_allowance_and_stay_draft(self):
        case = invented_case(); case['label_status'] = 'draft'; case['reviewer'] = ''
        with self.assertRaisesRegex(RetrievalError, 'Draft'):
            self.load_case(case)
        self.assertEqual(self.load_case(case, allow_drafts=True)[0]['label_status'], 'draft')

    def test_stale_and_out_of_scope_gold_rejected(self):
        stale = invented_case(); stale['gold_source_sha256'][GOLD[0]] = 'b' * 64
        foreign = invented_case(gold=['wrong_company'])
        for case in [stale, foreign]:
            with self.subTest(case=case['gold_passage_ids']), self.assertRaises(RetrievalError):
                self.load_case(case)


class EvaluationGateTests(unittest.TestCase):
    def run_invented(self, engine=None, cases=None, **kwargs):
        engine = InventedEngine() if engine is None else engine
        cases = [invented_case(i) for i in range(40)] if cases is None else cases
        with redirect_stdout(StringIO()):
            report = evaluate(engine, cases, **kwargs)
        return report

    def test_forty_perfect_top_five_results_pass_with_twenty_exhaustive_gold_each(self):
        cases = [invented_case(i) for i in range(40)]
        original = copy.deepcopy(cases)
        report = self.run_invented(cases=cases)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['summary']['hybrid_rerank']['ndcg_at_5'], 1)
        self.assertEqual(report['summary']['hybrid_rerank']['recall_at_5'], .25)
        self.assertEqual(report['metric_limits']['gold_counts'], [20] * 40)
        self.assertEqual(cases, original)
        self.assertIsNone(report['minimum_recall'])
        self.assertEqual(report['minimum_ndcg'], .7)

    def test_labels_and_rationales_never_enter_search_requests(self):
        engine = InventedEngine()
        self.run_invented(engine=engine)
        self.assertEqual(len(engine.requests), 160)
        expected_fields = set(invented_case()['request'])
        for mode, request in engine.requests:
            self.assertIn(mode, MODES)
            self.assertEqual(set(request), expected_fields)
            self.assertNotIn('fictional_gold', json.dumps(request))

    def test_error_scores_zero_remains_in_denominator_and_blocks_pass(self):
        def builder(request, mode):
            if request.query.endswith('00') and mode == 'hybrid_rerank':
                raise TimeoutError('Invented timeout; no network was used.')
            return response(GOLD[:5])
        report = self.run_invented(InventedEngine(builder=builder))
        rerank = report['summary']['hybrid_rerank']
        self.assertEqual(rerank['queries'], 40)
        self.assertEqual(rerank['errors'], 1)
        self.assertEqual(rerank['ndcg_at_5'], 39 / 40)
        self.assertEqual(report['status'], 'needs_review')
        self.assertIn('no_retrieval_errors', report['failed_gates'])
        failure = next(r for r in report['rows'] if r['error'])
        self.assertEqual(failure['error'], 'TimeoutError')
        for metric in ['recall_at_5', 'precision_at_5', 'ndcg_at_5', 'hit_at_5']:
            self.assertEqual(failure[metric], 0)

    def test_malformed_duplicate_and_out_of_scope_responses_score_as_errors(self):
        invalid_responses = [None, {'hits': [None]}, response([GOLD[0]] * 2), response(['wrong_company'])]
        for invalid in invalid_responses:
            def builder(request, mode):
                return invalid
            with self.subTest(invalid=invalid):
                report = self.run_invented(InventedEngine(builder=builder), cases=[invented_case()])
                self.assertEqual(report['status'], 'needs_review')
                self.assertTrue(all(r['error'] for r in report['rows']))
                self.assertTrue(all(r['ndcg_at_5'] == 0 for r in report['rows']))

    def test_better_rank_with_fewer_relevant_hits_fails_recall_nonregression(self):
        engine = InventedEngine({'hybrid': NEGATIVE[:3] + GOLD[:2], 'hybrid_rerank': GOLD[:1]})
        report = self.run_invented(engine, [invented_case(i, GOLD[:2]) for i in range(40)], minimum_ndcg=0)
        self.assertGreater(report['reranker_ndcg_delta'], 0)
        self.assertLess(report['reranker_recall_delta'], 0)
        self.assertTrue(report['gates']['no_ndcg_regression'])
        self.assertFalse(report['gates']['no_recall_regression'])
        self.assertEqual(report['status'], 'needs_review')

    def test_more_relevant_hits_with_worse_ranking_fails_ndcg_nonregression(self):
        engine = InventedEngine({'hybrid': GOLD[:1], 'hybrid_rerank': NEGATIVE[:3] + GOLD[:2]})
        report = self.run_invented(engine, [invented_case(i, GOLD[:2]) for i in range(40)], minimum_ndcg=0)
        self.assertGreater(report['reranker_recall_delta'], 0)
        self.assertLess(report['reranker_ndcg_delta'], 0)
        self.assertFalse(report['gates']['no_ndcg_regression'])
        self.assertTrue(report['gates']['no_recall_regression'])
        self.assertEqual(report['status'], 'needs_review')

    def test_regression_override_is_explicit_recorded_and_does_not_waive_quality_floor(self):
        engine = InventedEngine({'hybrid': GOLD[:2], 'hybrid_rerank': GOLD[:1]})
        cases = [invented_case(i, GOLD[:2]) for i in range(40)]
        report = self.run_invented(engine, cases, allow_regression=True)
        self.assertTrue(report['allow_regression'])
        self.assertEqual(report['status'], 'needs_review')
        self.assertFalse(report['gates']['minimum_ndcg_met'])
        waived = self.run_invented(engine, cases, minimum_ndcg=.6, allow_regression=True)
        self.assertEqual(waived['status'], 'passed')

    def test_feasible_optional_recall_gate_is_enforced(self):
        engine = InventedEngine({mode: GOLD[:1] for mode in MODES})
        report = self.run_invented(engine, minimum_ndcg=0, minimum_recall=.25)
        self.assertFalse(report['gates']['optional_minimum_recall_met'])
        self.assertEqual(report['status'], 'needs_review')

    def test_impossible_recall_threshold_stops_before_any_search(self):
        engine = InventedEngine()
        with self.assertRaisesRegex(RetrievalError, 'ceiling'):
            self.run_invented(engine, minimum_recall=.7)
        self.assertEqual(engine.requests, [])

    def test_under_forty_or_draft_cases_never_pass_even_with_perfect_metrics(self):
        few = self.run_invented(cases=[invented_case()])
        self.assertFalse(few['gates']['at_least_40_cases'])
        self.assertEqual(few['status'], 'needs_review')
        cases = [invented_case(i) for i in range(40)]
        cases[0]['label_status'] = 'draft'
        draft = self.run_invented(cases=cases)
        self.assertFalse(draft['gates']['all_labels_reviewed'])
        self.assertEqual(draft['status'], 'needs_review')


if __name__ == '__main__':
    unittest.main()
