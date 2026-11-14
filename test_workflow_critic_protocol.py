"""Offline critic transport/provenance tests, not semantic approval evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from workflow_agents import Agents, OpenRouterModel, load_config
from workflow_contracts import WorkflowError, canonical, digest
from workflow_critic_protocol import critic_protocol, canonical_selection, API_UNSUPPORTED_BOUNDS
from workflow_store import RunStore
from workflow_support import selection_issues, resolve_selection, validate_support
from workflow_tools import ToolBroker


REQUEST = {"question": "Compare operating margins and competition risks.", "tickers": ["GOOGL"],
           "years": [2025], "include_risks": True}
PARAGRAPH = {"text": "The company faces competitive pressure.", "evidence_ids": ["p_original"]}
RECORDS = {"p_original": {"kind": "passage", "ticker": "GOOGL", "year": 2025, "item": "1A",
    "source_sha256": "a" * 64, "source_url": "https://www.sec.gov/Archives/edgar/data/0/synthetic.htm",
    "passage": {"passage_id": "p_original", "ticker": "GOOGL", "fiscal_year": 2025, "item": "1A", "kind": "text",
                "text": "The company faces competitive pressure and customers may choose competing services.",
                "source_sha256": "a" * 64, "source_url": "https://www.sec.gov/Archives/edgar/data/0/synthetic.htm"}}}


def verdict(refs=("P1R1",), *, supported=True):
    return {"sentences": [{"sentence_index": 0, "supported": supported, "reference_ids": list(refs),
                          "reason": "Synthetic verdict for protocol testing only."}],
            "issues": [] if supported else ["Synthetic unsupported sentence."], "approved": supported}


class CriticProtocolTests(unittest.TestCase):
    def test_short_aliases_preserve_exact_canonical_receipts(self):
        protocol = critic_protocol(0, PARAGRAPH, RECORDS)
        self.assertEqual(list(protocol["aliases"]), ["P1R1"])
        selected = protocol["selection_type"].model_validate(verdict()).model_dump()
        canonical_result = canonical_selection(selected, protocol, PARAGRAPH, RECORDS)
        self.assertEqual(canonical_result["sentences"][0]["reference_ids"], list(protocol["catalog"]["references"]))
        self.assertEqual(selection_issues(canonical_result, protocol["catalog"], PARAGRAPH, RECORDS), [])
        receipt = resolve_selection(canonical_result, protocol["catalog"], PARAGRAPH, RECORDS)["sentences"][0]["references"][0]
        self.assertEqual(receipt, {"evidence_id": "p_original", "quote": RECORDS["p_original"]["passage"]["text"]})

    def test_foreign_unknown_and_storage_ids_are_not_selectable(self):
        protocol = critic_protocol(0, PARAGRAPH, RECORDS)
        for wrong in ("P2R1", "P1R999", "p_original", next(iter(protocol["catalog"]["references"]))):
            with self.subTest(wrong=wrong), self.assertRaises(ValidationError):
                protocol["selection_type"].model_validate(verdict([wrong]))

    def test_missing_aggregate_fields_are_rejected(self):
        protocol = critic_protocol(0, PARAGRAPH, RECORDS)
        for field in ("issues", "approved"):
            incomplete = verdict()
            del incomplete[field]
            with self.assertRaises(ValidationError):
                protocol["selection_type"].model_validate(incomplete)

    def test_existing_duplicate_and_sentence_coverage_checks_remain_active(self):
        protocol = critic_protocol(0, PARAGRAPH, RECORDS)
        for selected in (verdict(["P1R1", "P1R1"]), verdict([]),
                         {**verdict(), "sentences": [{**verdict()["sentences"][0], "sentence_index": 1}]}):
            checked = protocol["selection_type"].model_validate(selected).model_dump()
            self.assertTrue(selection_issues(canonical_selection(checked, protocol, PARAGRAPH, RECORDS),
                                            protocol["catalog"], PARAGRAPH, RECORDS))

    def test_changed_alias_view_paragraph_or_source_fails_closed(self):
        protocol = critic_protocol(0, PARAGRAPH, RECORDS)
        cases = []
        altered = copy.deepcopy(protocol)
        altered["aliases"]["P1R1"] = "r_forged"
        cases.append((altered, PARAGRAPH, RECORDS))
        altered = copy.deepcopy(protocol)
        altered["view"]["references"]["P1R1"]["quote"] = "Forged evidence."
        cases.append((altered, PARAGRAPH, RECORDS))
        cases.append((protocol, {**PARAGRAPH, "text": "The company has no competition."}, RECORDS))
        records = copy.deepcopy(RECORDS)
        records["p_original"]["passage"]["text"] += " Changed."
        cases.append((protocol, PARAGRAPH, records))
        for protocol_value, paragraph, records in cases:
            with self.assertRaises(WorkflowError) as error:
                canonical_selection(verdict(), protocol_value, paragraph, records)
            self.assertEqual(error.exception.code, "changed_support_catalog")

    def test_empty_catalog_permits_explicit_unsupported_verdict_without_empty_enum(self):
        records = copy.deepcopy(RECORDS)
        records["p_original"]["passage"]["text"] = ""
        protocol = critic_protocol(0, PARAGRAPH, records)
        self.assertEqual(protocol["aliases"], {})
        self.assertNotIn('"enum":[]', canonical(protocol["response_format"]))
        result = protocol["selection_type"].model_validate(verdict([], supported=False)).model_dump()
        self.assertEqual(canonical_selection(result, protocol, PARAGRAPH, records)["sentences"][0]["reference_ids"], [])
        with self.assertRaises(ValidationError):
            protocol["selection_type"].model_validate(verdict())

    def test_strict_schema_has_required_closed_objects_and_exact_tool_scope(self):
        protocol = critic_protocol(0, PARAGRAPH, RECORDS)
        schema = protocol["response_format"]["json_schema"]["schema"]
        def check(node):
            if isinstance(node, dict):
                self.assertFalse(set(node) & API_UNSUPPORTED_BOUNDS)
                if node.get("type") == "object":
                    self.assertIs(node["additionalProperties"], False)
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                for value in node.values():
                    check(value)
            elif isinstance(node, list):
                for value in node:
                    check(value)
        check(schema)
        self.assertEqual(schema["properties"]["tool"]["enum"], ["finish", "get_passage"])
        passage_schema = schema["properties"]["arguments"]["anyOf"][1]
        self.assertEqual(passage_schema["properties"]["passage_id"]["enum"], ["p_original"])

    def test_portable_api_schema_keeps_full_local_bounds(self):
        protocol = critic_protocol(0, PARAGRAPH, RECORDS)
        too_many = verdict(["P1R1"] * 9)
        empty_reason = verdict()
        empty_reason["sentences"][0]["reason"] = ""
        for invalid in (too_many, empty_reason):
            with self.assertRaises(ValidationError):
                protocol["selection_type"].model_validate(invalid)
        self.assertIn("maxItems", canonical(protocol["selection_type"].model_json_schema()))

    def test_unsupported_semantics_are_preserved_and_forged_approval_still_fails(self):
        protocol = critic_protocol(0, PARAGRAPH, RECORDS)
        selection = verdict([], supported=False)
        converted = canonical_selection(selection, protocol, PARAGRAPH, RECORDS)
        self.assertEqual(converted, selection)
        converted["approved"] = True
        review = resolve_selection(converted, protocol["catalog"], PARAGRAPH, RECORDS)
        self.assertTrue(validate_support(review, PARAGRAPH, RECORDS))


class ScriptedModel:
    def __init__(self, selection=None):
        self.selection = selection or verdict()
        self.calls = []
    async def complete(self, messages, config):
        self.calls.append((copy.deepcopy(messages), copy.deepcopy(config)))
        return {"content": canonical({"tool": "finish", "arguments": {}, "final": self.selection}),
                "usage": {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40, "cost": 0},
                "model": "scripted-test-only"}


class CriticTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = RunStore(Path(self.tmp.name) / "journal.sqlite3")
        self.config = load_config()
        self.store.start("critic", self.config)
        self.store.save_evidence("critic", "p_original", RECORDS["p_original"])
        self.broker = ToolBroker(None, self.store, "critic", REQUEST)
        self.protocol = critic_protocol(0, PARAGRAPH, RECORDS)

    async def ask(self, model, response_format=None, role="critic"):
        return await Agents(model, self.broker, self.store, "critic", self.config).ask(role,
            {"reference_catalog": self.protocol["view"]}, self.protocol["selection_type"],
            response_format=response_format or self.protocol["response_format"],
            validate_final=lambda result: selection_issues(canonical_selection(result, self.protocol, PARAGRAPH, RECORDS),
                self.protocol["catalog"], PARAGRAPH, RECORDS))

    async def test_schema_is_in_cache_identity_reservations_events_and_transport(self):
        model = ScriptedModel()
        original_config = copy.deepcopy(self.config)
        first = self.protocol["response_format"]
        second = copy.deepcopy(first)
        second["json_schema"]["name"] = "critic_action_alternate"
        await self.ask(model, first)
        await self.ask(model, second)
        await self.ask(model, second)
        self.assertEqual(len(model.calls), 2)
        report = self.store.report("critic")
        starts = [e["details"] for e in report["events"] if e["kind"] == "llm_start"]
        self.assertNotEqual(starts[0]["response_format_sha256"], starts[1]["response_format_sha256"])
        for call, start in zip(model.calls, starts):
            messages, config = call
            schema_bytes = len(canonical(config["_response_format"]).encode())
            self.assertEqual(start["response_format_bytes"], schema_bytes)
            self.assertEqual(start["reserved_tokens"], len(canonical(messages).encode()) + schema_bytes + self.config["max_output_tokens"] + 2048)
        self.assertEqual(report["metrics"]["model_response_cache_hits"], 1)
        self.assertEqual(self.config, original_config)

    async def test_graph_records_reconstructable_original_alias_selections(self):
        from langgraph.types import Command
        from research_workflow import open_workflow
        from run_research_workflow import pending_gate
        from test_research_workflow import FixtureBackend, FixtureModel, REQUEST as graph_request
        backend = FixtureBackend(self.tmp.name)
        async with open_workflow(Path(self.tmp.name) / "graph", "graph_aliases", graph_request,
                                 self.config, FixtureModel(), backend) as (workflow, graph, options):
            await graph.ainvoke({"request": graph_request, "researchers": {}, "revisions": 0, "replans": 0}, options)
            gate = pending_gate(await graph.aget_state(options))
            await graph.ainvoke(Command(resume={"action": "approve", "expected_hash": gate["expected_hash"],
                                               "replacement": None}), options)
            state = (await graph.aget_state(options)).values
            records = workflow.broker.store.evidence("graph_aliases")
            for index, (paragraph, review) in enumerate(zip(state["draft"]["paragraphs"], state["critique"]["paragraph_reviews"])):
                protocol = critic_protocol(index, paragraph, records)
                self.assertEqual(review["selection"], canonical_selection(review["alias_selection"], protocol, paragraph, records))
                self.assertEqual(review["alias_binding_sha256"], protocol["binding_sha256"])
                self.assertEqual(review["response_format_sha256"], digest(protocol["response_format"]))

    async def test_three_invalid_responses_still_stop_with_all_costs_recorded(self):
        model = ScriptedModel(verdict(["p_original"]))
        with self.assertRaises(WorkflowError) as error:
            await self.ask(model)
        self.assertEqual(error.exception.code, "agent_response_limit")
        self.assertEqual(len(model.calls), 3)
        report = self.store.report("critic")
        self.assertEqual(report["metrics"]["llm_requests_started"], 3)
        self.assertEqual(report["metrics"]["llm_responses_received"], 3)
        self.assertEqual(report["budget"]["token_limit"], 900000)
        self.assertEqual(sum(e["kind"] == "agent_correction" for e in report["events"]), 3)

    async def test_noncritic_schema_is_rejected_before_call(self):
        model = ScriptedModel()
        with self.assertRaises(WorkflowError) as error:
            await self.ask(model, role="writer")
        self.assertEqual(error.exception.code, "invalid_response_format")
        self.assertEqual(model.calls, [])

    async def test_schema_bytes_count_toward_context_limit_before_call(self):
        model = ScriptedModel()
        self.config = {**self.config, "max_context_bytes": 1}
        with self.assertRaises(WorkflowError) as error:
            await self.ask(model)
        self.assertEqual(error.exception.code, "context_limit")
        self.assertEqual(model.calls, [])

    async def provider_call(self, response, *, status=200):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(status, json=response)
        original_client = httpx.AsyncClient
        def client(**kwargs):
            return original_client(transport=httpx.MockTransport(handler), **kwargs)
        config = {**self.config, "_response_format": self.protocol["response_format"]}
        with patch("workflow_agents.httpx.AsyncClient", side_effect=client):
            try:
                result = await OpenRouterModel("synthetic-secret-must-not-be-exposed").complete([], config)
                return result, requests
            except WorkflowError as error:
                return error, requests

    async def test_provider_receives_strict_schema_without_fallback(self):
        result, requests = await self.provider_call({"model": self.config["model"],
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3, "cost": 0}})
        self.assertEqual(result["content"], "{}")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["response_format"], self.protocol["response_format"])
        self.assertEqual(requests[0]["provider"], {"allow_fallbacks": False, "require_parameters": True})

    async def test_http_schema_rejection_is_safe_and_not_retried(self):
        error, requests = await self.provider_call({"error": {"code": "invalid_json_schema",
            "message": "synthetic-secret-must-not-be-exposed"}}, status=400)
        self.assertIsInstance(error, WorkflowError)
        self.assertEqual(error.code, "model_http_error")
        self.assertEqual(error.http_status, 400)
        self.assertEqual(error.provider_error_code, "invalid_json_schema")
        self.assertNotIn("synthetic-secret", str(error))
        self.assertEqual(len(requests), 1)

    async def test_unknown_provider_code_and_truncated_output_fail_closed(self):
        error, requests = await self.provider_call({"error": {"code": "synthetic-secret-must-not-be-exposed"}}, status=400)
        self.assertIsNone(error.provider_error_code)
        self.assertNotIn("synthetic-secret", str(error))
        error, requests = await self.provider_call({"model": self.config["model"],
            "choices": [{"finish_reason": "length", "message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}})
        self.assertEqual(error.code, "model_unavailable")
        self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()
