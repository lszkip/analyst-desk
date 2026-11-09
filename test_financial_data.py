"""Offline tests with invented records; no SEC requests or OpenRouter credits."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from company_registry import COMPANIES, normalise_ticker
from financial_data import FinancialDataError, get_annual_financial_fact, select_annual_fact
from download_company_facts import download_company_facts, _download
from sec_fiscal_years import extract_year_evidence, primary_document_url, evidence_path
from financial_metrics import METRICS, METRIC_LABELS, METRIC_DEFINITIONS, CORE_METRICS, MetricName

ACC = "0000789019-25-000001"
AMEND = "0000789019-25-000002"
LATER = "0000789019-26-000002"


def record(value=100, **changes):
    return {"start": "2024-07-01", "end": "2025-06-30", "val": value,
            "filed": "2025-08-01", "accn": ACC, "form": "10-K", "fy": 2025, "fp": "FY", **changes}


def document(records=None, ticker="MSFT", tag="NetIncomeLoss"):
    return {"cik": int(COMPANIES[ticker]["cik"]), "entityName": COMPANIES[ticker]["name"],
            "facts": {"us-gaap": {tag: {"units": {"USD": [record()] if records is None else records}}}}}


def filing(accession=ACC, filed="2025-08-01", end="2025-06-30", form="10-K"):
    return {"accessionNumber": accession, "filingDate": filed, "reportDate": end,
            "form": form, "primaryDocument": "annual.htm"}


def history(ticker="MSFT", rows=None):
    return {"cik": COMPANIES[ticker]["cik"], "complete": True,
            "annual_filings": rows if rows is not None else [filing(),
                filing(AMEND, "2025-08-03", form="10-K/A"),
                filing(LATER, "2026-08-01", "2026-06-30")]}


def table(rows):
    return {key: [row[key] for row in rows]
            for key in ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")}


class AnnualFactTests(unittest.TestCase):
    def lookup(self, facts=None, **kwargs):
        options = {"history": history(), **kwargs}
        return select_annual_fact(document() if facts is None else facts, "MSFT", "net_income", 2025, **options)

    def test_microsoft_period_has_filing_evidence(self):
        r = self.lookup()
        self.assertEqual((r["period_start"], r["period_end"]), ("2024-07-01", "2025-06-30"))
        self.assertEqual(r["value"], 100)
        self.assertEqual(r["period_sources"][0]["filing_identifier"], ACC)
        self.assertEqual(r["period_days"], 365)

    def test_alphabet_period_and_google_aliases(self):
        facts = document([record(start="2025-01-01", end="2025-12-31", filed="2026-02-05")], ticker="GOOGL")
        meta = history("GOOGL", [filing(filed="2026-02-05", end="2025-12-31")])
        for ticker in ("GOOGL", "GOOG", "Google", "alphabet"):
            with self.subTest(ticker=ticker):
                r = select_annual_fact(facts, ticker, "net_income", 2025, history=meta)
                self.assertEqual(r["ticker"], "GOOGL")
                self.assertEqual((r["period_start"], r["period_end"]), ("2025-01-01", "2025-12-31"))

    def test_52_and_53_week_years_keep_exact_dates(self):
        for year, start, end, filed, days in [(2025, "2024-09-29", "2025-09-27", "2025-10-31", 364),
                                            (2023, "2022-09-25", "2023-09-30", "2023-11-03", 371)]:
            with self.subTest(days=days):
                facts = document([record(start=start, end=end, fy=year, filed=filed)], ticker="AAPL")
                r = select_annual_fact(facts, "AAPL", "net_income", year,
                    history=history("AAPL", [filing(filed=filed, end=end)]))
                self.assertEqual((r["period_start"], r["period_end"], r["period_days"]), (start, end, days))

    def test_fiscal_label_need_not_equal_calendar_end_year(self):
        # Fictional example: the company labels its year FY2025 even though
        # the last day falls in December 2024. No month-based inference allowed.
        facts = document([record(start="2024-01-01", end="2024-12-29", filed="2025-02-01")])
        r = self.lookup(facts, history=history(rows=[filing(filed="2025-02-01", end="2024-12-29")]))
        self.assertEqual(r["period_end"], "2024-12-29")
        self.assertEqual(r["fiscal_year"], 2025)

    def test_later_comparative_does_not_identify_its_filing_fiscal_year(self):
        facts = document([record(100, fy=2026, filed="2026-08-01", accn=LATER)])
        r = select_annual_fact(facts, "MSFT", "net_income", 2026, history=history())
        self.assertEqual(r["code"], "annual_period_not_found")

    def test_comparative_value_can_update_an_identified_period(self):
        facts = document([record(), record(110, fy=2026, filed="2026-08-01", accn=LATER)])
        r = self.lookup(facts)
        self.assertEqual(r["value"], 110)
        self.assertEqual(r["period_sources"][0]["filing_identifier"], ACC)
        self.assertEqual(r["filing_identifiers"], [LATER])

    def test_latest_amendment_and_as_of_cutoff(self):
        facts = document([record(), record(110, filed="2025-08-03", accn=AMEND, form="10-K/A")])
        self.assertEqual(self.lookup(facts)["value"], 110)
        self.assertEqual(self.lookup(facts, as_of="2025-08-02")["value"], 100)
        r = self.lookup(facts, as_of="2025-07-31")
        self.assertEqual(r["status"], "not_found")
        self.assertNotIn("value", r)

    def test_missing_year_and_metric_never_return_a_value(self):
        cases = [select_annual_fact(document(), "MSFT", "net_income", 2099, history=history()),
                 select_annual_fact(document(), "MSFT", "revenue", 2025, history=history())]
        for r in cases:
            self.assertEqual(r["status"], "not_found")
            self.assertNotIn("value", r)

    def test_short_transition_year_is_explicitly_unsupported(self):
        r = self.lookup(document([record(start="2025-01-01")]))
        self.assertEqual(r["status"], "unsupported_period")
        self.assertNotIn("value", r)

    def test_ambiguous_full_year_periods_fail_closed(self):
        r = self.lookup(document([record(), record(start="2024-06-30")]))
        self.assertEqual(r["code"], "annual_period_conflict")
        self.assertNotIn("value", r)

    def test_quarters_ytd_and_proxy_do_not_replace_annual_value(self):
        r = self.lookup(document([record(), record(900, form="10-Q"),
            record(901, start="2025-01-01"), record(902, form="DEF 14A")]))
        self.assertEqual(r["value"], 100)

    def test_period_can_be_identified_using_another_supported_metric(self):
        facts = document([record()], tag="OperatingIncomeLoss")
        facts["facts"]["us-gaap"]["NetIncomeLoss"] = {"units": {"USD": [
            record(110, fy=2026, filed="2026-08-01", accn=LATER)]}}
        self.assertEqual(self.lookup(facts)["value"], 110)

    def test_latest_date_is_selected_across_revenue_tags(self):
        facts = document([record()], tag="RevenueFromContractWithCustomerExcludingAssessedTax")
        facts["facts"]["us-gaap"]["Revenues"] = {"units": {"USD": [
            record(110, fy=2026, filed="2026-08-01", accn=LATER)]}}
        r = select_annual_fact(facts, "MSFT", "revenue", 2025, history=history())
        self.assertEqual((r["value"], r["concept"]), (110, "Revenues"))

    def test_conflicting_values_and_tags_are_reported(self):
        r = self.lookup(document([record(100), record(101)]))
        self.assertEqual(r["status"], "conflict")
        self.assertNotIn("value", r)
        facts = document([record()], tag="Revenues")
        facts["facts"]["us-gaap"]["SalesRevenueNet"] = {"units": {"USD": [record(101)]}}
        r = select_annual_fact(facts, "MSFT", "revenue", 2025, history=history())
        self.assertEqual(r["status"], "conflict")

    def test_zero_and_negative_values_are_valid(self):
        for value in (0, -100):
            self.assertEqual(self.lookup(document([record(value)]))["value"], value)

    def test_noninteger_values_are_rejected(self):
        for value in (True, "100", 100.0):
            with self.subTest(value=value), self.assertRaises(FinancialDataError):
                self.lookup(document([record(value)]))

    def test_wrong_identity_and_incomplete_history_are_errors(self):
        with self.assertRaises(FinancialDataError):
            self.lookup(document(ticker="GOOGL"))
        with self.assertRaises(FinancialDataError):
            self.lookup(history=history("GOOGL"))
        h = history(); h["complete"] = False
        with self.assertRaises(FinancialDataError):
            self.lookup(history=h)

    def test_mismatched_fact_and_filing_metadata_are_errors(self):
        for changes in ({"accn": "bad"}, {"filed": "2025-08-02"},
                        {"filed": "2025-02-30"}, {"start": "bad"}):
            with self.subTest(changes=changes), self.assertRaises(FinancialDataError):
                self.lookup(document([record(**changes)]))

    def test_duplicate_filing_metadata_cannot_disagree(self):
        h = history(); h["annual_filings"].append(filing(end="2025-06-29"))
        with self.assertRaises(FinancialDataError):
            self.lookup(history=h)

    def test_other_currency_is_not_relabelled_as_usd(self):
        facts = document()
        facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"] = {"EUR": [record()]}
        self.assertEqual(self.lookup(facts)["status"], "not_found")

    def test_invalid_arguments_are_rejected(self):
        for year in (True, "2025", 2025.0, 1800, 2101):
            with self.subTest(year=year), self.assertRaises(ValueError):
                select_annual_fact(document(), "MSFT", "net_income", year, history=history())
        for ticker, metric in [("../../secret", "net_income"), ("MSFT", "eps")]:
            with self.assertRaises(ValueError):
                select_annual_fact(document(), ticker, metric, 2025, history=history())
        with self.assertRaises(ValueError):
            self.lookup(as_of="2025-02-30")

    def test_config_has_15_distinct_companies_and_no_calendars(self):
        self.assertEqual(len(COMPANIES), 15)
        self.assertEqual(len({c["cik"] for c in COMPANIES.values()}), 15)
        self.assertEqual(len({c["sector"] for c in COMPANIES.values()}), 2)
        self.assertTrue(all("year_end" not in c for c in COMPANIES.values()))
        self.assertEqual(normalise_ticker("microsoft"), "MSFT")

    def test_filing_sources_are_deduplicated(self):
        r = self.lookup(document([record(), record()]))
        self.assertEqual(len(r["sources"]), 1)
        self.assertEqual(len(r["period_sources"]), 1)
        self.assertEqual(r["sources"][0]["url"],
            "https://www.sec.gov/Archives/edgar/data/789019/000078901925000001/0000789019-25-000001-index.html")


class CacheAndDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.facts_path = self.folder / "MSFT_companyfacts.json"
        self.history_path = self.folder / "MSFT_filings.json"

    def save(self):
        self.facts_path.write_text(json.dumps(document()))
        self.history_path.write_text(json.dumps(history()))

    def lookup(self):
        return get_annual_financial_fact("MSFT", "net_income", 2025, data_dir=self.folder)

    def responses(self, root=None, archive=None, facts=None):
        root = root if root is not None else {"cik": "0000789019", "filings": {"recent": table([filing()]), "files": []}}
        def fetch(url, _):
            if "/companyfacts/" in url: return json.dumps(document() if facts is None else facts).encode()
            if "-submissions-" in url: return json.dumps(archive).encode()
            return json.dumps(root).encode()
        return fetch

    def test_missing_facts_or_history_is_error_not_missing_financial_data(self):
        self.assertEqual(self.lookup()["code"], "data_file_missing")
        self.facts_path.write_text(json.dumps(document()))
        self.assertEqual(self.lookup()["code"], "data_file_missing")

    def test_corrupt_or_wrong_company_files_return_error(self):
        self.save()
        for path in (self.facts_path, self.history_path):
            before = path.read_bytes()
            for raw in (b"bad-json", b"\xff", b'{"cik": 123}'):
                path.write_bytes(raw)
                self.assertEqual(self.lookup()["code"], "invalid_data_file")
            path.write_bytes(before)

    def test_pretty_printing_preserves_value_and_changes_fingerprint(self):
        self.save(); first = self.lookup()
        self.facts_path.write_text(json.dumps(document(), indent=2))
        second = self.lookup()
        self.assertEqual(first["value"], second["value"])
        self.assertNotEqual(first["snapshot_sha256"], second["snapshot_sha256"])
        self.assertEqual(first["filings_snapshot_sha256"], second["filings_snapshot_sha256"])

    def test_reusing_complete_cache_makes_no_requests_or_changes(self):
        self.save(); before = (self.facts_path.read_bytes(), self.history_path.read_bytes())
        with patch("download_company_facts._download") as fetch:
            r = download_company_facts("MSFT", "", data_dir=self.folder)
        fetch.assert_not_called()
        self.assertEqual((r["status"], r["history_status"]), ("cached", "cached"))
        self.assertEqual(before, (self.facts_path.read_bytes(), self.history_path.read_bytes()))

    def test_existing_facts_are_preserved_while_adding_history(self):
        self.facts_path.write_text(json.dumps(document(), indent=4)); before = self.facts_path.read_bytes()
        with patch("download_company_facts._download", side_effect=self.responses()) as fetch:
            r = download_company_facts("MSFT", "Project test@example.com", data_dir=self.folder)
        self.assertEqual(r["status"], "cached")
        self.assertEqual(self.facts_path.read_bytes(), before)
        self.assertTrue(self.history_path.exists())
        self.assertEqual(fetch.call_count, 1)

    def test_new_download_preserves_raw_sources_and_fingerprints(self):
        with patch("download_company_facts._download", side_effect=self.responses()):
            download_company_facts("MSFT", "Project test@example.com", data_dir=self.folder)
        meta = json.loads((self.folder / "MSFT_companyfacts_metadata.json").read_text())
        self.assertEqual(meta["sha256"], hashlib.sha256(self.facts_path.read_bytes()).hexdigest())
        h = json.loads(self.history_path.read_text())
        raw = self.folder / "sec_submissions" / "CIK0000789019.json"
        self.assertEqual(h["source_snapshots"][0]["sha256"], hashlib.sha256(raw.read_bytes()).hexdigest())
        self.assertEqual(self.lookup()["value"], 100)

    def test_annual_filing_on_archive_page_is_found(self):
        name = "CIK0000789019-submissions-001.json"
        root = {"cik": "0000789019", "filings": {"recent": table([]), "files": [{"name": name}]}}
        with patch("download_company_facts._download", side_effect=self.responses(root, table([filing()]))):
            download_company_facts("MSFT", "Project test@example.com", data_dir=self.folder)
        self.assertEqual(self.lookup()["value"], 100)
        self.assertTrue((self.folder / "sec_submissions" / name).exists())

    def test_untrusted_archive_filename_is_rejected_before_fetch(self):
        for name in ("../../secret", "https://evil.test/a", "CIK0001652044-submissions-001.json"):
            root = {"cik": "0000789019", "filings": {"recent": table([]), "files": [{"name": name}]}}
            with self.subTest(name=name):
                with patch("download_company_facts._download", side_effect=self.responses(root)) as fetch:
                    with self.assertRaises(FinancialDataError):
                        download_company_facts("MSFT", "Project test@example.com", refresh=True, data_dir=self.folder)
                self.assertEqual(fetch.call_count, 2)
                self.assertFalse(self.history_path.exists())

    def test_failed_archive_does_not_publish_incomplete_history(self):
        root = {"cik": "0000789019", "filings": {"recent": table([]),
            "files": [{"name": "CIK0000789019-submissions-001.json"}]}}
        responses = [json.dumps(document()).encode(), json.dumps(root).encode(),
                     HTTPError("https://data.sec.gov", 429, "rate limited", None, None)]
        with patch("download_company_facts._download", side_effect=responses), self.assertRaises(HTTPError):
            download_company_facts("MSFT", "Project test@example.com", data_dir=self.folder)
        self.assertFalse(self.history_path.exists())

    def test_bad_or_failed_refresh_preserves_existing_facts(self):
        self.save(); before = self.facts_path.read_bytes()
        for payload in (json.dumps(document(ticker="GOOGL")).encode(),
                        HTTPError("https://data.sec.gov", 429, "rate limited", None, None)):
            with self.subTest(payload=type(payload).__name__):
                with patch("download_company_facts._download", side_effect=[payload]):
                    with self.assertRaises((FinancialDataError, HTTPError)):
                        download_company_facts("MSFT", "Project test@example.com", refresh=True, data_dir=self.folder)
                self.assertEqual(self.facts_path.read_bytes(), before)

    def test_missing_user_agent_blocks_network(self):
        with patch("download_company_facts.urlopen") as network, self.assertRaises(ValueError):
            _download("https://data.sec.gov/submissions/CIK0000789019.json", "")
        network.assert_not_called()

    def test_download_is_bounded_and_sets_identification_header(self):
        response = Mock(); response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False); response.read.return_value = b'{}'
        with patch("download_company_facts.urlopen", return_value=response) as network:
            _download("https://data.sec.gov/submissions/CIK0000789019.json", "Project test@example.com")
        request = network.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), "Project test@example.com")
        response.read.assert_called_once_with(25 * 1024 * 1024 + 1)


def original_filing(*, year=2025, tagged_year=2025, end="June 30, 2025",
                    cik="0000789019", form="10-K", context="current", definition=None):
    definition = definition if definition is not None else (
        f"References to fiscal {year}, for example, refer to the fiscal year ending {end}.")
    return (f'<html><body><ix:hidden>'
        f'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="{context}">{cik}</ix:nonNumeric>'
        f'<ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="{context}">{tagged_year}</ix:nonNumeric>'
        f'<ix:nonNumeric name="dei:DocumentFiscalPeriodFocus" contextRef="{context}">FY</ix:nonNumeric>'
        f'<ix:nonNumeric name="dei:DocumentType" contextRef="{context}">{form}</ix:nonNumeric>'
        f'</ix:hidden><div>For the fiscal year ended '
        f'<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="{context}">{end}</ix:nonNumeric>'
        f'</div><p>{definition}</p></body></html>').encode()


class FiscalYearEvidenceTests(unittest.TestCase):
    """Invented records reproduce a duplicate year label across two annual filings."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.facts_path = self.folder / "MSFT_companyfacts.json"
        self.history_path = self.folder / "MSFT_filings.json"
        self.facts = document([record(100), record(200, accn=LATER, filed="2026-08-01",
            start="2025-07-01", end="2026-06-30", fy=2025),
            record(110, accn=LATER, filed="2026-08-01", fy=2025)])
        self.history = history(rows=[filing(), filing(LATER, "2026-08-01", "2026-06-30")])
        self.facts_path.write_text(json.dumps(self.facts))
        self.history_path.write_text(json.dumps(self.history))
        self.raw = {ACC: original_filing(), LATER: original_filing(year=2026, end="June 30, 2026")}

    def fetch(self, url, _):
        for accession, raw in self.raw.items():
            if accession.replace("-", "") in url:
                return raw
        self.fail("Unexpected request: " + url)

    def prepare(self):
        with patch("download_company_facts._download", side_effect=self.fetch):
            return download_company_facts("MSFT", "Project test@example.com", data_dir=self.folder, resolve_years=[2025])

    def lookup(self, year=2025, as_of=None):
        return get_annual_financial_fact("MSFT", "net_income", year, as_of=as_of, data_dir=self.folder)

    def test_duplicate_tag_resolves_both_years_and_keeps_later_comparative(self):
        self.assertEqual(self.lookup()["code"], "annual_period_conflict")
        self.prepare()
        previous, current = self.lookup(), self.lookup(2026)
        self.assertEqual((previous["period_start"], previous["period_end"], previous["value"]),
                         ("2024-07-01", "2025-06-30", 110))
        self.assertEqual((current["period_start"], current["period_end"], current["value"]),
                         ("2025-07-01", "2026-06-30", 200))
        self.assertEqual(previous["filing_identifiers"], [LATER])
        self.assertEqual(previous["fiscal_year_resolutions"][1]["tagged_fiscal_year"], 2025)
        self.assertEqual(previous["fiscal_year_resolutions"][1]["fiscal_year"], 2026)

    def test_as_of_does_not_use_future_resolution_or_value(self):
        self.prepare()
        result = self.lookup(as_of="2025-12-31")
        self.assertEqual(result["value"], 100)
        self.assertEqual(len(result["fiscal_year_resolutions"]), 1)
        self.assertEqual(result["fiscal_year_resolutions"][0]["filing_identifier"], ACC)
        self.assertEqual(self.lookup(2026, as_of="2025-12-31")["status"], "not_found")

    def test_fiscal_label_is_not_inferred_from_end_year(self):
        raw = original_filing(year=2025, end="December 29, 2024")
        row = filing(filed="2025-02-01", end="2024-12-29")
        evidence = extract_year_evidence(raw, "MSFT", row)
        self.assertEqual(evidence["fiscal_year"], 2025)
        self.assertEqual(evidence["report_date"], "2024-12-29")

    def test_nested_inline_period_date_and_split_sentence(self):
        raw = original_filing().replace(b'June 30, 2025</ix:nonNumeric>',
            b'<ix:nonNumeric name="dei:CurrentFiscalYearEndDate" contextRef="current">June 30</ix:nonNumeric>, 2025</ix:nonNumeric>')
        raw = raw.replace(b'References to fiscal 2025', b'References to <b>fiscal 2025</b>')
        self.assertEqual(extract_year_evidence(raw, "MSFT", filing())["fiscal_year"], 2025)

    def test_date_only_or_unsupported_wording_does_not_resolve(self):
        for definition in ("For the fiscal year ended June 30, 2025.", "Our year ends in June."):
            with self.subTest(definition=definition):
                self.assertIsNone(extract_year_evidence(original_filing(definition=definition), "MSFT", filing()))

    def test_definition_for_another_reporting_date_does_not_resolve(self):
        raw = original_filing(definition="References to fiscal 2024 refer to the fiscal year ending June 30, 2024.")
        self.assertIsNone(extract_year_evidence(raw, "MSFT", filing()))

    def test_hidden_definition_does_not_resolve(self):
        for wrapper in ('<ix:hidden>{}</ix:hidden>', '<div style="display: none">{}</div>'):
            definition = wrapper.format("References to fiscal 2025 refer to the fiscal year ending June 30, 2025.")
            self.assertIsNone(extract_year_evidence(original_filing(definition=definition), "MSFT", filing()))

    def test_conflicting_explicit_definitions_are_rejected(self):
        definition = ("References to fiscal 2025 refer to the fiscal year ending June 30, 2025. "
                      "References to fiscal 2024 refer to the fiscal year ending June 30, 2025.")
        with self.assertRaises(ValueError):
            extract_year_evidence(original_filing(definition=definition), "MSFT", filing())

    def test_wrong_identity_form_context_and_cover_date_are_rejected(self):
        raw = original_filing()
        cases = [original_filing(cik="0001652044"), original_filing(form="10-Q"),
                 raw.replace(b'name="dei:DocumentType" contextRef="current"',
                             b'name="dei:DocumentType" contextRef="other"'),
                 raw.replace(b'June 30, 2025</ix:nonNumeric>', b'June 30, 2024</ix:nonNumeric>')]
        for bad in cases:
            with self.subTest(raw=bad[:50]), self.assertRaises(ValueError):
                extract_year_evidence(bad, "MSFT", filing())

    def test_untrusted_primary_document_is_rejected_before_network(self):
        for name in ("../../secret.html", "https://evil.test/a.htm", "annual.htm?x=1"):
            self.history["annual_filings"][0]["primaryDocument"] = name
            self.history_path.write_text(json.dumps(self.history))
            with patch("download_company_facts._download") as fetch, self.assertRaises(ValueError):
                download_company_facts("MSFT", "Project test@example.com", data_dir=self.folder, resolve_years=[2025])
            fetch.assert_not_called()

    def test_preparation_preserves_facts_and_caches_source_bytes(self):
        before = self.facts_path.read_bytes()
        prepared = self.prepare()
        self.assertEqual(prepared["fiscal_year_resolution"], {"candidates": 2, "resolved": 2, "unresolved": 0})
        self.assertEqual(self.facts_path.read_bytes(), before)
        meta = json.loads(self.history_path.read_bytes())
        for row in meta["annual_filings"]:
            self.assertEqual(evidence_path(self.folder, row["fiscal_year_evidence"]).read_bytes(), self.raw[row["accessionNumber"]])

    def test_prepared_evidence_is_reused_without_network(self):
        self.prepare(); before = self.history_path.read_bytes()
        with patch("download_company_facts._download") as fetch:
            download_company_facts("MSFT", "", data_dir=self.folder, resolve_years=[2025])
        fetch.assert_not_called()
        self.assertEqual(before, self.history_path.read_bytes())

    def test_unsupported_document_keeps_conflict(self):
        self.raw[LATER] = original_filing(year=2026, end="June 30, 2026", definition="Unrecognized definition.")
        self.assertEqual(self.prepare()["fiscal_year_resolution"]["unresolved"], 1)
        self.assertEqual(self.lookup()["code"], "annual_period_conflict")

    def test_failed_second_download_preserves_original_history(self):
        before = self.history_path.read_bytes()
        with patch("download_company_facts._download", side_effect=[self.raw[ACC], OSError("offline")]):
            with self.assertRaises(OSError):
                download_company_facts("MSFT", "Project test@example.com", data_dir=self.folder, resolve_years=[2025])
        self.assertEqual(before, self.history_path.read_bytes())
        self.assertEqual(self.lookup()["code"], "annual_period_conflict")

    def test_missing_or_modified_original_document_blocks_lookup(self):
        self.prepare()
        row = json.loads(self.history_path.read_bytes())["annual_filings"][1]
        path = evidence_path(self.folder, row["fiscal_year_evidence"])
        path.write_bytes(path.read_bytes() + b'changed')
        self.assertEqual(self.lookup()["code"], "invalid_data_file")
        path.unlink()
        self.assertEqual(self.lookup()["code"], "invalid_data_file")

    def test_self_consistent_but_forged_metadata_fails_reextraction(self):
        self.prepare()
        meta = json.loads(self.history_path.read_bytes())
        evidence = meta["annual_filings"][1]["fiscal_year_evidence"]
        evidence["fiscal_year"] = 2027
        evidence["quote"] = evidence["quote"].replace("fiscal 2026", "fiscal 2027")
        self.history_path.write_text(json.dumps(meta))
        self.assertEqual(self.lookup()["code"], "invalid_data_file")


