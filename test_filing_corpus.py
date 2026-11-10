"""Offline corpus tests with invented SEC records and invented inline HTML.

No real SEC/OpenRouter connections. These prove the downloader's behaviour,
not that all 45 live company reports have been downloaded or parsed.
"""

from contextlib import redirect_stdout
from datetime import date
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from company_registry import COMPANIES
import filing_corpus as corpus
from prepare_filing_corpus import main as cli
from test_api import write_fixture


def original_html(document, **changes):
    values = {"EntityCentralIndexKey": document["cik"], "DocumentType": "10-K",
              "DocumentPeriodEndDate": document["period_end"],
              "DocumentFiscalYearFocus": str(document["fiscal_year"]),
              "DocumentFiscalPeriodFocus": "FY", **changes}
    identity = "".join(f'<ix:nonNumeric name="dei:{name}" contextRef="annual"><span>{value}</span></ix:nonNumeric>'
                       for name, value in values.items())
    return (f'<html><body>{identity}<h2>Item 1A. Risk Factors</h2>'
            '<p>Fictional test company faces supply constraints.</p>'
            '<table><tr><th>Year</th><th>Revenue</th></tr>'
            '<tr><td>2025</td><td>100</td></tr></table></body></html>').encode()


def alternate_date_html(document, *, numeric=None, context="annual",
                        transform="date-month-day-year",
                        namespace="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"):
    """Invented filing reproducing Disney's hidden numeric / visible named date."""
    end = date.fromisoformat(document["period_end"])
    months = ("January February March April May June July August September October November December").split()
    english = f"{months[end.month - 1]} {end.day}, {end.year}"
    numeric = numeric if numeric is not None else f"{end.month}/{end.day}/{end.year}"
    raw = original_html(document, DocumentPeriodEndDate=english)
    raw = raw.replace(b"<html>", f'<html xmlns:ixt="{namespace}">'.encode())
    hidden = (f'<ix:hidden><ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="{context}" '
              f'format="ixt:{transform}">{numeric}</ix:nonNumeric></ix:hidden>')
    return raw.replace(b"<body>", b"<body>" + hidden.encode())


class FilingCorpusTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        self.out = self.root / "filings"
        for ticker in ("MSFT", "GOOGL"):
            write_fixture(self.raw, ticker)
        self.kwargs = {"data_dir": self.raw, "corpus_dir": self.out}
        self.plan = corpus.plan_corpus(["MSFT", "GOOGL"], [2023, 2024, 2025], data_dir=self.raw)
        self.pages = {d["source_url"]: original_html(d) for d in self.plan["documents"]}
        p = patch("filing_corpus.fetch_html", side_effect=lambda url, ua: self.pages[url])
        self.fetch = p.start()
        self.addCleanup(p.stop)

    def prepare(self, tickers=("MSFT", "GOOGL"), years=(2023, 2024, 2025), **extra):
        return corpus.prepare_corpus(tickers, years, "Offline tests test@example.invalid", **self.kwargs, **extra)

    def manifest(self):
        return json.loads((self.out / "manifest.json").read_bytes())

    def mutate_history(self, change, ticker="MSFT"):
        path = self.raw / f"{ticker}_filings.json"
        history = json.loads(path.read_bytes())
        change(history["annual_filings"])
        path.write_text(json.dumps(history))

    def test_plan_has_six_unique_pairs_and_actual_reporting_dates(self):
        self.assertEqual(self.plan["requested_pairs"], 6)
        rows = {(d["ticker"], d["fiscal_year"]): d for d in self.plan["documents"]}
        self.assertEqual(rows["MSFT", 2025]["period_start"], "2024-07-01")
        self.assertEqual(rows["GOOGL", 2025]["period_end"], "2025-12-31")
        self.assertTrue(all(d["source_url"].startswith("https://www.sec.gov/Archives/") for d in rows.values()))
        self.fetch.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_aliases_and_duplicate_years_do_not_duplicate_documents(self):
        plan = corpus.plan_corpus(["Google", "GOOGL"], [2025, 2025], data_dir=self.raw)
        self.assertEqual(plan["requested_pairs"], 1)
        self.assertEqual(plan["documents"][0]["ticker"], "GOOGL")

    def test_all_fifteen_fictional_companies_produce_45_planned_pairs(self):
        for ticker in COMPANIES:
            write_fixture(self.raw, ticker)
        plan = corpus.plan_corpus(list(COMPANIES), [2023, 2024, 2025], data_dir=self.raw)
        self.assertEqual(len(plan["documents"]), 45)
        self.assertEqual(len({d["sector"] for d in plan["documents"]}), 2)

    def test_missing_cache_stops_before_network_and_output(self):
        (self.raw / "MSFT_filings.json").unlink()
        with self.assertRaises(corpus.CorpusError):
            self.prepare()
        self.fetch.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_missing_fiscal_year_does_not_guess_from_calendar(self):
        with self.assertRaises(corpus.CorpusError):
            self.prepare(years=[2030])
        self.fetch.assert_not_called()

    def test_invalid_selection_never_downloads(self):
        for tickers, years in [([], [2025]), (["MSFT"], []), (["INVALID"], [2025]), (["MSFT"], [True])]:
            with self.subTest(tickers=tickers, years=years), self.assertRaises(ValueError):
                self.prepare(tickers=tickers, years=years)
        self.fetch.assert_not_called()

    def test_cutoff_before_filing_prevents_download(self):
        with self.assertRaises(corpus.CorpusError):
            self.prepare(tickers=["MSFT"], years=[2025], as_of="2025-07-01")
        self.fetch.assert_not_called()

    def test_partial_amendment_is_not_selected_as_base_10k(self):
        def add_amendment(rows):
            rows.append({**rows[-1], "accessionNumber": "0000789019-25-000002",
                         "filingDate": "2025-08-15", "form": "10-K/A"})
        self.mutate_history(add_amendment)
        plan = corpus.plan_corpus(["MSFT"], [2025], data_dir=self.raw)
        self.assertEqual(plan["documents"][0]["form"], "10-K")
        self.assertTrue(plan["documents"][0]["filing_identifier"].endswith("000001"))

    def test_bad_primary_document_is_rejected_before_download(self):
        self.mutate_history(lambda rows: rows[-1].update(primaryDocument="../../secret.html"))
        with self.assertRaises(corpus.CorpusError):
            self.prepare()
        self.fetch.assert_not_called()

    def test_download_keeps_original_bytes_tables_and_provenance(self):
        result = self.prepare()
        self.assertEqual(result["downloaded"], 6)
        for row in self.manifest()["documents"]:
            raw = (self.out / row["html_path"]).read_bytes()
            self.assertEqual(raw, self.pages[row["source_url"]])
            self.assertIn(b"<table>", raw)
            self.assertEqual(corpus.digest(raw), row["source_sha256"])
            self.assertEqual(row["inline_identity"]["fiscal_year"], row["fiscal_year"])
            self.assertIn("downloaded_at", row)

    def test_repeated_run_uses_verified_cache_without_network_or_changes(self):
        self.prepare()
        before = {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        self.fetch.reset_mock()
        result = self.prepare()
        self.assertEqual(result["cached"], 6)
        self.fetch.assert_not_called()
        self.assertEqual(before, {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()})

    def test_wrong_identity_fields_do_not_publish_a_document(self):
        changes = {"EntityCentralIndexKey": "0000000001", "DocumentType": "10-Q",
                   "DocumentPeriodEndDate": "2020-01-01", "DocumentFiscalYearFocus": "2020",
                   "DocumentFiscalPeriodFocus": "Q1"}
        doc = self.plan["documents"][0]
        for key, value in changes.items():
            with self.subTest(field=key), self.assertRaises(corpus.CorpusError):
                corpus.validate_html(original_html(doc, **{key: value}), doc)

    def test_nested_inline_facts_are_read(self):
        doc = self.plan["documents"][0]
        result = corpus.validate_html(original_html(doc), doc)
        self.assertEqual(result["form"], "10-K")

    def test_equivalent_date_formats_prepare_and_verify_without_changing_bytes(self):
        doc = self.plan["documents"][0]
        raw = alternate_date_html(doc)
        self.pages[doc["source_url"]] = raw
        self.prepare(tickers=[doc["ticker"]], years=[doc["fiscal_year"]])
        saved = self.manifest()["documents"][0]
        self.assertEqual((self.out / saved["html_path"]).read_bytes(), raw)
        self.assertEqual(saved["inline_identity"]["report_date"], doc["period_end"])
        self.assertEqual(corpus.verify_corpus([doc["ticker"]], [doc["fiscal_year"]], **self.kwargs)["verified_company_years"], 1)

    def test_actually_different_report_dates_still_fail(self):
        doc = self.plan["documents"][0]
        for numeric in ("12/30/2023", "12/31/2022"):
            with self.subTest(numeric=numeric), self.assertRaisesRegex(corpus.CorpusError, "Conflicting.*DocumentPeriodEndDate"):
                corpus.validate_html(alternate_date_html(doc, numeric=numeric), doc)

    def test_equivalent_report_dates_in_different_contexts_still_fail(self):
        doc = self.plan["documents"][0]
        with self.assertRaisesRegex(corpus.CorpusError, "Conflicting.*DocumentPeriodEndDate"):
            corpus.validate_html(alternate_date_html(doc, context="other-company"), doc)

    def test_numeric_report_dates_require_supported_declared_transform(self):
        doc = self.plan["documents"][0]
        raw = alternate_date_html(doc)
        bad_pages = [raw.replace(b' format="ixt:date-month-day-year"', b''),
                     raw.replace(b'xmlns:ixt=', b'xmlns:unrelated='),
                     alternate_date_html(doc, namespace="https://example.invalid/transform"),
                     alternate_date_html(doc, transform="date-day-month-year"),
                     alternate_date_html(doc, transform="invented-transform")]
        for bad in bad_pages:
            with self.subTest(raw=bad[:120]), self.assertRaises(corpus.CorpusError):
                corpus.validate_html(bad, doc)

    def test_invalid_and_short_year_numeric_dates_are_rejected(self):
        doc = self.plan["documents"][0]
        for numeric in ("2/30/2023", "2/29/2023", "13/31/2023", "12/31/23"):
            with self.subTest(numeric=numeric), self.assertRaises(corpus.CorpusError):
                corpus.validate_html(alternate_date_html(doc, numeric=numeric), doc)

    def test_date_transform_namespace_aliases_and_local_scope_are_checked(self):
        doc = self.plan["documents"][0]
        raw = alternate_date_html(doc)
        alias = raw.replace(b'xmlns:ixt=', b'xmlns:alternate=').replace(b'format="ixt:', b'format="alternate:')
        scoped = raw.replace(b'<body>', b'<body><div xmlns:ixt="https://example.invalid/transform"></div>')
        for good in (alias, scoped):
            self.assertEqual(corpus.validate_html(good, doc)["report_date"], doc["period_end"])
        bad = raw.replace(b'<ix:hidden>', b'<ix:hidden xmlns:ixt="https://example.invalid/transform">')
        with self.assertRaises(corpus.CorpusError):
            corpus.validate_html(bad, doc)

    def test_supported_legacy_and_modern_date_transforms_agree(self):
        doc = self.plan["documents"][0]
        for version, transform, numeric in (
            ("2011-07-31", "datemonthdayyear", "12/31/2023"),
            ("2015-02-26", "datemonthdayyearen", "December 31, 2023"),
            ("2020-02-12", "date-monthname-day-year-en", "December 31, 2023"),
            ("2022-02-16", "date-year-month-day", "2023-12-31")):
            with self.subTest(version=version, transform=transform):
                raw = alternate_date_html(doc, numeric=numeric, transform=transform,
                    namespace=f"http://www.xbrl.org/inlineXBRL/transformation/{version}")
                self.assertEqual(corpus.validate_html(raw, doc)["report_date"], doc["period_end"])

    def test_explicit_year_evidence_accepts_equivalent_dates_and_rejects_conflicts(self):
        doc = next(d for d in self.plan["documents"] if d["ticker"] == "MSFT" and d["fiscal_year"] == 2025)
        raw = alternate_date_html(doc).replace(b'</body>',
            b'<p>References to fiscal 2025 refer to the fiscal year ending June 30, 2025.</p></body>')
        row = {"accessionNumber": doc["filing_identifier"], "primaryDocument": doc["primary_document"],
               "form": "10-K", "reportDate": doc["period_end"]}
        evidence = corpus.extract_year_evidence(raw, "MSFT", row)
        self.assertEqual(evidence["fiscal_year"], 2025)
        self.assertEqual(evidence["source_sha256"], corpus.digest(raw))
        with self.assertRaisesRegex(ValueError, "Conflicting.*DocumentPeriodEndDate"):
            corpus.extract_year_evidence(raw.replace(b'6/30/2025', b'6/29/2025'), "MSFT", row)

    def prepare_resolved_source(self):
        doc = next(d for d in self.plan["documents"] if d["ticker"] == "MSFT" and d["fiscal_year"] == 2025)
        html = original_html(doc, DocumentFiscalYearFocus="2024").replace(b"</body>",
            b"<p>References to fiscal 2025 refer to the fiscal year ending June 30, 2025.</p></body>")
        history_path = self.raw / "MSFT_filings.json"
        history = json.loads(history_path.read_bytes())
        row = history["annual_filings"][-1]
        evidence = corpus.extract_year_evidence(html, "MSFT", row)
        row["fiscal_year_evidence"] = evidence
        history_path.write_text(json.dumps(history))
        evidence_dir = self.raw / "sec_fiscal_years"
        evidence_dir.mkdir()
        (evidence_dir / (evidence["source_sha256"] + ".html")).write_bytes(html)
        facts_path = self.raw / "MSFT_companyfacts.json"
        facts = json.loads(facts_path.read_bytes())
        for tag in facts["facts"]["us-gaap"].values():
            tag["units"]["USD"][-1]["fy"] = 2024
        facts_path.write_text(json.dumps(facts))
        self.pages[doc["source_url"]] = html
        return doc, html, evidence

    def test_verified_explicit_year_resolution_can_override_inline_year_tag(self):
        doc, html, evidence = self.prepare_resolved_source()
        self.prepare(tickers=["MSFT"], years=[2025])
        saved = self.manifest()["documents"][0]
        self.assertEqual(saved["fiscal_year"], 2025)
        self.assertEqual(saved["inline_identity"]["tagged_fiscal_year"], 2024)
        self.assertEqual(saved["fiscal_year_evidence"], evidence)

    def test_resolved_source_reuses_exact_verified_bytes_without_downloading(self):
        doc, html, evidence = self.prepare_resolved_source()
        result = self.prepare(tickers=["MSFT"], years=[2025])
        self.fetch.assert_not_called()
        self.assertEqual(result["reused_fiscal_year_documents"], 1)
        self.assertEqual(result["downloaded"], 0)
        saved = self.manifest()["documents"][0]
        self.assertEqual(saved["source_sha256"], evidence["source_sha256"])
        self.assertEqual((self.out / saved["html_path"]).read_bytes(), html)
        self.assertIsNone(saved["downloaded_at"])
        self.assertEqual(saved["source_acquisition"], "fiscal_year_cache")
        self.assertIn("prepared_at", saved)
        self.assertEqual(corpus.verify_corpus(["MSFT"], [2025], **self.kwargs)["verified_company_years"], 1)

    def test_different_delivery_script_keeps_original_fingerprint_checks(self):
        doc, html, evidence = self.prepare_resolved_source()
        altered = html.replace(b"</body>", b'<script src="/delivery-script.js"></script></body>')
        self.pages[doc["source_url"]] = altered
        planned = corpus.plan_corpus(["MSFT"], [2025], data_dir=self.raw)["documents"][0]
        with self.assertRaisesRegex(corpus.CorpusError, "fingerprint differs"):
            corpus.validate_html(altered, planned)
        self.prepare(tickers=["MSFT"], years=[2025])
        self.fetch.assert_not_called()
        self.assertEqual(self.manifest()["documents"][0]["source_sha256"], corpus.digest(html))

    def test_refresh_preserves_resolved_source_and_does_not_redownload_it(self):
        self.prepare_resolved_source()
        self.prepare(tickers=["MSFT"], years=[2025])
        result = self.prepare(tickers=["MSFT"], years=[2025], refresh=True)
        self.fetch.assert_not_called()
        self.assertEqual(result["reused_fiscal_year_documents"], 1)

    def test_original_source_changed_after_planning_still_blocks_preparation(self):
        doc, html, evidence = self.prepare_resolved_source()
        original_path = self.raw / "sec_fiscal_years" / (evidence["source_sha256"] + ".html")
        def change_source(*args):
            original_path.write_bytes(html.replace(b"</body>", b"<!--modified--></body>"))
            return original_path
        with patch("filing_corpus.evidence_path", side_effect=change_source):
            with self.assertRaisesRegex(corpus.CorpusError, "fingerprint differs"):
                self.prepare(tickers=["MSFT"], years=[2025])
        self.assertFalse((self.out / "manifest.json").exists())
        self.fetch.assert_not_called()

    def test_existing_receipts_without_new_provenance_fields_remain_usable(self):
        self.prepare()
        for path in (self.out / "receipts").glob("*.json"):
            record = json.loads(path.read_bytes())
            record.pop("source_acquisition")
            record.pop("prepared_at")
            path.write_text(json.dumps(record))
        self.fetch.reset_mock()
        result = self.prepare()
        self.assertEqual(result["cached"], 6)
        self.fetch.assert_not_called()

    def test_identity_failure_names_company_year_and_reason(self):
        doc = self.plan["documents"][0]
        with self.assertRaisesRegex(corpus.CorpusError, "GOOGL FY2023:.*Wrong company"):
            corpus.validate_html(original_html(doc, EntityCentralIndexKey="1"), doc)

    def test_unsupported_inline_year_mismatch_is_not_guessed(self):
        doc = self.plan["documents"][0]
        self.pages[doc["source_url"]] = original_html(doc, DocumentFiscalYearFocus="1999")
        with self.assertRaises(corpus.CorpusError):
            self.prepare()
        self.assertFalse((self.out / "manifest.json").exists())

    def test_error_page_empty_body_and_oversized_body_are_rejected(self):
        doc = self.plan["documents"][0]
        for raw in [b"", b"<html><h1>Access denied</h1></html>", b"not html"]:
            with self.subTest(raw=raw), self.assertRaises(corpus.CorpusError):
                corpus.validate_html(raw, doc)
        with patch("filing_corpus.MAX_HTML_BYTES", 10), self.assertRaises(corpus.CorpusError):
            corpus.validate_html(original_html(doc), doc)

    def test_modified_html_fails_cache_and_verification(self):
        self.prepare()
        row = self.manifest()["documents"][0]
        (self.out / row["html_path"]).write_bytes(b"changed")
        self.fetch.reset_mock()
        with self.assertRaises(corpus.CorpusError):
            self.prepare()
        with self.assertRaises(corpus.CorpusError):
            corpus.verify_corpus(["MSFT", "GOOGL"], [2023, 2024, 2025], **self.kwargs)
        self.fetch.assert_not_called()

    def test_failed_run_preserves_previous_manifest_and_retry_reuses_receipt(self):
        self.prepare(tickers=["MSFT"], years=[2023])
        before = (self.out / "manifest.json").read_bytes()
        def flaky(url, ua):
            if "/000078901925000001/" in url:
                raise OSError("controlled download failure")
            return self.pages[url]
        self.fetch.side_effect = flaky
        with self.assertRaises(OSError):
            self.prepare(tickers=["MSFT"])
        self.assertEqual((self.out / "manifest.json").read_bytes(), before)
        self.fetch.reset_mock()
        self.fetch.side_effect = lambda url, ua: self.pages[url]
        result = self.prepare(tickers=["MSFT"])
        self.assertEqual(result["cached"], 2)
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual(self.fetch.call_count, 1)

    def test_failed_refresh_does_not_replace_good_manifest(self):
        self.prepare()
        before = (self.out / "manifest.json").read_bytes()
        self.fetch.side_effect = lambda *a: b"<html>Access denied</html>"
        with self.assertRaises(corpus.CorpusError):
            self.prepare(refresh=True)
        self.assertEqual((self.out / "manifest.json").read_bytes(), before)

    def test_changed_financial_snapshot_requires_explicit_refresh(self):
        self.prepare()
        path = self.raw / "MSFT_companyfacts.json"
        path.write_text(json.dumps(json.loads(path.read_bytes()), indent=2))
        self.fetch.reset_mock()
        with self.assertRaises(corpus.CorpusError):
            self.prepare()
        self.fetch.assert_not_called()

    def test_source_change_during_download_prevents_manifest_publication(self):
        changed = False
        def fetch(url, ua):
            nonlocal changed
            if not changed:
                path = self.raw / "MSFT_companyfacts.json"
                path.write_text(json.dumps(json.loads(path.read_bytes()), indent=2))
                changed = True
            return self.pages[url]
        self.fetch.side_effect = fetch
        with self.assertRaises(corpus.CorpusError):
            self.prepare()
        self.assertFalse((self.out / "manifest.json").exists())

    def test_separate_batches_preserve_previously_prepared_companies(self):
        self.prepare(tickers=["MSFT"])
        result = self.prepare(tickers=["GOOGL"])
        self.assertEqual(result["manifest_documents"], 6)
        verified = corpus.verify_corpus(["MSFT", "GOOGL"], [2023, 2024, 2025], **self.kwargs)
        self.assertEqual(verified["verified_company_years"], 6)
        self.assertEqual(len(verified["sectors"]), 2)

    def test_manifest_path_cannot_select_an_arbitrary_file(self):
        self.prepare()
        manifest = self.manifest()
        manifest["documents"][0]["html_path"] = "../../private"
        (self.out / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(corpus.CorpusError):
            corpus.verify_corpus(["MSFT", "GOOGL"], [2023, 2024, 2025], **self.kwargs)

    def test_duplicate_manifest_pairs_are_rejected(self):
        self.prepare()
        manifest = self.manifest()
        manifest["documents"].append(manifest["documents"][0])
        (self.out / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(corpus.CorpusError):
            self.prepare()

    def test_verify_missing_pair_never_claims_full_coverage(self):
        self.prepare(tickers=["MSFT"], years=[2025])
        with self.assertRaises(corpus.CorpusError):
            corpus.verify_corpus(["MSFT", "GOOGL"], [2023, 2024, 2025], **self.kwargs)

    def test_cli_plan_only_is_read_only(self):
        out = StringIO()
        with redirect_stdout(out):
            status = cli(["MSFT", "GOOGL", "--years", "2023", "2024", "2025", "--plan-only",
                          "--data-dir", str(self.raw), "--corpus-dir", str(self.out)])
        self.assertEqual(status, 0)
        self.assertIn("Plan ready: 6 company-years", out.getvalue())
        self.assertFalse(self.out.exists())
        self.fetch.assert_not_called()


class CorpusHTTPTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://www.sec.gov/Archives/edgar/data/789019/000078901925000001/annual.htm"

    def test_invalid_url_and_user_agent_never_open_connection(self):
        with patch("filing_corpus.build_opener") as opener:
            for url, ua in [("https://evil.example/a.html", "Test a@b.invalid"),
                            (self.url + "?redirect=evil", "Test a@b.invalid"),
                            (self.url, ""), (self.url, "Test a@b.invalid\r\nInjected: x")]:
                with self.subTest(url=url, ua=ua), self.assertRaises(corpus.CorpusError):
                    corpus.fetch_html(url, ua)
            opener.assert_not_called()

    def test_request_is_identified_bounded_and_rate_limited(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"<html></html>"
        with patch("filing_corpus.build_opener") as build, patch("filing_corpus.time.sleep") as sleep, \
             patch("filing_corpus.time.monotonic", return_value=10), patch("filing_corpus._last_request", 10):
            build.return_value.open.return_value = response
            self.assertEqual(corpus.fetch_html(self.url, "Test a@b.invalid"), b"<html></html>")
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header("User-agent"), "Test a@b.invalid")
            self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 30)
            response.read.assert_called_once_with(corpus.MAX_HTML_BYTES + 1)
            sleep.assert_called_once_with(0.3)
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsInstance(build.call_args.args[1], corpus._NoRedirect)

    def test_redirect_is_not_followed(self):
        self.assertIsNone(corpus._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example"))

    def test_oversized_http_response_is_rejected(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"12345"
        with patch("filing_corpus.build_opener") as build, patch("filing_corpus.MAX_HTML_BYTES", 4), \
             patch("filing_corpus.time.sleep"):
            build.return_value.open.return_value = response
            with self.assertRaises(corpus.CorpusError):
                corpus.fetch_html(self.url, "Test a@b.invalid")


if __name__ == "__main__":
    unittest.main()
