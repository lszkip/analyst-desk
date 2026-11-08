"""Offline tests using small, invented financial records. No API key needed."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from financial_data import (
    FinancialDataError, annual_period, get_annual_financial_fact,
    select_annual_fact,
)
from download_company_facts import download_company_facts


def record(value=100, **overrides):
    return {
        "start": "2024-07-01", "end": "2025-06-30", "val": value,
        "filed": "2025-08-01", "accn": "0000789019-25-000001",
        "form": "10-K", "fy": 2025, "fp": "FY", **overrides,
    }


def document(records=None, *, ticker="MSFT", tag="NetIncomeLoss"):
    return {
        "cik": 789019 if ticker == "MSFT" else 1652044,
        "entityName": "MICROSOFT CORP" if ticker == "MSFT" else "Alphabet Inc.",
        "facts": {"us-gaap": {tag: {"units": {"USD": records if records is not None else [record()]}}}},
    }


class AnnualFactTests(unittest.TestCase):
    def lookup(self, facts, **kwargs):
        return select_annual_fact(facts, "MSFT", "net_income", 2025, **kwargs)

    def test_microsoft_fiscal_year(self):
        self.assertEqual(annual_period("MSFT", 2025), ("2024-07-01", "2025-06-30"))

    def test_alphabet_calendar_year_and_share_class_alias(self):
        facts = document([record(start="2025-01-01", end="2025-12-31", filed="2026-02-05")], ticker="GOOGL")
        for ticker in ("GOOGL", "GOOG", "googl"):
            with self.subTest(ticker=ticker):
                result = select_annual_fact(facts, ticker, "net_income", 2025)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["period_start"], "2025-01-01")
                self.assertEqual(result["period_end"], "2025-12-31")
                self.assertEqual(result["ticker"], "GOOGL")

    def test_invalid_year_is_rejected(self):
        for value in (True, 2025.0, "2025", 1800, 2101):
            with self.subTest(value=value), self.assertRaises(ValueError):
                annual_period("MSFT", value)

    def test_unsupported_company_and_metric_are_rejected(self):
        with self.assertRaises(ValueError):
            select_annual_fact(document(), "../../secret", "net_income", 2025)
        with self.assertRaises(ValueError):
            select_annual_fact(document(), "MSFT", "earnings_per_share", 2025)

    def test_reporting_period_wins_over_filing_fiscal_year_label(self):
        result = self.lookup(document([record(100, fy=2026, filed="2026-08-01")]))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["fiscal_year"], 2025)

    def test_quarter_ytd_proxy_and_wrong_period_are_excluded(self):
        facts = document([
            record(900, form="10-Q"), record(901, start="2025-01-01"),
            record(902, end="2025-03-31"), record(903, form="DEF 14A"), record(100),
        ])
        self.assertEqual(self.lookup(facts)["value"], 100)

    def test_latest_amendment_wins(self):
        facts = document([record(100), record(110, filed="2025-08-03", form="10-K/A", accn="0000789019-25-000002")])
        result = self.lookup(facts)
        self.assertEqual(result["value"], 110)
        self.assertEqual(result["sources"][0]["form"], "10-K/A")

    def test_as_of_excludes_later_restatements(self):
        facts = document([record(100), record(110, filed="2026-08-01", accn="0000789019-26-000002")])
        self.assertEqual(self.lookup(facts, as_of="2025-09-01")["value"], 100)
        self.assertEqual(self.lookup(facts)["value"], 110)

    def test_cutoff_before_first_filing_returns_not_found(self):
        result = self.lookup(document(), as_of="2025-07-01")
        self.assertEqual(result["status"], "not_found")
        self.assertNotIn("value", result)

    def test_invalid_cutoff_is_rejected(self):
        for value in ("2025-02-30", "20250801", 2025):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.lookup(document(), as_of=value)

    def test_absent_year_or_concept_returns_not_found(self):
        result = select_annual_fact(document(), "MSFT", "net_income", 2099)
        self.assertEqual(result["status"], "not_found")
        self.assertNotIn("value", result)
        result = select_annual_fact(document(), "MSFT", "revenue", 2025)
        self.assertEqual(result["status"], "not_found")

    def test_wrong_company_is_an_error(self):
        with self.assertRaises(FinancialDataError):
            self.lookup(document(ticker="GOOGL"))

    def test_malformed_matching_records_fail_closed(self):
        cases = [
            {"val": True}, {"val": 100.0}, {"val": "100"},
            {"filed": "not-a-date"}, {"filed": "2025-01-01"},
            {"accn": "bad-id"}, {"filed": None},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(FinancialDataError):
                self.lookup(document([record(**changes)]))

    def test_zero_and_loss_are_valid_values(self):
        for value in (0, -100):
            with self.subTest(value=value):
                self.assertEqual(self.lookup(document([record(value)]))["value"], value)

    def test_conflicting_latest_values_are_not_silently_selected(self):
        result = self.lookup(document([record(100), record(101, accn="0000789019-25-000002")]))
        self.assertEqual(result["status"], "conflict")
        self.assertNotIn("value", result)

    def test_latest_revenue_tag_wins_over_older_tag(self):
        facts = document([record(100)], tag="RevenueFromContractWithCustomerExcludingAssessedTax")
        facts["facts"]["us-gaap"]["Revenues"] = {"units": {"USD": [record(110, filed="2026-08-01")]}}
        result = select_annual_fact(facts, "MSFT", "revenue", 2025)
        self.assertEqual(result["concept"], "Revenues")
        self.assertEqual(result["value"], 110)

    def test_conflicting_revenue_tags_fail_closed(self):
        facts = document([record(100)], tag="RevenueFromContractWithCustomerExcludingAssessedTax")
        facts["facts"]["us-gaap"]["Revenues"] = {"units": {"USD": [record(101)]}}
        result = select_annual_fact(facts, "MSFT", "revenue", 2025)
        self.assertEqual(result["status"], "conflict")
        self.assertNotIn("value", result)

    def test_sources_are_deduplicated_and_link_to_correct_company(self):
        result = self.lookup(document([record(), record()]))
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(result["sources"][0]["url"],
            "https://www.sec.gov/Archives/edgar/data/789019/000078901925000001/0000789019-25-000001-index.html")

    def test_other_currency_is_not_reported_as_usd(self):
        facts = document()
        facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"] = {"EUR": [record(900)]}
        self.assertEqual(self.lookup(facts)["status"], "not_found")


class CacheAndDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / "MSFT_companyfacts.json"

    def lookup(self):
        return get_annual_financial_fact("MSFT", "net_income", 2025, data_dir=self.folder)

    def save(self, facts=None):
        self.path.write_text(json.dumps(document() if facts is None else facts), encoding="utf-8")

    def test_missing_file_is_error_not_not_found(self):
        self.assertEqual(self.lookup()["code"], "data_file_missing")
        self.assertEqual(self.lookup()["status"], "error")

    def test_corrupt_or_wrong_company_file_is_error(self):
        for raw in (b"not json", b"\xff", json.dumps(document(ticker="GOOGL")).encode()):
            with self.subTest(raw=raw[:20]):
                self.path.write_bytes(raw)
                self.assertEqual(self.lookup()["code"], "invalid_data_file")

    def test_indented_json_and_compact_json_return_same_value(self):
        self.save()
        first = self.lookup()
        self.path.write_text(json.dumps(document(), indent=2), encoding="utf-8")
        second = self.lookup()
        self.assertEqual(first["value"], second["value"])
        self.assertNotEqual(first["snapshot_sha256"], second["snapshot_sha256"])
        self.assertEqual(second["snapshot_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_cached_download_does_not_fetch_or_rewrite(self):
        self.save()
        before = self.path.read_bytes()
        with patch("download_company_facts.urlopen") as network:
            result = download_company_facts("MSFT", "", data_dir=self.folder)
        network.assert_not_called()
        self.assertEqual(result["status"], "cached")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.folder / "MSFT_companyfacts_metadata.json").exists())

    def test_download_records_source_time_and_hash(self):
        raw = json.dumps(document()).encode()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = raw
        with patch("download_company_facts.urlopen", return_value=response) as network:
            result = download_company_facts("MSFT", "AnalystDesk test@example.com", data_dir=self.folder)
        self.assertEqual(result["status"], "downloaded")
        self.assertEqual(self.path.read_bytes(), raw)
        metadata = json.loads((self.folder / "MSFT_companyfacts_metadata.json").read_text())
        self.assertEqual(metadata["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertIn("downloaded_at", metadata)
        self.assertEqual(network.call_args.args[0].full_url,
                         "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json")

    def test_invalid_refresh_preserves_cached_file(self):
        self.save()
        before = self.path.read_bytes()
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(document(ticker="GOOGL")).encode()
        with patch("download_company_facts.urlopen", return_value=response), self.assertRaises(FinancialDataError):
            download_company_facts("MSFT", "AnalystDesk test@example.com", refresh=True, data_dir=self.folder)
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_refresh_preserves_cached_file(self):
        self.save()
        before = self.path.read_bytes()
        with patch("download_company_facts.urlopen", side_effect=HTTPError("https://data.sec.gov", 429, "limited", None, None)), self.assertRaises(HTTPError):
            download_company_facts("MSFT", "AnalystDesk test@example.com", refresh=True, data_dir=self.folder)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_user_agent_prevents_download(self):
        with patch("download_company_facts.urlopen") as network, self.assertRaises(ValueError):
            download_company_facts("MSFT", "", data_dir=self.folder)
        network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
