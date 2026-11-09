"""Offline API tests: real lookup/guardrail, temporary invented SEC files.

Only the model is normally mocked. These tests use no API credits or network,
and do not assert that the invented amounts are real company financial data.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from openai import OpenAI, APIConnectionError

with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-placeholder"}), patch("openai.OpenAI"):
    import main

from company_registry import COMPANIES
from financial_data import get_annual_financial_fact
from output_guardrails import FinancialClaim, GuardrailError, check_and_render
from request_guardrails import requested_fiscal_years
from financial_metrics import METRICS


def args(ticker="MSFT", metric="net_income", year=2025, as_of=None):
    return {"ticker": ticker, "metric": metric, "year": year, "as_of": as_of}


def fake_response(content=None, tool_calls=None, finish_reason="stop", refusal=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=tool_calls, refusal=refusal),
        finish_reason=finish_reason,
    )])


def fake_tool_call(arguments, index=1):
    payload = {"id": f"call_{index}", "type": "function", "function": {
        "name": main.TOOL_NAME, "arguments": json.dumps(arguments),
    }}
    call = Mock()
    call.id, call.type = payload["id"], payload["type"]
    call.function = SimpleNamespace(**payload["function"])
    call.model_dump.return_value = payload
    return call


def make_claim(result, evidence_id="e1"):
    return {"evidence_id": evidence_id, **{
        key: result[key] for key in FinancialClaim.model_fields if key != "evidence_id"
    }}


def draft(kind="financial", claims=None):
    return json.dumps({"kind": kind, "claims": [] if claims is None else claims})


def write_fixture(folder, ticker):
    """Three years; intentionally different calendars and invented amounts."""
    cik = COMPANIES[ticker]["cik"]
    tags = {"RevenueFromContractWithCustomerExcludingAssessedTax": [],
            "OperatingIncomeLoss": [], "NetIncomeLoss": []}
    filings = []
    for year in (2023, 2024, 2025):
        if ticker == "MSFT":
            start, end, filed = f"{year-1}-07-01", f"{year}-06-30", f"{year}-08-01"
            values = [1000, 400, 250]
        else:
            start, end, filed = f"{year}-01-01", f"{year}-12-31", f"{year+1}-02-01"
            values = [2000, 500, 300]
        accn = f"{cik}-{year % 100:02d}-000001"
        filings.append({"accessionNumber": accn, "filingDate": filed, "reportDate": end,
                        "form": "10-K", "primaryDocument": "annual.htm"})
        for tag, value in zip(tags, values):
            tags[tag].append({"start": start, "end": end, "val": value + year - 2025,
                              "accn": accn, "filed": filed, "form": "10-K", "fy": year, "fp": "FY"})
    facts = {"cik": int(cik), "entityName": COMPANIES[ticker]["name"], "facts": {"us-gaap": {
        tag: {"units": {"USD": rows}} for tag, rows in tags.items()
    }}}
    history = {"cik": cik, "complete": True, "annual_filings": filings}
    (folder / f"{ticker}_companyfacts.json").write_text(json.dumps(facts), encoding="utf-8")
    (folder / f"{ticker}_filings.json").write_text(json.dumps(history), encoding="utf-8")


class AskEndpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        for ticker in ("MSFT", "GOOGL"):
            write_fixture(self.folder, ticker)
        for target, value in (("main.DATA_DIR", self.folder), ("main.client", Mock())):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("main.get_annual_financial_fact", wraps=get_annual_financial_fact)
        self.lookup = p.start()
        self.addCleanup(p.stop)
        self.ai = main.client.chat.completions.create
        self.http = TestClient(main.app)
        self.addCleanup(self.http.close)

    def result(self, arguments=None):
        return get_annual_financial_fact(**(arguments or args()), data_dir=self.folder)

    def prepare(self, requests=None, content=None):
        requests = requests or [args()]
        results = [self.result(a) for a in requests]
        if content is None:
            content = draft(claims=[make_claim(r, f"e{i}") for i, r in enumerate(results, 1) if r["status"] == "ok"])
        self.ai.side_effect = [fake_response(tool_calls=[fake_tool_call(a, i) for i, a in enumerate(requests, 1)]),
                               fake_response(content=content)]
        return results

    def ask(self, question="What was Microsoft's annual net income for FY2025?"):
        return self.http.post("/ask", json={"question": question})

    def assert_blocked(self, response):
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"]["code"], "output_guardrail_blocked")
        self.assertNotIn("answer", response.json())

    def change_facts(self, mutate):
        path = self.folder / "MSFT_companyfacts.json"
        document = json.loads(path.read_text())
        mutate(document["facts"]["us-gaap"])
        path.write_text(json.dumps(document))

    def test_health(self):
        self.assertEqual(self.http.get("/health").json(), {"status": "ok"})
        self.ai.assert_not_called()

    def test_blank_or_oversized_question_does_not_call_ai(self):
        for question in ("", "  ", "x" * 2001):
            self.assertEqual(self.ask(question).status_code, 422)
        self.ai.assert_not_called()

    def test_microsoft_lookup_preserves_evidence_and_link(self):
        result = self.prepare()[0]
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["evidence"][0]["result"], result)
        self.lookup.assert_called_once_with(**args(), data_dir=self.folder)
        self.assertIn("USD 250", body["answer"])
        self.assertIn("2024-07-01 to 2025-06-30", body["answer"])
        self.assertIn(result["sources"][0]["url"], body["answer"])
        self.assertEqual(body["guardrail"], {"status": "passed", "checked_claims": 1})
        self.assertEqual(self.ai.call_count, 2)
        self.assertEqual([c.kwargs["tool_choice"] for c in self.ai.call_args_list], ["auto", "none"])

    def test_google_alias_revenue_uses_calendar_year(self):
        self.prepare([args("Google", "revenue")])
        response = self.ask("What was Google's annual revenue in 2025?")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["evidence"][0]["arguments"]["ticker"], "GOOGL")
        self.assertIn("USD 2,000", body["answer"])
        self.assertIn("2025-01-01 to 2025-12-31", body["answer"])

    def test_three_measures_keep_their_own_evidence(self):
        self.prepare([args(metric=m) for m in ("revenue", "operating_income", "net_income")])
        response = self.ask("Show Microsoft's revenue, operating income and net income for FY2025.")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["guardrail"]["checked_claims"], 3)
        for amount in ("USD 1,000", "USD 400", "USD 250"):
            self.assertIn(amount, response.json()["answer"])

    def test_two_companies_three_years_fit_six_lookup_limit(self):
        requests = [args(t, "revenue", y) for t in ("MSFT", "GOOGL") for y in (2023, 2024, 2025)]
        self.prepare(requests)
        response = self.ask("Show Microsoft and Google revenue for FY2023, FY2024 and FY2025.")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["guardrail"]["checked_claims"], 6)
        self.assertEqual(self.lookup.call_count, 6)
        self.assertEqual(self.ai.call_count, 2)

    def test_missing_future_year_is_rendered_without_second_ai_request(self):
        result = self.prepare([args(year=2099)])[0]
        response = self.ask("Microsoft annual net income for FY2099?")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(result["status"], "not_found")
        self.assertNotIn("USD", response.json()["answer"])
        self.assertEqual(response.json()["guardrail"]["checked_claims"], 0)
        self.assertEqual(response.json()["evidence"][0]["result"], result)
        self.assertEqual(self.ai.call_count, 1)

    def test_missing_metric_is_distinct_from_missing_cache(self):
        self.change_facts(lambda tags: tags.pop("OperatingIncomeLoss"))
        self.prepare([args(metric="operating_income")])
        response = self.ask("Microsoft operating income for FY2025?")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["evidence"][0]["result"]["code"], "metric_not_found")

    def test_mixed_found_and_missing_results_are_both_reported(self):
        self.prepare([args(), args("GOOGL", "net_income", 2099)])
        response = self.ask("Show Microsoft net income for FY2025 and Google net income for FY2099.")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["guardrail"]["checked_claims"], 1)
        self.assertIn("USD 250", response.json()["answer"])
        self.assertIn("no supported matching", response.json()["answer"])
        self.assertEqual(self.ai.call_count, 2)

    def test_conflicting_values_report_no_figure(self):
        def conflict(tags):
            rows = tags["NetIncomeLoss"]["units"]["USD"]
            rows.append({**rows[-1], "val": 999})
        self.change_facts(conflict)
        self.prepare()
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("conflicting", response.json()["answer"])
        self.assertNotIn("USD", response.json()["answer"])
        self.assertEqual(self.ai.call_count, 1)

    def test_unsupported_period_reports_no_figure(self):
        def shorten(tags):
            for concept in tags.values():
                concept["units"]["USD"][-1]["start"] = "2025-01-01"
        self.change_facts(shorten)
        self.prepare()
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["evidence"][0]["result"]["status"], "unsupported_period")
        self.assertNotIn("USD", response.json()["answer"])
        self.assertEqual(self.ai.call_count, 1)

    def test_as_of_cutoff_is_preserved_and_applied(self):
        for cutoff, status in (("2025-07-31", "not_found"), ("2025-08-01", "ok")):
            with self.subTest(cutoff=cutoff):
                self.prepare([args(as_of=cutoff)])
                response = self.ask(f"Microsoft net income for FY2025 using filings on or before {cutoff}?")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["evidence"][0]["result"]["status"], status)
                self.assertIn(cutoff, response.json()["answer"])

    def test_missing_cache_is_503_and_no_second_model_request(self):
        (self.folder / "GOOGL_filings.json").unlink()
        self.ai.return_value = fake_response(tool_calls=[fake_tool_call(args("GOOGL"))])
        response = self.ask()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "financial_data_unavailable")
        self.assertEqual(response.json()["detail"]["ticker"], "GOOGL")
        self.assertEqual(self.ai.call_count, 1)
        self.assertNotIn("answer", response.json())

    def test_corrupt_cache_is_503_not_not_found(self):
        (self.folder / "MSFT_companyfacts.json").write_text("broken json")
        self.ai.return_value = fake_response(tool_calls=[fake_tool_call(args())])
        self.assertEqual(self.ask().status_code, 503)
        self.assertEqual(self.ai.call_count, 1)

    def test_lookup_exception_has_controlled_message(self):
        self.lookup.side_effect = OSError("private-path-and-secret")
        self.ai.return_value = fake_response(tool_calls=[fake_tool_call(args())])
        response = self.ask()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private-path-and-secret", response.text)

    def test_clarifications_and_definitions_do_not_need_lookup(self):
        for kind in ("ask_year", "ask_company", "ask_metric", "clarify_request", "define_net_income",
                     "define_operating_income", "define_revenue", "define_profit", "compare_revenue_profit", "unsupported"):
            with self.subTest(kind=kind):
                self.ai.return_value = fake_response(content=draft(kind))
                response = self.ask()
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["evidence"], [])
                self.assertEqual(response.json()["guardrail"]["status"], "not_applicable")
        self.lookup.assert_not_called()

    def test_missing_year_overrides_guessed_2023_and_2025_before_lookup(self):
        # Reproduces both live failures: the model ignores the clarification rule.
        for guessed_year in (2023, 2025):
            with self.subTest(guessed_year=guessed_year):
                self.ai.reset_mock()
                self.prepare([args("GOOGL", "revenue", guessed_year)])
                response = self.ask("What was Google's annual revenue?")
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertIn("Which fiscal year", body["answer"])
                self.assertEqual(body["evidence"], [])
                self.assertEqual(body["guardrail"], {"status": "not_applicable", "checked_claims": 0})
                self.assertNotIn("USD", body["answer"])
                self.lookup.assert_not_called()
                self.ai.assert_called_once()

    def test_missing_year_stops_the_entire_batch_before_lookup(self):
        self.ai.return_value = fake_response(tool_calls=[
            fake_tool_call(args(), 1), fake_tool_call(args("GOOGL"), 2)
        ])
        response = self.ask("Show Microsoft and Google annual net income.")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["evidence"], [])
        self.lookup.assert_not_called()
        self.ai.assert_called_once()

    def test_relative_years_and_cutoff_only_require_a_fiscal_year(self):
        questions = (
            "What was Google's latest annual revenue?",
            "What was Google's annual revenue last year?",
            "Google annual revenue using filings on or before 2025-12-31?",
            "Google annual revenue as of December 31, 2025?",
            "Google annual revenue for FY2023-25?",
            "Google annual revenue for 2025 through 2023?",
        )
        for question in questions:
            with self.subTest(question=question):
                self.ai.return_value = fake_response(tool_calls=[fake_tool_call(args("GOOGL", "revenue"))])
                response = self.ask(question)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("Which fiscal year", response.json()["answer"])
                self.assertEqual(response.json()["evidence"], [])
        self.lookup.assert_not_called()

    def test_unrequested_year_blocks_every_call_including_valid_first_call(self):
        self.ai.return_value = fake_response(tool_calls=[
            fake_tool_call(args(year=2024), 1), fake_tool_call(args("GOOGL", year=2025), 2)
        ])
        response = self.ask("Show Microsoft and Google annual net income for FY2024.")
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"]["code"], "request_guardrail_blocked")
        self.assertNotIn("answer", response.json())
        self.lookup.assert_not_called()
        self.ai.assert_called_once()

    def test_cutoff_year_cannot_replace_the_requested_fiscal_year(self):
        self.ai.return_value = fake_response(tool_calls=[fake_tool_call(args(year=2025, as_of="2025-12-31"))])
        response = self.ask("Microsoft net income for FY2024 using filings on or before 2025-12-31?")
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"]["code"], "request_guardrail_blocked")
        self.lookup.assert_not_called()

    def test_explicit_year_notations_still_allow_lookup(self):
        for expression in ("2025", "FY2025", "FY 2025", "fiscal year 2025"):
            with self.subTest(expression=expression):
                self.prepare([args("GOOGL", "revenue")])
                response = self.ask(f"What was Google's annual revenue in {expression}?")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["guardrail"]["checked_claims"], 1)

    def test_explicit_year_range_allows_the_middle_year(self):
        self.prepare([args("GOOGL", "revenue", year) for year in (2023, 2024, 2025)])
        response = self.ask("Show Google's annual revenue for 2023 through 2025.")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["guardrail"]["checked_claims"], 3)

    def test_definition_without_year_still_returns_definition(self):
        self.ai.return_value = fake_response(content=draft("define_revenue"))
        response = self.ask("What is revenue?")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Revenue is", response.json()["answer"])
        self.assertEqual(response.json()["evidence"], [])
        self.lookup.assert_not_called()

    def test_invalid_arguments_rejected_before_any_lookup(self):
        changes = [{"year": True}, {"year": "2025"}, {"year": 1800}, {"year": 2101},
                   {"ticker": "ZZZZ"}, {"metric": "margin"}, {"as_of": "2025-02-30"},
                   {"as_of": "2025-2-1"}, {"extra": "ignored?"}]
        for change in changes:
            with self.subTest(change=change):
                self.ai.return_value = fake_response(tool_calls=[fake_tool_call(args(), 1), fake_tool_call({**args(), **change}, 2)])
                self.assertEqual(self.ask().status_code, 502)
                self.lookup.assert_not_called()

    def test_missing_argument_or_bad_json_is_rejected(self):
        for bad_json in ('{"ticker":"MSFT"}', "not JSON"):
            call = fake_tool_call(args())
            call.function.arguments = bad_json
            self.ai.return_value = fake_response(tool_calls=[call])
            self.assertEqual(self.ask().status_code, 502)
        self.lookup.assert_not_called()

    def test_duplicate_calls_are_rejected_before_lookup(self):
        for calls in ([fake_tool_call(args(), 1), fake_tool_call(args(), 2)],
                      [fake_tool_call(args(), 1), fake_tool_call(args("GOOGL"), 1)]):
            self.ai.return_value = fake_response(tool_calls=calls)
            self.assertEqual(self.ask().status_code, 502)
        self.lookup.assert_not_called()

    def test_seven_calls_are_rejected_before_lookup(self):
        self.ai.return_value = fake_response(tool_calls=[fake_tool_call(args(year=2020+i), i) for i in range(7)])
        self.assertEqual(self.ask().status_code, 422)
        self.lookup.assert_not_called()

    def test_unknown_tool_is_rejected(self):
        call = fake_tool_call(args())
        call.function.name = "run_python"
        self.ai.return_value = fake_response(tool_calls=[call])
        self.assertEqual(self.ask().status_code, 502)
        self.lookup.assert_not_called()

    def test_second_tool_round_is_rejected(self):
        self.ai.side_effect = [fake_response(tool_calls=[fake_tool_call(args())]),
                               fake_response(tool_calls=[fake_tool_call(args("GOOGL"))])]
        self.assertEqual(self.ask().status_code, 502)
        self.assertEqual(self.lookup.call_count, 1)
        self.assertEqual(self.ai.call_count, 2)

    def test_provider_error_is_controlled(self):
        self.ai.side_effect = APIConnectionError(message="private-provider-message", request=httpx.Request("POST", "https://example.invalid"))
        response = self.ask()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-provider-message", response.text)

    def test_truncation_refusal_and_empty_response_are_rejected(self):
        responses = [fake_response(content=draft("ask_year"), finish_reason="length"),
                     fake_response(content=draft("ask_year"), finish_reason="content_filter"),
                     fake_response(refusal="Cannot answer"), fake_response(content=""), SimpleNamespace(choices=[])]
        for response in responses:
            self.ai.return_value = response
            self.assertEqual(self.ask().status_code, 502)
        self.lookup.assert_not_called()

    def test_changed_claim_fields_are_blocked(self):
        original = self.result()
        mutations = {"ticker": "GOOGL", "company": "Another company", "cik": "0001652044",
                     "metric": "revenue", "concept": "Revenues", "fiscal_year": 2024,
                     "period_start": "2025-01-01", "period_end": "2025-12-31",
                     "unit": "EUR", "value": 999, "evidence_id": "e999"}
        for field, value in mutations.items():
            with self.subTest(field=field):
                claim = {**make_claim(original), field: value}
                self.prepare(content=draft(claims=[claim]))
                self.assert_blocked(self.ask())

    def test_claim_without_lookup_is_blocked(self):
        self.ai.return_value = fake_response(content=draft(claims=[make_claim(self.result())]))
        self.assert_blocked(self.ask())
        self.lookup.assert_not_called()

    def test_free_text_extra_prose_and_noninteger_claims_are_blocked(self):
        for content in ("Google revenue was USD 999.", json.dumps({"kind": "ask_year", "claims": [], "answer": "999"}),
                        draft("ask_year", [make_claim(self.result())])):
            self.ai.return_value = fake_response(content=content)
            self.assert_blocked(self.ask())
        for value in (True, 250.0, "250"):
            self.prepare(content=draft(claims=[{**make_claim(self.result()), "value": value}]))
            self.assert_blocked(self.ask())

    def test_successful_result_cannot_be_omitted_or_duplicated(self):
        claim = make_claim(self.result())
        for content in (draft(), draft(claims=[claim, claim]), draft("ask_year")):
            self.prepare(content=content)
            self.assert_blocked(self.ask())

    def test_claim_for_missing_result_is_blocked(self):
        # A mixed batch still uses a model draft. A missing item cannot become
        # a claim just because another requested result was found.
        result = self.result()
        self.prepare([args(), args(year=2099)], content=draft(claims=[
            make_claim(result, "e1"), make_claim(result, "e2"),
        ]))
        self.assert_blocked(self.ask("Microsoft net income for FY2025 and FY2099?"))

    def test_cross_company_evidence_swap_is_blocked(self):
        msft, google = self.result(), self.result(args("GOOGL"))
        self.prepare([args(), args("GOOGL")], content=draft(claims=[make_claim(google, "e1"), make_claim(msft, "e2")]))
        self.assert_blocked(self.ask())

    def test_tampered_tool_fields_are_blocked_even_if_ai_copies_them(self):
        original = self.result()
        changes = {"value": 999, "value_boolean": True, "company": "Other", "ticker": "GOOGL",
                   "metric": "revenue", "cik": "0001652044", "concept": "Revenues",
                   "period_start": "2024-07-02", "period_end": "2025-06-29",
                   "filed": "2025-08-02", "snapshot_sha256": "a" * 64,
                   "filings_snapshot_sha256": "b" * 64}
        for field, value in changes.items():
            with self.subTest(field=field):
                result = {**original, ("value" if field == "value_boolean" else field): value}
                self.lookup.return_value = result
                self.prepare(content=draft(claims=[make_claim(result)]))
                self.assert_blocked(self.ask())

    def test_incomplete_evidence_and_altered_source_url_are_blocked(self):
        original = self.result()
        for field in ("filed", "filing_identifiers", "sources", "period_sources", "value", "snapshot_sha256"):
            with self.subTest(field=field):
                result = deepcopy(original)
                del result[field]
                self.lookup.return_value = result
                self.prepare(content=draft(claims=[make_claim(original)]))
                self.assert_blocked(self.ask())
        result = deepcopy(original)
        result["sources"][0]["url"] = "https://example.invalid/fake"
        self.lookup.return_value = result
        self.prepare(content=draft(claims=[make_claim(original)]))
        self.assert_blocked(self.ask())

    def test_fabricated_not_found_status_is_blocked(self):
        self.lookup.return_value = {"status": "not_found"}
        self.prepare(content=draft())
        self.assert_blocked(self.ask())

    def test_cache_change_during_model_response_is_blocked(self):
        result = self.result()
        calls = [fake_tool_call(args())]
        def model_reply(**kwargs):
            if kwargs["tool_choice"] == "auto":
                return fake_response(tool_calls=calls)
            path = self.folder / "MSFT_companyfacts.json"
            path.write_text(path.read_text() + "\n")  # Same values, different snapshot bytes.
            return fake_response(content=draft(claims=[make_claim(result)]))
        self.ai.side_effect = model_reply
        self.assert_blocked(self.ask())

    def test_invalid_evidence_envelope_is_blocked(self):
        result = self.result()
        item = {"id": "e1", "tool": main.TOOL_NAME, "arguments": args(), "result": result}
        malformed = [{**item, "id": "e999"}, {**item, "tool": "other"},
                     {**item, "arguments": args("GOOGL")}, {**item, "extra": "text"}]
        for bad in malformed:
            with self.assertRaises(GuardrailError):
                check_and_render(draft(claims=[make_claim(result)]), [bad], data_dir=self.folder)

    def test_tool_schema_requires_all_arguments_and_forbids_extras(self):
        schema = main.TOOLS[0]["function"]["parameters"]
        self.assertEqual(set(schema["required"]), {"ticker", "metric", "year", "as_of"})
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]["ticker"]["enum"]), set(COMPANIES))

    def test_openai_sdk_serializes_and_parses_two_requests_without_network(self):
        result = self.result()
        requests = []
        def transport(request):
            self.assertEqual(str(request.url), "https://openrouter.ai/api/v1/chat/completions")
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                message = {"role": "assistant", "content": None, "tool_calls": [
                    fake_tool_call(args()).model_dump()
                ]}
                finish_reason = "tool_calls"
            else:
                message = {"role": "assistant", "content": draft(claims=[make_claim(result)])}
                finish_reason = "stop"
            return httpx.Response(200, json={
                "id": "offline-completion", "object": "chat.completion", "created": 0,
                "model": main.MODEL,
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            })
        with OpenAI(api_key="offline-placeholder", base_url="https://openrouter.ai/api/v1",
                    http_client=httpx.Client(transport=httpx.MockTransport(transport)), max_retries=0) as sdk:
            with patch("main.client", sdk):
                response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["provider"], {"require_parameters": True})
        self.assertEqual(requests[0]["response_format"]["type"], "json_schema")
        self.assertEqual(requests[1]["tool_choice"], "none")


    def add_alternative_facts(self):
        def mutate(tags):
            tags["ProfitLoss"] = deepcopy(tags["NetIncomeLoss"])
            tags[METRICS["pretax_income"][0]] = deepcopy(tags["NetIncomeLoss"])
            for row in tags["ProfitLoss"]["units"]["USD"]:
                row["val"] += 20
            for row in tags[METRICS["pretax_income"][0]]["units"]["USD"]:
                row["val"] += 100
        self.change_facts(mutate)

    def test_explicit_consolidated_net_income_returns_distinct_label_and_evidence(self):
        self.add_alternative_facts()
        self.prepare([args(metric="net_income_consolidated")])
        response = self.ask("What was Microsoft's consolidated net income in FY2025?")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("including noncontrolling interests", body["answer"])
        self.assertIn("USD 270", body["answer"])
        self.assertEqual(body["evidence"][0]["result"]["concept"], "ProfitLoss")
        self.assertEqual(body["guardrail"]["checked_claims"], 1)

    def test_explicit_pretax_income_is_labelled_continuing_operations(self):
        self.add_alternative_facts()
        self.prepare([args(metric="pretax_income")])
        response = self.ask("What was Microsoft's GAAP pretax income in FY2025?")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("income from continuing operations before income taxes", response.json()["answer"])
        self.assertIn("USD 350", response.json()["answer"])

    def test_missing_parent_income_suggests_new_question_but_no_alternative_amount(self):
        self.add_alternative_facts()
        self.change_facts(lambda tags: tags.pop("NetIncomeLoss"))
        self.prepare()
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("consolidated net income", body["answer"])
        self.assertIn("not a substitute", body["answer"])
        self.assertNotIn("USD 270", body["answer"])
        self.assertEqual(body["guardrail"]["checked_claims"], 0)
        self.assertEqual(body["evidence"][0]["result"]["status"], "not_found")

    def test_missing_operating_income_suggests_pretax_with_original_cutoff(self):
        self.add_alternative_facts()
        self.change_facts(lambda tags: tags.pop("OperatingIncomeLoss"))
        self.prepare([args(metric="operating_income", as_of="2025-12-31")])
        response = self.ask("Microsoft operating income FY2025 using filings dated on or before 2025-12-31?")
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()["answer"]
        self.assertIn("before income taxes", answer)
        self.assertIn("not a substitute for GAAP operating income", answer)
        self.assertIn("fiscal year 2025, using filings dated on or before 2025-12-31?", answer)
        self.assertNotIn("USD 350", answer)

    def test_unrequested_alternative_is_blocked_before_any_lookup(self):
        for metric, question in (
            ("net_income_consolidated", "Microsoft net income FY2025?"),
            ("pretax_income", "Microsoft operating income FY2025?"),
        ):
            self.ai.side_effect = None
            self.ai.return_value = fake_response(tool_calls=[fake_tool_call(args(metric=metric))])
            response = self.ask(question)
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()["detail"]["code"], "request_guardrail_blocked")
            self.lookup.assert_not_called()

    def test_mixed_batch_with_unrequested_alternative_reads_no_data(self):
        self.ai.return_value = fake_response(tool_calls=[fake_tool_call(args(metric="revenue"), 1),
                                                        fake_tool_call(args(metric="pretax_income"), 2)])
        response = self.ask("Microsoft revenue and operating income FY2025?")
        self.assertEqual(response.status_code, 502)
        self.lookup.assert_not_called()

    def test_non_gaap_or_adjusted_request_cannot_be_served_with_gaap_facts(self):
        for question, metric in (("IBM non-GAAP operating income FY2025?", "operating_income"),
                                 ("Microsoft adjusted pretax income FY2025?", "pretax_income")):
            self.ai.return_value = fake_response(tool_calls=[fake_tool_call(args(metric=metric))])
            response = self.ask(question)
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()["detail"]["code"], "request_guardrail_blocked")
            self.lookup.assert_not_called()

    def test_alternative_suggestions_cannot_be_promoted_to_claims(self):
        self.add_alternative_facts()
        self.change_facts(lambda tags: tags.pop("NetIncomeLoss"))
        alternative = self.result(args(metric="net_income_consolidated"))
        google = self.result(args("GOOGL", "revenue"))
        self.prepare([args(), args("GOOGL", "revenue")], content=draft(claims=[
            make_claim(alternative, "e1"), make_claim(google, "e2"),
        ]))
        self.assert_blocked(self.ask("Microsoft parent net income and Google revenue FY2025?"))

    def test_changed_suggestion_definition_and_source_are_blocked(self):
        self.add_alternative_facts()
        self.change_facts(lambda tags: tags.pop("NetIncomeLoss"))
        result = self.result()
        for field, value in (("metric", "pretax_income"), ("label", "parent net income"),
                             ("definition", "Same as parent income"), ("sources", [])):
            changed = deepcopy(result)
            changed["available_alternatives"][0][field] = value
            self.prepare()
            self.lookup.side_effect = lambda *a, **k: changed
            self.assert_blocked(self.ask())

    def test_new_metric_definitions_need_no_financial_claim_or_lookup(self):
        for kind in ("define_net_income_consolidated", "define_pretax_income"):
            self.ai.return_value = fake_response(content=draft(kind))
            response = self.ask("What does this accounting measure mean?")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["guardrail"]["status"], "not_applicable")
            self.lookup.assert_not_called()

    def test_tool_schema_uses_all_five_distinct_metric_names(self):
        schema = main.TOOLS[0]["function"]["parameters"]
        self.assertEqual(set(schema["properties"]["metric"]["enum"]), set(METRICS))

    def test_missing_parent_income_does_not_depend_on_model_answer_kind(self):
        self.add_alternative_facts()
        self.change_facts(lambda tags: tags.pop("NetIncomeLoss"))
        # The live failure selected a nonfinancial kind after a missing lookup.
        # That second response must never be requested for a status-only batch.
        for kind in ("unsupported", "ask_metric", "clarify_request", "define_net_income"):
            with self.subTest(kind=kind):
                self.ai.reset_mock()
                self.prepare(content=draft(kind))
                response = self.ask()
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["guardrail"], {"status": "passed", "checked_claims": 0})
                self.assertIn("no supported matching", body["answer"])
                self.assertIn("consolidated net income", body["answer"])
                self.assertNotIn("USD", body["answer"])
                self.assertEqual(self.ai.call_count, 1)

    def test_missing_operating_income_needs_no_second_model_request(self):
        self.add_alternative_facts()
        self.change_facts(lambda tags: tags.pop("OperatingIncomeLoss"))
        self.ai.side_effect = [
            fake_response(tool_calls=[fake_tool_call(args(metric="operating_income"))]),
            AssertionError("No second model request is needed for a missing result"),
        ]
        response = self.ask("Microsoft operating income FY2025?")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("before income taxes", response.json()["answer"])
        self.assertNotIn("USD", response.json()["answer"])
        self.assertEqual(self.ai.call_count, 1)

    def test_all_missing_batch_preserves_each_request_and_suggestion(self):
        self.add_alternative_facts()
        self.change_facts(lambda tags: [tags.pop(tag) for tag in ("NetIncomeLoss", "OperatingIncomeLoss")])
        requests = [args(year=2024), args(metric="operating_income")]
        expected = self.prepare(requests, content=draft("unsupported"))
        response = self.ask("Microsoft parent net income FY2024 and operating income FY2025?")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([e["arguments"] for e in body["evidence"]], requests)
        self.assertEqual([e["result"] for e in body["evidence"]], expected)
        self.assertIn("[e1]", body["answer"])
        self.assertIn("[e2]", body["answer"])
        self.assertIn("consolidated net income", body["answer"])
        self.assertIn("before income taxes", body["answer"])
        self.assertNotIn("USD", body["answer"])
        self.assertEqual(body["guardrail"], {"status": "passed", "checked_claims": 0})
        self.assertEqual(self.ai.call_count, 1)

    def test_missing_status_still_rejects_cache_changed_after_lookup(self):
        self.prepare([args(year=2099)])
        def lookup_then_change_cache(*positional, **kwargs):
            result = get_annual_financial_fact(*positional, **kwargs)
            path = self.folder / "MSFT_companyfacts.json"
            path.write_text(path.read_text() + "\n")
            return result
        self.lookup.side_effect = lookup_then_change_cache
        self.assert_blocked(self.ask("Microsoft net income FY2099?"))
        self.assertEqual(self.ai.call_count, 1)

    def test_successful_result_disguised_as_missing_is_still_blocked(self):
        forged = {**self.result(), "status": "not_found"}
        self.prepare(content=draft())
        self.lookup.return_value = forged
        self.assert_blocked(self.ask())
        self.assertEqual(self.ai.call_count, 1)

    def test_mixed_found_and_missing_batch_still_requires_financial_draft(self):
        self.prepare([args(), args("GOOGL", year=2099)], content=draft("unsupported"))
        self.assert_blocked(self.ask("Microsoft net income FY2025 and Google net income FY2099?"))
        self.assertEqual(self.ai.call_count, 2)

    def test_nonfinancial_draft_with_evidence_is_still_rejected_by_guardrail(self):
        for request in (args(), args(year=2099)):
            evidence = [{"id": "e1", "tool": main.TOOL_NAME, "arguments": request,
                         "result": self.result(request)}]
            with self.assertRaisesRegex(GuardrailError, "did not account for the tool results"):
                check_and_render(draft("unsupported"), evidence, data_dir=self.folder)


class RequestedFiscalYearTests(unittest.TestCase):
    def test_explicit_year_labels_and_lists(self):
        for question in (
            "Google revenue for 2023, 2024 and 2025",
            "Google revenue for FY2023, FY 2024 and fiscal year 2025",
        ):
            self.assertEqual(requested_fiscal_years(question), {2023, 2024, 2025})

    def test_inclusive_ranges_keep_middle_year(self):
        for expression in ("2023-2025", "2023–2025", "2023—2025", "FY2023 to FY2025", "2023 through 2025"):
            self.assertEqual(requested_fiscal_years(f"Google revenue {expression}"), {2023, 2024, 2025})

    def test_dates_are_not_fiscal_year_labels(self):
        for cutoff in ("2025-12-31", "2025/12/31", "31/12/2025", "December 31, 2025", "31 December 2025", "December 2025"):
            with self.subTest(cutoff=cutoff):
                self.assertEqual(requested_fiscal_years(f"Google revenue as of {cutoff}"), set())
                self.assertEqual(requested_fiscal_years(f"Google FY2024 revenue as of {cutoff}"), {2024})

    def test_missing_relative_and_word_years_do_not_invent_defaults(self):
        for expression in ("", "latest", "last year", "twenty twenty-five"):
            self.assertEqual(requested_fiscal_years(f"Google revenue {expression}"), set())

    def test_short_or_reversed_ranges_require_clarification(self):
        for expression in ("2023-25", "FY2023-25", "FY2023/24", "2025 through 2023"):
            self.assertEqual(requested_fiscal_years(f"Google revenue {expression}"), set())

    def test_urls_amounts_and_long_identifiers_do_not_supply_years(self):
        for expression in ("https://example.invalid/2025/report", "USD 2025", "$2025", "2025 million", "2025.50", "120250", "report2025"):
            with self.subTest(expression=expression):
                self.assertEqual(requested_fiscal_years(f"Google revenue {expression}"), set())

    def test_normalised_text_and_specified_future_year_are_supported(self):
        self.assertEqual(requested_fiscal_years("Google revenue in FY２０２５"), {2025})
        self.assertEqual(requested_fiscal_years("Microsoft net income for FY2099"), {2099})


if __name__ == "__main__":
    unittest.main(verbosity=2)
