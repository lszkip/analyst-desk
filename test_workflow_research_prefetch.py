"""Offline prefetch/receipt tests; no model or retrieval-quality claims."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from research_workflow import Workflow
from workflow_agents import Agents, load_config
from workflow_contracts import Handoff, WorkflowError, canonical, digest
from workflow_store import RunStore
from workflow_tools import ToolBroker


REQUEST = {"question": "Compare operating margins and competition risks for the requested companies.",
           "tickers": ["GOOGL", "META"], "years": [2024, 2025], "include_risks": True}
PLAN = {"objective": "Compare source-supported results.", "outline": ["Financial comparison", "Risk comparison"],
        "research_query": "competition and customer demand", "limitations": ["Source samples are incomplete."]}


class SourceBackend:
    def __init__(self):
        self.calls = []
        self.outcomes = {}
        self.passages = {}
        self.results = []

    def passage(self, ticker, year, item, suffix="first"):
        key = f"p_{ticker}_{year}_{item}_{suffix}"
        passage = {"passage_id": key, "ticker": ticker, "fiscal_year": year, "item": item, "kind": "text",
                   "text": "Original disclosure about competition and customer demand. " * 80,
                   "source_url": "https://www.sec.gov/Archives/edgar/data/0/synthetic.htm", "source_sha256": "a" * 64}
        self.passages[key] = passage
        return passage

    async def call(self, service, tool, args, access=None):
        self.calls.append((service, tool, copy.deepcopy(args)))
        if tool == "get_section":
            slot = (args["ticker"], args["year"], args["item"])
            result = copy.deepcopy(self.outcomes.get(slot))
            if result is None:
                result = {"status": "ok", "passages": [self.passage(*slot, str(args["offset"]))],
                          "total_passages": 8, "next_offset": args["offset"] + 2}
            self.results.append(copy.deepcopy(result))
            return result
        if tool == "search_filings":
            request = args["request"]
            passage = self.passage(request["tickers"][0], request["years"][0], request["items"][0], "topic")
            return {"hits": [{"passage_id": passage["passage_id"], "ticker": passage["ticker"],
                              "fiscal_year": passage["fiscal_year"], "item": passage["item"], "excerpt": passage["text"][:100]}]}
        if tool == "get_passage":
            return {"passage": copy.deepcopy(self.passages[args["passage_id"]])}
        raise AssertionError(tool)


class ScriptedModel:
    def __init__(self, actions):
        self.actions = list(actions)
        self.contexts = []

    async def complete(self, messages, config):
        self.contexts.append(json.loads(messages[1]["content"]))
        action = self.actions.pop(0)
        return {"content": canonical(action), "usage": {"prompt_tokens": 20, "completion_tokens": 20,
                "total_tokens": 40, "cost": 0}, "model": "scripted-test-only"}


def finish(ids=()):
    return {"tool": "finish", "arguments": {}, "final": {"summary": "Only source samples were reviewed.",
            "evidence_ids": list(ids), "issues": []}}


class ResearchPrefetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "journal.sqlite3"
        self.store = RunStore(self.path)
        self.config = load_config()
        self.store.start("prefetch", self.config)
        self.backend = SourceBackend()
        self.broker = ToolBroker(self.backend, self.store, "prefetch", copy.deepcopy(REQUEST))

    def agents(self, actions):
        model = ScriptedModel(actions)
        return Agents(model, self.broker, self.store, "prefetch", self.config), model

    async def ask(self, seed, actions):
        agents, model = self.agents(actions)
        result = await agents.ask("researcher", {"registered_sources": seed["sources"]}, Handoff, "GOOGL",
                                  require_sections=True, initial_section_lookups=seed["lookups"])
        return result, model

    async def test_required_prefetch_uses_scoped_metered_broker_calls_and_exact_receipts(self):
        seed = await self.broker.research_prefetch("GOOGL")
        self.assertEqual(len(self.backend.calls), 4)
        self.assertEqual(self.store.report("prefetch")["budget"]["tool_calls"], 4)
        self.assertEqual({(c[2]["ticker"], c[2]["year"], c[2]["item"]) for c in self.backend.calls},
                         {("GOOGL", year, item) for year in REQUEST["years"] for item in ("7", "1A")})
        for lookup, original in zip(seed["lookups"], self.backend.results):
            self.assertEqual(lookup["receipt"]["result_sha256"], digest(original))
            self.assertEqual(lookup["receipt"]["operation_sha256"], digest(["get_section", lookup["arguments"]]))
            self.assertFalse(lookup["reused"])
            for passage in original["passages"]:
                self.assertEqual(self.store.evidence("prefetch")[passage["passage_id"]]["passage"], passage)
        report_text = canonical(self.store.report("prefetch")["events"])
        self.assertNotIn("Original disclosure", report_text)
        self.assertFalse(seed["sources"]["complete_section_coverage"])

    async def test_seeded_successful_page_is_deduplicated_but_next_page_is_available(self):
        seed = await self.broker.research_prefetch("GOOGL")
        args = seed["lookups"][0]["arguments"]
        ids = list(self.store.evidence("prefetch"))
        actions = [{"tool": "get_section", "arguments": args, "final": None},
                   {"tool": "get_section", "arguments": {**args, "offset": 2}, "final": None}, finish(ids)]
        result, model = await self.ask(seed, actions)
        self.assertEqual(result["evidence_ids"], ids)
        self.assertEqual(len(self.backend.calls), 5)
        self.assertEqual(self.backend.calls[-1][2]["offset"], 2)
        self.assertEqual(model.contexts[1]["recent_observations"][-1]["error"], "duplicate_tool_call")
        self.assertEqual(self.store.report("prefetch")["budget"]["tool_calls"], 5)

    async def test_empty_and_failed_lookups_allow_honest_handoff_without_false_absence(self):
        self.backend.outcomes[("GOOGL", 2024, "7")] = {"status": "error", "code": "evidence_unavailable"}
        self.backend.outcomes[("GOOGL", 2024, "1A")] = {"status": "ok", "passages": [], "next_offset": None}
        seed = await self.broker.research_prefetch("GOOGL")
        result, model = await self.ask(seed, [finish(self.store.evidence("prefetch"))])
        self.assertEqual(len(model.contexts), 1)
        self.assertEqual(len(self.backend.calls), 4)
        self.assertIn("does not establish", result["issues"][0])
        self.assertIn('"year":2024', result["issues"][0])
        lookups = model.contexts[0]["prefetched_section_lookups"]
        self.assertEqual(lookups[0]["receipt"]["status"], "error")
        self.assertEqual(lookups[1]["receipt"]["passage_ids"], [])

    async def test_failures_and_empty_outcomes_survive_reopen_without_automatic_retry(self):
        self.backend.outcomes[("GOOGL", 2024, "7")] = {"status": "error", "code": "evidence_unavailable"}
        self.backend.outcomes[("GOOGL", 2024, "1A")] = {"status": "ok", "passages": []}
        before = await self.broker.research_prefetch("GOOGL")
        reopened = ToolBroker(self.backend, RunStore(self.path), "prefetch", copy.deepcopy(REQUEST))
        after = await reopened.research_prefetch("GOOGL")
        self.assertEqual(len(self.backend.calls), 4)
        self.assertTrue(all(row["reused"] for row in after["lookups"]))
        self.assertEqual([row["receipt"] for row in before["lookups"]], [row["receipt"] for row in after["lookups"]])
        self.assertEqual(after["sources"], before["sources"])

    async def test_model_can_explicitly_retry_a_failed_lookup(self):
        slot = ("GOOGL", 2024, "7")
        self.backend.outcomes[slot] = {"status": "error", "code": "evidence_unavailable"}
        seed = await self.broker.research_prefetch("GOOGL")
        del self.backend.outcomes[slot]
        await self.ask(seed, [{"tool": "get_section", "arguments": seed["lookups"][0]["arguments"], "final": None}, finish()])
        self.assertEqual(len(self.backend.calls), 5)
        self.assertEqual(self.broker.evidence_overview("researcher", "GOOGL")["missing_slots"], [])

    async def test_receipt_only_reuse_is_scoped_and_does_not_invent_page_calls(self):
        for ticker in REQUEST["tickers"]:
            for year in REQUEST["years"]:
                for item in ("7", "1A"):
                    for suffix in ("first", "second", "topic"):
                        self.broker.register_passage(self.backend.passage(ticker, year, item, suffix), ticker)
        seed = await self.broker.research_prefetch("GOOGL")
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(seed["lookups"], [])
        index = seed["sources"]["source_catalogue"]
        self.assertEqual(len(index), 4)
        self.assertTrue(all(row["ticker"] == "GOOGL" for row in index))
        ids = [p["id"] for row in index for p in row["passages"]]
        self.assertEqual(len(ids), 12)
        self.assertTrue(any(key.endswith("topic") for key in ids))
        self.assertNotIn("META", canonical(seed["sources"]))
        self.assertLess(len(canonical(seed["sources"]).encode()), 18000)
        self.assertTrue(all(sample["excerpt_truncated"] for sample in seed["sources"]["source_samples"]))

    async def test_topical_search_and_full_passage_tools_remain_available(self):
        seed = await self.broker.research_prefetch("GOOGL")
        topic = "p_GOOGL_2025_1A_topic"
        result, model = await self.ask(seed, [
            {"tool": "search_filings", "arguments": {"ticker": "GOOGL", "year": 2025, "item": "1A", "query": "customer renewals"}, "final": None},
            {"tool": "get_passage", "arguments": {"passage_id": topic}, "final": None}, finish([topic])])
        self.assertEqual(result["evidence_ids"], [topic])
        self.assertEqual([c[1] for c in self.backend.calls[4:]], ["search_filings", "get_passage", "get_passage"])
        self.assertIn("search_filings", model.contexts[0]["tools"])
        self.assertIn("get_passage", model.contexts[0]["tools"])
        later = await self.broker.research_prefetch("GOOGL")
        self.assertEqual(len(self.backend.calls), 7)
        self.assertIn(topic, canonical(later["sources"]["source_catalogue"]))

    async def test_new_topical_id_remains_visible_after_observation_rollover(self):
        seed = await self.broker.research_prefetch("GOOGL")
        self.broker.register_passage(self.backend.passage("GOOGL", 2025, "1A", "1"), "GOOGL")
        existing = list(self.store.evidence("prefetch"))[:4]
        topic = "p_GOOGL_2025_1A_topic"
        actions = [{"tool": "search_filings", "arguments": {"ticker": "GOOGL", "year": 2025,
                    "item": "1A", "query": "customer renewals"}, "final": None}]
        actions.extend({"tool": "get_passage", "arguments": {"passage_id": key}, "final": None} for key in existing)
        actions.append(finish([topic]))
        _, model = await self.ask(seed, actions)
        self.assertNotIn(topic, canonical(model.contexts[0]["task"]["registered_sources"]))
        last = model.contexts[-1]
        self.assertNotIn(topic, canonical(last["recent_observations"]))
        self.assertNotIn(topic, canonical(last["saved_evidence"]))
        self.assertIn(topic, canonical(last["task"]["registered_sources"]["source_catalogue"]))
        self.assertLess(len(canonical(last["task"]["registered_sources"]).encode()), 18000)

    async def test_cross_company_seed_is_rejected_before_model_or_new_tool_calls(self):
        seed = await self.broker.research_prefetch("META")
        agents, model = self.agents([finish()])
        with self.assertRaises(WorkflowError) as error:
            await agents.ask("researcher", {}, Handoff, "GOOGL", require_sections=True,
                             initial_section_lookups=seed["lookups"])
        self.assertEqual(error.exception.code, "invalid_prefetch_seed")
        self.assertEqual(model.contexts, [])
        self.assertEqual(len(self.backend.calls), 4)

    async def test_forged_seed_cannot_satisfy_an_unattempted_slot(self):
        args = {"ticker": "GOOGL", "year": 2024, "item": "7", "offset": 0}
        forged = [{"arguments": args, "reused": False,
                   "receipt": {"operation_sha256": digest(["get_section", args]), "result_sha256": "a" * 64,
                               "status": "ok", "passage_ids": []}}]
        agents, model = self.agents([finish()])
        with self.assertRaises(WorkflowError) as error:
            await agents.ask("researcher", {}, Handoff, "GOOGL", require_sections=True, initial_section_lookups=forged)
        self.assertEqual(error.exception.code, "invalid_prefetch_seed")
        self.assertEqual(model.contexts, [])
        self.assertEqual(self.backend.calls, [])

    async def test_seed_receipt_cannot_be_modified_and_cannot_be_given_to_other_roles(self):
        seed = await self.broker.research_prefetch("GOOGL")
        forged = copy.deepcopy(seed["lookups"])
        forged[0]["receipt"]["status"] = "error"
        agents, model = self.agents([finish()])
        for role, rows in (("researcher", forged), ("risk_diff", seed["lookups"])):
            with self.assertRaises(WorkflowError) as error:
                await agents.ask(role, {}, Handoff, "GOOGL", require_sections=True, initial_section_lookups=rows)
            self.assertEqual(error.exception.code, "invalid_prefetch_seed")
        self.assertEqual(model.contexts, [])

    async def test_exhausted_tool_budget_stops_before_backend_call(self):
        self.store.reserve("prefetch", tools=self.config["tool_budget"])
        with self.assertRaises(WorkflowError) as error:
            await self.broker.research_prefetch("GOOGL")
        self.assertEqual(error.exception.code, "budget_exhausted")
        self.assertEqual(self.backend.calls, [])

    async def test_prefetch_keeps_original_source_scope_guard(self):
        wrong = self.backend.passage("META", 2024, "7")
        self.backend.outcomes[("GOOGL", 2024, "7")] = {"passages": [wrong]}
        with self.assertRaises(WorkflowError) as error:
            await self.broker.research_prefetch("GOOGL")
        self.assertEqual(error.exception.code, "scope_denied")
        self.assertEqual(self.store.evidence("prefetch"), {})

    async def test_graph_researcher_prefetches_before_single_model_handoff_and_reuses_on_replan(self):
        ids = [f"p_GOOGL_{year}_{item}_0" for year in REQUEST["years"] for item in ("7", "1A")]
        agents, model = self.agents([finish(ids), finish(ids)])
        workflow = Workflow(agents, self.broker, self.config)
        state = {"request": REQUEST, "plan": PLAN, "company": "GOOGL"}
        await workflow.researcher(state)
        await workflow.researcher({**state, "plan": {**PLAN, "objective": "Reassess source limitations."}})
        self.assertEqual(len(self.backend.calls), 4)
        self.assertEqual(len(model.contexts), 2)
        self.assertTrue(all(c["saved_evidence"]["missing_slots"] == [] for c in model.contexts))
        self.assertEqual(model.contexts[0]["prefetched_section_lookups"], model.contexts[1]["prefetched_section_lookups"])
        self.assertEqual(self.config["token_budget"], 900000)
        self.assertEqual(self.config["model"], "openai/gpt-4.1-mini")

    async def test_identical_reopened_seed_preserves_model_cache_identity(self):
        before = await self.broker.research_prefetch("GOOGL")
        ids = list(self.store.evidence("prefetch"))
        _, first_model = await self.ask(before, [finish(ids)])
        self.broker = ToolBroker(self.backend, RunStore(self.path), "prefetch", copy.deepcopy(REQUEST))
        after = await self.broker.research_prefetch("GOOGL")
        _, second_model = await self.ask(after, [])
        self.assertEqual(len(first_model.contexts), 1)
        self.assertEqual(second_model.contexts, [])
        report = self.store.report("prefetch")
        self.assertEqual(report["metrics"]["model_response_cache_hits"], 1)
        self.assertEqual(report["budget"]["tool_calls"], 4)

    async def test_writer_instructions_require_company_period_and_qualifier_support(self):
        from test_research_workflow import FixtureBackend, FixtureModel, REQUEST as writer_request

        class CapturingModel(FixtureModel):
            async def complete(self, messages, config):
                self.prompt = json.loads(messages[0]["content"])
                return await super().complete(messages, config)

        backend = FixtureBackend(self.tmp.name)
        broker = ToolBroker(backend, self.store, "prefetch", writer_request)
        for ticker in writer_request["tickers"]:
            await broker.research_prefetch(ticker)
        await broker.financials()
        model = CapturingModel()
        agents = Agents(model, broker, self.store, "prefetch", self.config)
        workflow = Workflow(agents, broker, self.config)
        result = await workflow.writer({"request": writer_request, "plan": PLAN,
            "risk_diff": {"evidence_ids": list(self.store.evidence("prefetch"))}})
        constraints = model.prompt["constraints"]
        self.assertIn("every clause", constraints)
        self.assertIn("each company and fiscal period", constraints)
        self.assertIn("support for each company", constraints)
        self.assertIn("support for each period", constraints)
        self.assertIn("potential risk and an established outcome", constraints)
        self.assertEqual(result["writer_mode"], "risk_model_with_deterministic_financials")


if __name__ == "__main__":
    unittest.main()
