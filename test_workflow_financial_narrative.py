"""Self-contained receipt fixtures; no network, model, or human financial gold."""
import copy
from decimal import Decimal, ROUND_HALF_EVEN
import unittest

from company_registry import COMPANIES, filing_url
from workflow_contracts import WorkflowError, digest
from workflow_financial_narrative import financial_paragraphs, financial_narrative_issues
from workflow_render import check_prose
from workflow_support import sentences, support_issues


def request(tickers, years, risks=False):
    return {"question": "Compare the requested companies' operating margins and source evidence.",
            "tickers": sorted(tickers), "years": sorted(years), "include_risks": risks}


def add_margin(records, ticker, year, revenue, income):
    ids = {}
    for metric, value in [("revenue", revenue), ("operating_income", income)]:
        accession = COMPANIES[ticker]["cik"] + "-26-000001"
        fact = {"status": "ok", "ticker": ticker, "fiscal_year": year, "metric": metric,
                "unit": "USD", "value": value, "period_start": f"{year}-01-01",
                "period_end": f"{year}-12-31", "concept": "Revenues" if metric == "revenue" else "OperatingIncomeLoss",
                "filing_identifiers": [accession], "snapshot_sha256": "a" * 64,
                "filings_snapshot_sha256": "b" * 64}
        key = f"f_{fact['concept']}_{fact['period_start']}_{fact['period_end']}_{accession}_{digest(fact)[:12]}"
        records[key] = {"kind": "fact", "ticker": ticker, "year": year, "metric": metric,
                        "unit": "USD", "value": str(value), "source_sha256": fact["snapshot_sha256"],
                        "source_url": filing_url(ticker, accession), "fact": fact,
                        "arguments": {"ticker": ticker, "year": year, "metric": metric, "as_of": None}}
        ids[metric] = key
    value = str((Decimal(income) / Decimal(revenue) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
    mid = "margin_" + digest([ids["revenue"], ids["operating_income"], value])[:32]
    records[mid] = {"kind": "calculation", "ticker": ticker, "year": year, "unit": "%", "value": value,
                    "inputs": [ids["operating_income"], ids["revenue"]], "formula": "100 * operating_income / revenue",
                    "source_url": records[ids["operating_income"]]["source_url"], "source_sha256": "a" * 64}
    return mid


class FinancialNarrativeTests(unittest.TestCase):
    def test_google_declines_and_meta_remains_higher_using_observed_values(self):
        records = {}
        mids = [add_margin(records, "GOOGL", 2024, 350018000000, 112390000000),
                add_margin(records, "GOOGL", 2025, 402836000000, 129039000000),
                add_margin(records, "META", 2024, 164501000000, 69380000000),
                add_margin(records, "META", 2025, 200966000000, 83276000000)]
        scope = request(["GOOGL", "META"], [2024, 2025])
        paragraphs = financial_paragraphs(records, scope)
        self.assertEqual([records[key]["value"] for key in mids], ["32.11", "32.03", "42.18", "41.44"])
        self.assertEqual(len(paragraphs), 2)
        self.assertTrue(all("higher to lower as META, then GOOGL" in p["text"] for p in paragraphs))
        self.assertIn("GOOGL's calculated operating margin decreased", paragraphs[1]["text"])
        self.assertIn("META's calculated operating margin decreased", paragraphs[1]["text"])
        self.assertEqual(set(paragraphs[1]["evidence_ids"]), set(mids))
        self.assertEqual(support_issues({"paragraphs": paragraphs}, records, scope), [])
        for paragraph in paragraphs:
            check_prose(paragraph["text"])

    def test_netflix_ranks_above_comcast_without_causal_or_generated_quantities(self):
        records = {}
        add_margin(records, "CMCSA", 2025, 123707000000, 20672000000)
        add_margin(records, "NFLX", 2025, 45183036000, 13326603000)
        paragraphs = financial_paragraphs(records, request(["CMCSA", "NFLX"], [2025]))
        self.assertEqual(len(paragraphs), 1)
        self.assertIn("higher to lower as NFLX, then CMCSA", paragraphs[0]["text"])
        self.assertIn("requested fiscal period", paragraphs[0]["text"])
        self.assertNotIn("because", paragraphs[0]["text"])
        check_prose(paragraphs[0]["text"])

    def test_ties_and_trends_use_displayed_precision_not_hidden_decimals(self):
        records = {}
        for ticker in ["GOOGL", "META"]:
            add_margin(records, ticker, 2024, 1000000, 100041)
            add_margin(records, ticker, 2025, 1000000, 100049)
        paragraphs = financial_paragraphs(records, request(["META", "GOOGL"], [2024, 2025]))
        self.assertIn("GOOGL and META have equal calculated operating margins at the displayed precision", paragraphs[0]["text"])
        self.assertIn("GOOGL's calculated operating margin was unchanged", paragraphs[1]["text"])
        self.assertNotIn("increased", paragraphs[1]["text"])

    def test_negative_values_rank_correctly_and_reversed_record_order_is_stable(self):
        records = {}
        add_margin(records, "GOOGL", 2025, 100, -8)
        add_margin(records, "META", 2025, 100, -2)
        scope = request(["GOOGL", "META"], [2025])
        paragraphs = financial_paragraphs(records, scope)
        self.assertIn("higher to lower as META, then GOOGL", paragraphs[0]["text"])
        self.assertEqual(paragraphs, financial_paragraphs(dict(reversed(list(records.items()))), scope))

    def test_mixed_ties_and_maximum_scope_stay_within_receipt_bounds(self):
        records = {}
        scope = request(["GOOGL", "META", "MSFT", "AAPL"], [2023, 2024, 2025])
        for year in scope["years"]:
            for ticker, income in [("AAPL", 30), ("GOOGL", 20), ("META", 20), ("MSFT", 10)]:
                add_margin(records, ticker, year, 100, income)
        paragraphs = financial_paragraphs(records, scope)
        self.assertEqual(len(paragraphs), 3)
        for index, label in enumerate(["earliest", "middle", "latest"]):
            self.assertIn(label + " requested fiscal period", paragraphs[index]["text"])
            self.assertIn("AAPL, then GOOGL and META (equal at the displayed precision), then MSFT", paragraphs[index]["text"])
            self.assertLessEqual(len(sentences(paragraphs[index]["text"])), 8)
            self.assertLessEqual(len(paragraphs[index]["evidence_ids"]), 8)
            check_prose(paragraphs[index]["text"])

    def test_missing_calculations_do_not_create_absence_claims_or_bridge_requested_gap(self):
        records = {}
        add_margin(records, "GOOGL", 2023, 100, 20)
        add_margin(records, "GOOGL", 2025, 100, 30)
        add_margin(records, "META", 2025, 100, 40)
        scope = request(["GOOGL", "META"], [2023, 2024, 2025])
        paragraphs = financial_paragraphs(records, scope)
        self.assertEqual(len(paragraphs), 2)
        self.assertIn("calculation is available for GOOGL", paragraphs[0]["text"])
        self.assertIn("latest requested fiscal period", paragraphs[1]["text"])
        self.assertNotIn("increased", paragraphs[1]["text"])
        self.assertNotIn("missing", " ".join(p["text"] for p in paragraphs))
        self.assertNotIn("unavailable", " ".join(p["text"] for p in paragraphs))
        # An unrequested intermediate year does not break an adjacent requested-pair trend.
        sparse_scope = request(["GOOGL", "META"], [2023, 2025])
        self.assertIn("GOOGL's calculated operating margin increased", financial_paragraphs(records, sparse_scope)[1]["text"])

    def test_no_calculations_fail_explicitly(self):
        with self.assertRaises(WorkflowError) as caught:
            financial_paragraphs({}, request(["GOOGL"], [2025]))
        self.assertEqual(caught.exception.code, "missing_financial_comparisons")

    def test_duplicates_nonfinite_scope_and_provenance_mutations_fail(self):
        original = {}
        mid = add_margin(original, "GOOGL", 2025, 100, 25)
        scope = request(["GOOGL"], [2025])
        income_id, revenue_id = original[mid]["inputs"]
        mutations = [
            lambda r: r.update({"duplicate": copy.deepcopy(r[mid])}),
            lambda r: r[mid].update(value="NaN"),
            lambda r: r[mid].update(value="Infinity"),
            lambda r: r[mid].update(value="26.00"),
            lambda r: r[mid].update(ticker="META"),
            lambda r: r[mid].update(year=2024),
            lambda r: r[mid].update(source_sha256="b" * 64),
            lambda r: r[mid].update(formula="operating_income / revenue"),
            lambda r: r[mid].update(inputs=[revenue_id, income_id]),
            lambda r: r[revenue_id].update(value="0"),
            lambda r: r[income_id].update(value="NaN"),
            lambda r: r[income_id]["fact"].update(period_end="2025-11-30"),
            lambda r: r[income_id].update(source_url="https://example.invalid/changed"),
            lambda r: r[income_id]["fact"].update(value=True),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                records = copy.deepcopy(original)
                mutate(records)
                with self.assertRaises(WorkflowError):
                    financial_paragraphs(records, scope)

    def test_exact_financial_suffix_detects_wrong_direction_reordering_and_added_claims(self):
        records = {}
        add_margin(records, "GOOGL", 2024, 100, 30)
        add_margin(records, "GOOGL", 2025, 100, 25)
        scope = request(["GOOGL"], [2024, 2025])
        paragraphs = financial_paragraphs(records, scope)
        self.assertEqual(financial_narrative_issues({"paragraphs": paragraphs}, records, scope), [])
        bad = copy.deepcopy(paragraphs)
        bad[-1]["text"] = bad[-1]["text"].replace("decreased", "increased")
        self.assertTrue(financial_narrative_issues({"paragraphs": bad}, records, scope))
        self.assertTrue(financial_narrative_issues({"paragraphs": list(reversed(paragraphs))}, records, scope))
        self.assertTrue(financial_narrative_issues({"paragraphs": [paragraphs[0], *paragraphs]}, records, scope))

    def test_risk_analysis_cannot_be_omitted_or_smuggle_financial_claims(self):
        records = {}
        mid = add_margin(records, "GOOGL", 2025, 100, 25)
        records["p_sample"] = {"kind": "passage"}
        scope = request(["GOOGL"], [2025], risks=True)
        paragraphs = financial_paragraphs(records, scope)
        risk = {"text": "The cited source describes competitive pressures.", "evidence_ids": ["p_sample"]}
        self.assertTrue(financial_narrative_issues({"paragraphs": paragraphs}, records, scope))
        self.assertEqual(financial_narrative_issues({"paragraphs": [risk, *paragraphs]}, records, scope), [])
        for bad in [{**risk, "evidence_ids": [mid]}, {**risk, "evidence_ids": ["unknown"]},
                    {**risk, "text": "The operating margin improved."}, {**risk, "text": "Margins increased."}]:
            self.assertTrue(financial_narrative_issues({"paragraphs": [bad, *paragraphs]}, records, scope))
        self.assertTrue(financial_narrative_issues({"paragraphs": [risk] * 5 + paragraphs}, records, scope))


if __name__ == "__main__":
    unittest.main()
