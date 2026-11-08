"""Offline endpoint and output-guardrail tests. No OpenRouter credits used."""

from copy import deepcopy
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-placeholder"}), patch("openai.OpenAI"):
    import main

from output_guardrails import FinancialClaim


# Fixed test fixtures: these tests do not verify the original SEC data.
FACT_2025 = {
    "status": "ok",
    "company": "MICROSOFT CORP",
    "concept": "NetIncomeLoss",
    "fiscal_year": 2025,
    "period_start": "2024-07-01",
    "period_end": "2025-06-30",
    "value": 101832000000,
    "unit": "USD",
    "filed": "2026-07-29",
    "filing_identifiers": ["0001193125-26-323660"],
}


def fake_response(content=None, tool_calls=None, finish_reason="stop"):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[
        SimpleNamespace(message=message, finish_reason=finish_reason)
    ])


def fake_tool_call(year, index=1):
    payload = {
        "id": f"call_test_{index}",
        "type": "function",
        "function": {
            "name": "get_msft_annual_net_income",
            "arguments": json.dumps({"year": year}),
        },
    }
    call = Mock()
    call.id = payload["id"]
    call.type = payload["type"]
    call.function = SimpleNamespace(**payload["function"])
    call.model_dump.return_value = payload
    return call


def make_claim(result, evidence_id="e1"):
    fields = set(FinancialClaim.model_fields) - {"evidence_id"}
    return {"evidence_id": evidence_id, **{key: result[key] for key in fields}}


def draft(kind="financial", claims=None):
    return json.dumps({"kind": kind, "claims": claims or []})


