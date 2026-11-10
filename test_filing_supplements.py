"""Invented, offline exhibits exercise provenance, page mapping and recovery."""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import filing_corpus as corpus
import filing_passages as passages
import filing_supplements as supplements
from prepare_filing_supplements import main as cli
from test_api import write_fixture
from test_filing_passages import BODY, TABLE, report


def ibm_body(year, href="exhibit13.htm"):
    def ref(a, b):
        return f"Refer to pages {a} through {b} of IBM’s {year} Annual Report to Stockholders, which are incorporated herein by reference."
    return (BODY.replace("<p>Revenue increased.</p>", "<p>" + ref(6, 7) + "</p>")
        .replace("<p>Amounts are in USD millions.</p>" + TABLE, "<p>" + ref(10, 15) + "</p>")
        .replace("<p>Fictional company manufactures widgets.</p>", "<p>Fictional company manufactures widgets.</p>" + TABLE)
        + '<h2>Item 15. Exhibits</h2><table><tr><td>(13)</td><td><a href="' + href
        + '">Annual Report to Security Holders</a> (2)</td><td>13</td></tr></table>'
        + '<h2>Item 16. Form 10-K Summary</h2><p>None.</p>')


def exhibit(year):
    titles = ["Report of Financials", "Management Discussion", "Management Discussion",
        "Report of Management", "Report of Independent Registered Public Accounting Firm",
        "Consolidated Income Statement", "Consolidated Statement of Comprehensive Income",
        "Consolidated Balance Sheet", "Consolidated Statement of Cash Flows",
        "Consolidated Statement of Equity", "Notes to the Consolidated Financial Statements",
        "Performance Graphs", "Stockholder Information"]
    html = "<html><body>"
    for number, title in enumerate(titles, 5):
        header = (f"Report of Financials {supplements.COMPANY} Exhibit 13 5" if number == 5
                  else f"{number} {title} {supplements.COMPANY}")
        html += f'<table><tr><td>{header}</td></tr></table><p>Page {number} content.</p>'
        if number == 6:
            html += (f'<p>The financial section of the International Business Machines Corporation '
                     f'(IBM or the company) {year} Annual Report includes the Management Discussion.</p>')
        if number in (6, 10, 11, 12, 13, 14, 15):
            html += TABLE
    return (html + "</body></html>").encode()


class SupplementTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "raw"
        self.data.mkdir()
        self.corpus = self.root / "filings"
        self.output = self.root / "passages"
        self.options = dict(data_dir=self.data, corpus_dir=self.corpus)
        for ticker in ("IBM", "MSFT"):
            write_fixture(self.data, ticker)
        docs = corpus.plan_corpus(["IBM", "MSFT"], [2023, 2024, 2025], data_dir=self.data)["documents"]
        raw = {d["source_url"]: report(d, ibm_body(d["fiscal_year"]) if d["ticker"] == "IBM" else BODY) for d in docs}
        with patch("filing_corpus.fetch_html", side_effect=lambda url, ua: raw[url]):
            corpus.prepare_corpus(["IBM", "MSFT"], [2023, 2024, 2025], "offline test@example.invalid", **self.options)
        self.sources = passages._load_sources(["IBM"], [2023, 2024, 2025], self.data, self.corpus, None)
        self.doc, self.primary = self.sources[0]
        self.plan = supplements.supplement_plan(self.doc, self.primary)
        self.web = {supplements.supplement_plan(d, r)["source_url"]: exhibit(d["fiscal_year"]) for d, r in self.sources}
        fetch = patch("filing_supplements.fetch_html", side_effect=lambda url, ua: self.web[url])
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)
        network = patch("socket.socket", side_effect=AssertionError("Tests must stay offline"))
        network.start()
        self.addCleanup(network.stop)

    def prepare(self, years=(2023, 2024, 2025), **kwargs):
        return supplements.prepare_supplements(["IBM"], years, "offline test@example.invalid", **self.options, **kwargs)

    def passages(self, years=(2023, 2024, 2025), tickers=("IBM",), **kwargs):
        return passages.prepare_passages(tickers, years, **self.options, passages_dir=self.output, **kwargs)

    def snapshot(self, folder):
        return {p.relative_to(folder).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in folder.rglob("*") if p.is_file()}

    def changed_plan(self, transform):
        raw = transform(self.primary)
        return supplements.supplement_plan({**self.doc, "source_sha256": corpus.digest(raw)}, raw)

    def saved_record(self):
        return supplements._manifest(self.corpus / "supplements")[0]

    def test_plan_uses_explicit_same_accession_link_and_page_ranges_without_writes(self):
        before = self.snapshot(self.root)
        result = self.prepare(plan_only=True)
        self.assertEqual(result["supplements"], 3)
        self.assertEqual(self.plan["source_url"], self.doc["source_url"].rsplit("/", 1)[0] + "/exhibit13.htm")
        self.assertEqual([(r["item"], r["page_start"], r["page_end"]) for r in self.plan["references"]], [("7", 6, 7), ("8", 10, 15)])
        self.assertEqual(before, self.snapshot(self.root))
        self.fetch.assert_not_called()

    def test_external_cross_filing_query_and_fragment_links_are_rejected_before_network(self):
        for link in ("https://example.invalid/file.htm", "../../elsewhere/file.htm", "file.htm?x=1", "file.htm#anchor", self.doc["source_url"]):
            with self.subTest(link=link), self.assertRaises(passages.PassageError):
                self.changed_plan(lambda raw: raw.replace(b"exhibit13.htm", link.encode()))
        self.fetch.assert_not_called()

    def test_exhibit_link_must_be_in_item15_and_correct_exhibit_row(self):
        for a, b in ((b"Item 15. Exhibits", b"Item 14. Principal Accounting Fees"), (b"<td>(13)</td>", b"<td>(14)</td>"), (b'<a href=', b'<a hidden href=')):
            with self.subTest(a=a), self.assertRaises(passages.PassageError):
                self.changed_plan(lambda raw: raw.replace(a, b))

    def test_duplicate_exhibit_link_fails(self):
        link = b'<a href="exhibit13.htm">Annual Report to Security Holders</a>'
        with self.assertRaises(passages.PassageError):
            self.changed_plan(lambda raw: raw.replace(link, link + link))

    def test_wrong_year_and_conflicting_page_ranges_fail(self):
        for a, b in ((b"2023 Annual Report", b"2024 Annual Report"), (b"6 through 7", b"6 through 11"), (b"6 through 7", b"0 through 7"), (b"6 through 7", b"6 and 7")):
            with self.subTest(b=b), self.assertRaises(passages.PassageError):
                self.changed_plan(lambda raw: raw.replace(a, b))

    def test_primary_fingerprint_and_cik_are_checked(self):
        with self.assertRaisesRegex(passages.PassageError, "fingerprint"):
            supplements.supplement_plan(self.doc, self.primary + b" ")
        with self.assertRaisesRegex(passages.PassageError, "CIK"):
            supplements.supplement_plan({**self.doc, "cik": "1"}, self.primary)

    def test_wrong_year_company_and_non_html_exhibits_fail(self):
        for raw in (exhibit(2024), exhibit(2023).replace(b"International Business Machines", b"Another Company"), b"blocked", b"", exhibit(2023) + b"\xff"):
            with self.subTest(size=len(raw)), self.assertRaises(passages.PassageError):
                supplements._report_pages(raw, self.plan)

    def test_missing_duplicate_or_reordered_printed_page_fails(self):
        for a, b in ((b"7 Management Discussion", b"8 Management Discussion"), (b"7 Management Discussion", b"6 Management Discussion"), (b"7 Management Discussion", b"Bad header")):
            with self.subTest(b=b), self.assertRaisesRegex(passages.PassageError, "page sequence"):
                supplements._report_pages(exhibit(2023).replace(a, b), self.plan)

    def test_reference_page_boundaries_must_match_financial_section_titles(self):
        plan = deepcopy(self.plan)
        plan["references"][1]["page_start"] = 9
        with self.assertRaisesRegex(passages.PassageError, "page headings disagree"):
            supplements._report_pages(exhibit(2023), plan)

    def test_download_cache_and_verify_are_resumable_and_verification_is_read_only(self):
        originals = {k: v for k, v in self.snapshot(self.corpus).items()}
        self.assertEqual(self.prepare()["downloaded"], 3)
        self.assertEqual(self.fetch.call_count, 3)
        before = self.snapshot(self.corpus)
        self.assertEqual(self.prepare()["cached"], 3)
        self.assertEqual(self.fetch.call_count, 3)
        # Preparation may republish an identical manifest; verification must write nothing.
        before = self.snapshot(self.corpus)
        self.assertEqual(self.prepare(verify_only=True)["verified_supplements"], 3)
        self.assertEqual(before, self.snapshot(self.corpus))
        self.assertTrue(all(self.snapshot(self.corpus)[k] == v for k, v in originals.items()))

    def test_missing_supplement_blocks_ibm_passage_manifest_publication(self):
        with self.assertRaisesRegex(passages.PassageError, "prepare_filing_supplements"):
            self.passages()
        self.assertFalse((self.output / "manifest.json").exists())
        self.fetch.assert_not_called()

    def test_extra_passages_use_exhibit_source_pages_and_keep_original_ids(self):
        self.prepare()
        base = passages.extract_passages(self.primary, self.doc)
        merged = supplements.include_supplement(base, self.primary, corpus_dir=self.corpus)
        self.assertEqual(base["passages"], merged["passages"][:len(base["passages"])])
        extra = merged["passages"][len(base["passages"]):]
        self.assertTrue(extra)
        self.assertFalse(passages._coverage_warnings(merged))
        ids = [p["passage_id"] for p in merged["passages"]]
        self.assertEqual(len(ids), len(set(ids)))
        for p in extra:
            self.assertEqual(p["source_url"], self.plan["source_url"])
            self.assertEqual(p["parent_source_sha256"], self.doc["source_sha256"])
            self.assertEqual(p["item_assignment"], "explicit_exhibit_page_reference")
            self.assertTrue(p["untrusted_source"])
            page = p["source_spans"][0]["printed_page"]
            self.assertIn(page, (6, 7) if p["item"] == "7" else range(10, 16))
            if page == 10:
                self.assertEqual(p["source_page_title"], "Consolidated Income Statement")
        table = next(p for p in extra if p["kind"] == "table")
        self.assertEqual(table["table"]["rows"][1][1]["text"], "100")
        self.assertEqual(table["table"]["rows"][0][1]["is_header"], True)
        self.assertNotIn("Page 16 content", " ".join(p["text"] for p in extra))

    def test_preparation_and_replay_resolve_all_six_warnings_offline(self):
        self.prepare()
        result = self.passages(tickers=("IBM", "MSFT"))
        self.assertEqual(result["verified_company_years"], 6)
        self.assertFalse(result["coverage_review_required"])
        before = self.snapshot(self.root)
        self.assertFalse(self.passages(tickers=("IBM", "MSFT"), verify_only=True)["coverage_warnings"])
        self.assertEqual(before, self.snapshot(self.root))

    def test_legacy_ibm_outputs_require_explicit_refresh_and_other_companies_unchanged(self):
        self.passages(tickers=("MSFT",))
        msft_manifest = json.loads((self.output / "manifest.json").read_bytes())
        msft_objects = self.snapshot(self.output / "objects")
        # Represent an output created before supplement support existed.
        with patch("filing_supplements.include_supplement", side_effect=lambda v, r, **kw: v):
            self.passages()
        self.prepare()
        with self.assertRaisesRegex(passages.PassageError, "--refresh"):
            self.passages()
        self.passages(refresh=True)
        self.assertFalse(self.passages(tickers=("IBM", "MSFT"), verify_only=True)["coverage_warnings"])
        rows = json.loads((self.output / "manifest.json").read_bytes())["documents"]
        self.assertEqual([r for r in rows if r["ticker"] == "MSFT"], msft_manifest["documents"])
        self.assertTrue(all(self.snapshot(self.output / "objects")[k] == v for k, v in msft_objects.items()))

    def test_modified_supplement_fails_verification_and_refresh_repairs(self):
        self.prepare()
        self.passages()
        record = self.saved_record()
        path = self.corpus / record["html_path"]
        path.write_bytes(path.read_bytes() + b" ")
        for action in (lambda: self.prepare(verify_only=True), lambda: self.passages(verify_only=True)):
            with self.assertRaisesRegex(passages.PassageError, "fingerprint"):
                action()
        self.prepare(refresh=True)
        self.passages(refresh=True)
        self.assertFalse(self.passages(verify_only=True)["coverage_warnings"])

    def test_receipt_provenance_and_path_tampering_are_rejected(self):
        self.prepare()
        record = self.saved_record()
        for key, value in (("html_path", "../../secret.html"), ("plan", {}), ("source_sha256", "../../secret")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                supplements._read(self.corpus / "supplements", {**record, key: value}, self.plan)

    def test_duplicate_manifest_records_are_rejected(self):
        self.prepare()
        path = self.corpus / "supplements/manifest.json"
        value = json.loads(path.read_bytes())
        value["documents"].append(value["documents"][0])
        path.write_bytes(corpus._json_bytes(value))
        with self.assertRaisesRegex(passages.PassageError, "Duplicate"):
            self.prepare(verify_only=True)

    def test_failed_download_preserves_manifest_and_retry_uses_completed_receipts(self):
        self.prepare(years=(2023,))
        before = (self.corpus / "supplements/manifest.json").read_bytes()
        def fail_last(url, ua):
            if url == list(self.web)[-1]:
                raise OSError("simulated interruption")
            return self.web[url]
        self.fetch.side_effect = fail_last
        with self.assertRaises(OSError):
            self.prepare()
        self.assertEqual(before, (self.corpus / "supplements/manifest.json").read_bytes())
        self.fetch.side_effect = lambda url, ua: self.web[url]
        self.fetch.reset_mock()
        result = self.prepare()
        self.assertEqual((result["cached"], result["downloaded"], self.fetch.call_count), (2, 1, 1))

    def test_changed_primary_stops_publication(self):
        original = supplements._load_sources
        calls = 0
        def changed(*args):
            nonlocal calls
            calls += 1
            rows = original(*args)
            return rows if calls == 1 else rows[:-1]
        with patch("filing_supplements._load_sources", side_effect=changed):
            with self.assertRaisesRegex(passages.PassageError, "Primary corpus changed"):
                self.prepare()
        self.assertFalse((self.corpus / "supplements/manifest.json").exists())

    def test_supplement_change_during_extraction_stops_publication(self):
        self.prepare()
        self.passages()
        before = (self.output / "manifest.json").read_bytes()
        def mutate(*args):
            record = self.saved_record()
            p = self.corpus / record["html_path"]
            p.write_bytes(p.read_bytes() + b" ")
        with self.assertRaisesRegex(passages.PassageError, "fingerprint"):
            self.passages(progress=mutate)
        self.assertEqual(before, (self.output / "manifest.json").read_bytes())

    def test_cli_plan_prepare_verify_and_error(self):
        args = ["IBM", "--years", "2023", "2024", "2025", "--data-dir", str(self.data), "--corpus-dir", str(self.corpus)]
        with redirect_stdout(StringIO()):
            self.assertEqual(cli(args + ["--plan-only"]), 0)
            self.assertEqual(cli(args + ["--verify-only"]), 1)
            self.assertEqual(cli(args), 0)
            self.assertEqual(cli(args + ["--verify-only"]), 0)

    def test_unknown_company_selection_never_downloads(self):
        with self.assertRaises(ValueError):
            supplements.prepare_supplements(["UNKNOWN"], [2023], **self.options)
        self.fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
