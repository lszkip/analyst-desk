"""Request-derived topical retrieval regressions; synthetic offline sources only."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from workflow_agents import Agents, load_config
from workflow_contracts import canonical, digest
from workflow_store import RunStore
from workflow_tools import ToolBroker
from research_workflow import RiskDraft, Workflow
from evaluation.final import adapters
from test_workflow_research_prefetch import SourceBackend, ScriptedModel

REQUEST = {"question": "Compare Microsoft's operating margins and examine cybersecurity and cloud service reliability risks.",
           "tickers": ["MSFT"], "years": [2023, 2024], "include_risks": True}


class RequestedTopicTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.config = load_config()
        self.store = RunStore(self.folder/'journal.sqlite3')
        self.store.start('topics', self.config)
        self.backend = SourceBackend()
        self.broker = ToolBroker(self.backend, self.store, 'topics', copy.deepcopy(REQUEST))

    async def test_actual_request_terms_and_exact_scopes_drive_search(self):
        receipts = await self.broker.request_topic_prefetch('MSFT')
        searches = [args['request'] for _, tool, args in self.backend.calls if tool == 'search_filings']
        self.assertEqual(len(searches), 2)
        self.assertEqual({tuple(row['years']) for row in searches}, {(2023,), (2024,)})
        for row in searches:
            self.assertEqual(row['query'], REQUEST['question'])
            self.assertIn('cybersecurity', row['query'])
            self.assertIn('cloud service reliability', row['query'])
            self.assertEqual(row['tickers'], ['MSFT'])
            self.assertEqual(row['items'], ['1A'])
        self.assertEqual(len(receipts), 2)
        self.assertEqual(self.store.report('topics')['budget']['tool_calls'], 4)
        self.assertNotIn('gold', canonical(receipts))

    async def test_explicit_topic_clause_removes_financial_boilerplate_without_hardcoded_topics(self):
        for topic in ['cybersecurity and cloud service reliability', 'supplier outages and integration failures']:
            request = {**REQUEST, 'question': 'Compare operating margins and examine what the passages say about '
                       + topic + '. Distinguish wording differences from a new risk.'}
            broker = ToolBroker(self.backend, self.store, 'topics', request)
            self.assertEqual(broker.requested_risk_query(), topic)
        self.assertNotIn('expected', broker.requested_risk_query())
        short = {**REQUEST, 'question': 'Compare operating margins and discuss risks about AI.'}
        self.assertEqual(ToolBroker(self.backend, self.store, 'topics', short).requested_risk_query(), short['question'])

    async def test_reopen_reuses_receipts_without_new_calls(self):
        first = await self.broker.request_topic_prefetch('MSFT')
        count = len(self.backend.calls)
        reopened = ToolBroker(self.backend, RunStore(self.folder/'journal.sqlite3'), 'topics', copy.deepcopy(REQUEST))
        second = await reopened.request_topic_prefetch('MSFT')
        self.assertEqual(first, second)
        self.assertEqual(count, len(self.backend.calls))

    async def test_topic_samples_displace_unrelated_intro_but_keep_all_sources(self):
        for year in REQUEST['years']:
            for suffix in ['aaa', 'aab', 'aac']:
                p = self.backend.passage('MSFT', year, '1A', suffix)
                p['text'] = 'Unrelated introduction discussing competition.'
                self.broker.register_passage(p, 'MSFT')
        await self.broker.request_topic_prefetch('MSFT')
        coverage = self.broker.evidence_overview('risk_diff')
        self.assertTrue(all(row['sample_ids'][0].endswith('_topic') for row in coverage['slots']))
        samples = self.broker.writing_evidence()['source_samples']
        self.assertEqual(sum(row['id'].endswith('_topic') for row in samples), 2)
        catalog = self.broker.research_source_context('MSFT')['source_catalogue']
        self.assertEqual(sum(len(row['passages']) for row in catalog), 8)
        self.assertFalse(self.broker.writing_evidence()['source_coverage']['complete_section_coverage'])

    async def test_numeric_only_request_does_not_add_risk_search(self):
        request = {**REQUEST, 'include_risks': False}
        broker = ToolBroker(self.backend, self.store, 'topics', request)
        self.assertEqual(await broker.request_topic_prefetch('MSFT'), [])
        self.assertEqual(self.backend.calls, [])

    async def test_empty_search_is_not_invented_evidence_or_automatically_retried(self):
        class EmptyBackend(SourceBackend):
            async def call(self, service, tool, args, access=None):
                self.calls.append((service, tool, copy.deepcopy(args)))
                return {'hits': []}
        backend = EmptyBackend()
        broker = ToolBroker(backend, self.store, 'topics', copy.deepcopy(REQUEST))
        receipts = await broker.request_topic_prefetch('MSFT')
        await broker.request_topic_prefetch('MSFT')
        self.assertEqual(len(backend.calls), 2)
        self.assertTrue(all(row['passage_ids'] == [] for row in receipts))
        self.assertTrue(broker.evidence_overview('risk_diff')['missing_slots'])

    async def test_all_architectures_receive_identical_metered_request_searches(self):
        observed = []
        async def unchanged(workflow, state): return state
        async def validated(workflow, state): return {**state, 'status': 'completed'}
        for name in ['fixed_workflow', 'single_agent', 'team']:
            backend = SourceBackend()
            with patch.object(adapters, '_fixed_collection', side_effect=unchanged), \
                 patch.object(adapters, '_single_worker', side_effect=unchanged), \
                 patch.object(adapters, '_team_collection', side_effect=unchanged), \
                 patch.object(adapters, '_deterministic_validation', side_effect=validated), \
                 patch.object(Workflow, 'writer', new=AsyncMock(return_value={})):
                await adapters.execute_variant(REQUEST, {'architecture': name, 'critic': False}, self.config,
                    ScriptedModel([]), backend, self.folder/name, 'test-'+name)
            observed.append([args for _, tool, args in backend.calls if tool == 'search_filings'])
        self.assertEqual(observed[0], observed[1])
        self.assertEqual(observed[1], observed[2])
        self.assertEqual(len(observed[0]), 2)

    async def test_writer_keeps_original_requested_topics_and_quantitative_boundary(self):
        passage = self.backend.passage('MSFT', 2023, '1A', 'topic')
        self.broker.register_passage(passage, 'MSFT')
        model = ScriptedModel([{'tool':'finish','arguments':{},'final':{'title':'Risk context',
            'paragraphs':[{'text':'The cited disclosure discusses risks.', 'evidence_ids':[passage['passage_id']]}]}}])
        agents = Agents(model, self.broker, self.store, 'topics', self.config)
        await agents.ask('writer', {}, RiskDraft)
        question = model.contexts[0]['request']['question']
        self.assertIn(REQUEST['question'], question)
        self.assertIn('generated separately by code', question)
        self.assertEqual(self.broker.request, REQUEST)


if __name__ == '__main__':
    unittest.main()