class AskEndpointTests(unittest.TestCase):
    def setUp(self):
        client_patch = patch("main.client")
        self.ai = client_patch.start().chat.completions.create
        self.addCleanup(client_patch.stop)
        tool_patch = patch("main.get_msft_annual_net_income")
        self.lookup = tool_patch.start()
        self.addCleanup(tool_patch.stop)
        self.http = TestClient(main.app)
        self.addCleanup(self.http.close)

    def prepare_lookup(self, year, result, answer_json):
        self.ai.reset_mock()
        self.lookup.reset_mock()
        self.lookup.side_effect = None
        self.lookup.return_value = result
        self.ai.side_effect = [
            fake_response(tool_calls=[fake_tool_call(year)]),
            fake_response(content=answer_json),
        ]

    def ask(self, question="Microsoft annual net income for FY2025?"):
        return self.http.post("/ask", json={"question": question})

    def assert_blocked(self, response):
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["code"], "output_guardrail_blocked")
        self.assertNotIn("answer", response.json())

    def test_successful_lookup_preserves_evidence(self):
        self.prepare_lookup(2025, FACT_2025, draft(claims=[make_claim(FACT_2025)]))
        response = self.ask()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.lookup.assert_called_once_with(year=2025)
        self.assertEqual(body["evidence"][0]["result"], FACT_2025)
        self.assertIn("USD 101,832,000,000", body["answer"])
        self.assertIn("FY2025", body["answer"])
        self.assertIn("0001193125-26-323660", body["answer"])
        self.assertEqual(body["guardrail"], {"status": "passed", "checked_claims": 1})
        self.assertEqual(self.ai.call_count, 2)

    def test_missing_data_is_sent_back_to_ai(self):
        result = {"status": "not_found", "message": "No matching record."}
        self.prepare_lookup(2099, result, draft())
        response = self.ask("Microsoft annual net income for FY2099?")
        self.assertEqual(response.status_code, 200)
        self.lookup.assert_called_once_with(year=2099)
        self.assertEqual(self.ai.call_count, 2)
        tool_message = self.ai.call_args_list[1].kwargs["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["tool_call_id"], "call_test_1")
        self.assertEqual(json.loads(tool_message["content"])["result"], result)
        self.assertIn("No matching annual net income record", response.json()["answer"])
        self.assertEqual(response.json()["evidence"][0]["result"], result)
        self.assertEqual(response.json()["guardrail"]["checked_claims"], 0)

    def test_clarification_skips_lookup(self):
        self.ai.return_value = fake_response(content=draft("ask_year"))
        response = self.ask("What was Microsoft's net income?")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Which Microsoft fiscal year", response.json()["answer"])
        self.assertEqual(response.json()["evidence"], [])
        self.assertEqual(response.json()["guardrail"]["status"], "not_applicable")
        self.lookup.assert_not_called()
        self.assertEqual(self.ai.call_count, 1)

    def test_invalid_year_is_rejected_before_lookup(self):
        self.ai.return_value = fake_response(tool_calls=[fake_tool_call("not a year")])
        response = self.ask()
        self.assertEqual(response.status_code, 502)
        self.lookup.assert_not_called()
        self.assertEqual(self.ai.call_count, 1)

    def test_unreadable_file_is_reported_as_error(self):
        self.ai.return_value = fake_response(tool_calls=[fake_tool_call(2025)])
        self.lookup.side_effect = FileNotFoundError("Simulated missing file")
        response = self.ask()
        self.assertEqual(response.status_code, 500)
        self.assertIn("Could not read", response.json()["detail"])
        self.assertEqual(self.ai.call_count, 1)

    def test_changed_claim_fields_are_blocked(self):
        # Deliberately introduce the mistakes we want the guardrail to catch.
        wrong_values = {
            "value": 110000000000,
            "fiscal_year": 2024,
            "unit": "EUR",
            "company": "Another company",
            "concept": "Revenues",
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "evidence_id": "e999",
        }
        for field, wrong in wrong_values.items():
            with self.subTest(field=field):
                claim = make_claim(FACT_2025)
                claim[field] = wrong
                self.prepare_lookup(2025, FACT_2025, draft(claims=[claim]))
                self.assert_blocked(self.ask())

    def test_claim_without_any_lookup_is_blocked(self):
        self.ai.return_value = fake_response(content=draft(claims=[make_claim(FACT_2025)]))
        self.assert_blocked(self.ask())
        self.lookup.assert_not_called()

    def test_free_text_and_extra_prose_are_blocked(self):
        bad_drafts = [
            "Microsoft made USD 110 billion.",
            json.dumps({"kind": "ask_year", "claims": [], "answer": "USD 110 billion"}),
            draft("ask_year", claims=[make_claim(FACT_2025)]),
        ]
        for content in bad_drafts:
            with self.subTest(content=content):
                self.ai.return_value = fake_response(content=content)
                self.assert_blocked(self.ask())

    def test_successful_result_cannot_be_omitted_or_duplicated(self):
        for claims in ([], [make_claim(FACT_2025), make_claim(FACT_2025)]):
            with self.subTest(claims=claims):
                self.prepare_lookup(2025, FACT_2025, draft(claims=claims))
                self.assert_blocked(self.ask())

    def test_no_claim_allowed_for_missing_or_conflicting_data(self):
        for status in ("not_found", "conflict"):
            with self.subTest(status=status):
                self.prepare_lookup(2025, {"status": status}, draft(claims=[make_claim(FACT_2025)]))
                self.assert_blocked(self.ask())

    def test_conflict_has_clear_message_and_no_figure(self):
        self.prepare_lookup(2025, {"status": "conflict"}, draft())
        response = self.ask()
        self.assertEqual(response.status_code, 200)
        self.assertIn("conflicting", response.json()["answer"])
        self.assertNotIn("USD", response.json()["answer"])

    def test_two_years_are_matched_to_their_own_evidence(self):
        fact_2024 = deepcopy(FACT_2025)
        fact_2024.update(fiscal_year=2024, period_start="2023-07-01", period_end="2024-06-30", value=88136000000)
        self.lookup.side_effect = [fact_2024, FACT_2025]
        self.ai.side_effect = [
            fake_response(tool_calls=[fake_tool_call(2024, 1), fake_tool_call(2025, 2)]),
            fake_response(content=draft(claims=[make_claim(fact_2024, "e1"), make_claim(FACT_2025, "e2")])),
        ]
        response = self.ask("Show Microsoft annual net income for FY2024 and FY2025.")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["guardrail"]["checked_claims"], 2)
        self.assertIn("USD 88,136,000,000", response.json()["answer"])
        self.assertIn("USD 101,832,000,000", response.json()["answer"])
        self.assertEqual(self.lookup.call_count, 2)

    def test_incomplete_evidence_is_blocked(self):
        for field in ("filed", "filing_identifiers", "value"):
            with self.subTest(field=field):
                result = deepcopy(FACT_2025)
                del result[field]
                self.prepare_lookup(2025, result, draft(claims=[make_claim(FACT_2025)]))
                self.assert_blocked(self.ask())

    def test_wrong_period_in_tool_result_is_blocked_even_if_ai_copies_it(self):
        result = deepcopy(FACT_2025)
        result["period_start"] = "2025-01-01"
        self.prepare_lookup(2025, result, draft(claims=[make_claim(result)]))
        self.assert_blocked(self.ask())

    def test_booleans_are_not_accepted_as_financial_values(self):
        result = deepcopy(FACT_2025)
        result["value"] = True
        self.prepare_lookup(2025, result, draft(claims=[make_claim(result)]))
        self.assert_blocked(self.ask())

    def test_definition_and_future_reply_do_not_invent_figures(self):
        for kind in ("define_net_income", "compare_revenue_profit", "future_year"):
            with self.subTest(kind=kind):
                self.ai.return_value = fake_response(content=draft(kind))
                response = self.ask()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["guardrail"]["status"], "not_applicable")
                self.lookup.assert_not_called()

    def test_blank_question_does_not_call_ai(self):
        response = self.ask("   ")
        self.assertEqual(response.status_code, 422)
        self.ai.assert_not_called()

    def test_truncated_output_is_not_returned(self):
        self.ai.return_value = fake_response(content=draft("ask_year"), finish_reason="length")
        response = self.ask()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("answer", response.json())


if __name__ == "__main__":
    unittest.main(verbosity=2)