class MetricDefinitionTests(unittest.TestCase):
    def facts(self, *, parent=True, consolidated=True, pretax=True):
        facts = document()
        tags = facts["facts"]["us-gaap"]
        if not parent:
            del tags["NetIncomeLoss"]
        # Revenue establishes the period even if the requested measure is absent.
        tags["Revenues"] = {"units": {"USD": [record(1000)]}}
        if consolidated:
            tags["ProfitLoss"] = {"units": {"USD": [record(120)]}}
        if pretax:
            tags[METRICS["pretax_income"][0]] = {"units": {"USD": [record(160)]}}
        return facts

    def lookup(self, metric, facts=None, **kwargs):
        return select_annual_fact(self.facts() if facts is None else facts,
                                 "MSFT", metric, 2025, history=history(), **kwargs)

    def test_parent_and_consolidated_profits_remain_distinct(self):
        parent = self.lookup("net_income")
        group = self.lookup("net_income_consolidated")
        self.assertEqual((parent["value"], parent["concept"]), (100, "NetIncomeLoss"))
        self.assertEqual((group["value"], group["concept"]), (120, "ProfitLoss"))
        self.assertIn("including", group["metric_definition"])

    def test_missing_parent_suggests_consolidated_without_returning_an_amount(self):
        result = self.lookup("net_income", self.facts(parent=False))
        self.assertEqual((result["status"], result["metric"]), ("not_found", "net_income"))
        self.assertNotIn("value", result)
        alternative = result["available_alternatives"][0]
        self.assertEqual(alternative["metric"], "net_income_consolidated")
        self.assertEqual(alternative["concept"], "ProfitLoss")
        self.assertNotIn('"value"', json.dumps(alternative))
        self.assertEqual(alternative["sources"][0]["filing_identifier"], ACC)

    def test_parent_only_is_not_automatically_consolidated(self):
        result = self.lookup("net_income_consolidated", self.facts(consolidated=False))
        self.assertEqual(result["status"], "not_found")
        self.assertNotIn("value", result)
        self.assertEqual(result["available_alternatives"][0]["metric"], "net_income")

    def test_pretax_income_does_not_fill_missing_operating_income(self):
        result = self.lookup("operating_income")
        self.assertEqual(result["status"], "not_found")
        self.assertNotIn("value", result)
        self.assertEqual(result["available_alternatives"][0]["metric"], "pretax_income")
        self.assertEqual(self.lookup("pretax_income")["value"], 160)

    def test_different_pretax_equity_investment_definition_is_not_an_alias(self):
        facts = self.facts(pretax=False)
        tag = "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"
        facts["facts"]["us-gaap"][tag] = {"units": {"USD": [record(170)]}}
        self.assertEqual(self.lookup("pretax_income", facts)["status"], "not_found")

    def test_alternative_must_match_period_currency_and_cutoff(self):
        for change in ("different_period", "currency", "future"):
            facts = self.facts(parent=False)
            tag = facts["facts"]["us-gaap"]["ProfitLoss"]
            if change == "different_period":
                tag["units"]["USD"][0]["start"] = "2025-01-01"
            elif change == "currency":
                tag["units"]["EUR"] = tag["units"].pop("USD")
            else:
                tag["units"]["USD"] = [record(120, accn=LATER, filed="2026-08-01", fy=2026)]
            with self.subTest(change=change):
                result = self.lookup("net_income", facts, as_of="2025-12-31")
                self.assertNotIn("available_alternatives", result)

    def test_conflicting_alternative_is_not_advertised_as_available(self):
        facts = self.facts(parent=False)
        facts["facts"]["us-gaap"]["ProfitLoss"]["units"]["USD"].append(record(121))
        self.assertNotIn("available_alternatives", self.lookup("net_income", facts))
        self.assertEqual(self.lookup("net_income_consolidated", facts)["status"], "conflict")

    def test_new_measures_keep_latest_amendment_and_as_of_policy(self):
        for metric in ("net_income_consolidated", "pretax_income"):
            facts = self.facts()
            facts["facts"]["us-gaap"][METRICS[metric][0]]["units"]["USD"] = [
                record(0), record(-20, accn=AMEND, filed="2025-08-03", form="10-K/A")]
            self.assertEqual(self.lookup(metric, facts)["value"], -20)
            self.assertEqual(self.lookup(metric, facts, as_of="2025-08-02")["value"], 0)

    def test_consolidated_only_facts_can_establish_an_annual_period(self):
        facts = document([record(120)], tag="ProfitLoss")
        result = self.lookup("net_income_consolidated", facts)
        self.assertEqual((result["period_start"], result["period_end"], result["value"]),
                         ("2024-07-01", "2025-06-30", 120))

    def test_new_measure_values_must_still_be_integer_usd(self):
        for metric in ("net_income_consolidated", "pretax_income"):
            for value in (True, 123.0, "123"):
                facts = self.facts()
                facts["facts"]["us-gaap"][METRICS[metric][0]]["units"]["USD"][0]["val"] = value
                with self.subTest(metric=metric, value=value), self.assertRaises(FinancialDataError):
                    self.lookup(metric, facts)

    def test_metric_catalog_contracts_and_core_coverage_stay_aligned(self):
        from typing import get_args
        self.assertEqual(set(get_args(MetricName)), set(METRICS))
        self.assertEqual(set(METRICS), set(METRIC_LABELS))
        self.assertEqual(set(METRICS), set(METRIC_DEFINITIONS))
        self.assertEqual(CORE_METRICS, ("revenue", "operating_income", "net_income"))

    def test_coverage_cli_defaults_remain_135_and_explicit_metrics_work(self):
        import check_financial_coverage as coverage
        import io
        from contextlib import redirect_stdout
        for argv, expected_count, expected_metrics in (
            (["coverage"], 135, set(CORE_METRICS)),
            (["coverage", "--tickers", "IBM", "--metrics", "pretax_income", "--years", "2023", "2024", "2025"],
             3, {"pretax_income"}),
        ):
            with patch("sys.argv", argv), patch.object(coverage, "get_annual_financial_fact", return_value={"status": "ok"}) as lookup, redirect_stdout(io.StringIO()):
                self.assertEqual(coverage.main(), 0)
            self.assertEqual(lookup.call_count, expected_count)
            self.assertEqual({c.args[1] for c in lookup.call_args_list}, expected_metrics)


if __name__ == "__main__":
    unittest.main()
