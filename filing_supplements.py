"""Acquire and map IBM's explicitly incorporated Exhibit 13 (Items 7 and 8).

Original 10-Ks are never rewritten. The primary filing's Exhibit 13 link and
page references establish provenance; the exhibit's company, year and printed
page headings corroborate it. Unsupported references fail, never get guessed.
"""

from bisect import bisect_right
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urljoin

from company_registry import DATA_DIR
from download_company_facts import _atomic_write
from filing_corpus import DEFAULT_CORPUS_DIR, MAX_HTML_BYTES, _json_bytes, _object_path, digest, fetch_html
from filing_passages import (META_KEYS, PARSER_VERSION, PassageError, _DOM, _blocks,
    _coverage_warnings, _load_sources, _location, _nodes, extract_passages)

POLICY = "ibm_explicit_exhibit13_pages_v1"
COMPANY = "International Business Machines Corporation and Subsidiary Companies"
FINANCIAL_TITLES = {
    "Consolidated Income Statement", "Consolidated Statement of Comprehensive Income",
    "Consolidated Balance Sheet", "Consolidated Statement of Cash Flows",
    "Consolidated Statement of Equity", "Notes to the Consolidated Financial Statements",
}
PAGE_TITLES = FINANCIAL_TITLES | {"Management Discussion", "Report of Management",
    "Report of Independent Registered Public Accounting Firm", "Performance Graphs", "Stockholder Information"}


def supplement_plan(document, raw, value=None):
    """Resolve only observed, explicit IBM financial-report references."""
    if digest(raw) != document["source_sha256"]:
        raise PassageError("Primary source fingerprint changed before supplement planning.")
    value = value or extract_passages(raw, document)
    if document["ticker"] != "IBM" or not _coverage_warnings(value):
        return None
    if str(document["cik"]).lstrip("0") != "51143":
        raise PassageError("IBM supplement requires IBM's verified CIK.")
    refs = []
    for item in ("7", "8"):
        selected = [p for p in value["passages"] if p["item"] == item]
        text = " ".join(p["text"] for p in selected)
        match = re.fullmatch(
            r"Refer to pages (\d+) through (\d+) of IBM[’']s (\d{4}) Annual Report to Stockholders, "
            r"which are incorporated herein by reference\."
            r"(?: Also refer to the Financial Statement Schedule on page S-1 of this Form 10-K\.)?", text)
        if not match or int(match[3]) != document["fiscal_year"]:
            raise PassageError(f"Unsupported or conflicting IBM Item {item} annual-report reference.")
        first, last = int(match[1]), int(match[2])
        if not 1 <= first <= last <= 500:
            raise PassageError("Invalid incorporated page range.")
        refs.append({"item": item, "page_start": first, "page_end": last, "text": text,
            "source_url": document["source_url"], "source_sha256": document["source_sha256"],
            "html_path": document["html_path"],
            "source_spans": [s for p in selected for s in p["source_spans"]]})
    if refs[0]["page_end"] >= refs[1]["page_start"]:
        raise PassageError("Overlapping incorporated Item page ranges.")
    dom = _DOM(raw.decode("utf-8-sig"))
    sections = value["sections"]
    section = next((s for s in sections if s["item"] == "15"), None)
    if section is None:
        raise PassageError("Missing Item 15 Exhibit 13 provenance.")
    start = section["heading_source"]["html_char_end"]
    end = min((s["heading_source"]["html_char_start"] for s in sections
               if s["heading_source"]["html_char_start"] > start), default=len(dom.source))
    links = []
    for node in _nodes(dom.root):
        if (node.tag != "tr" or node.hidden or not start <= node.start < end
                or not re.fullmatch(r"\(13\) Annual Report to Security Holders(?: \(\d+\))? 13", node.text)):
            continue
        links.extend(n for n in _nodes(node) if n.tag == "a" and not n.hidden
                     and n.text == "Annual Report to Security Holders" and n.attrs.get("href"))
    if len(links) != 1:
        raise PassageError("Expected one explicit Exhibit 13 link inside Item 15.")
    prefix = ("https://www.sec.gov/Archives/edgar/data/51143/"
              + document["filing_identifier"].replace("-", "") + "/")
    if not document["source_url"].startswith(prefix):
        raise PassageError("Primary IBM URL does not match its filing accession.")
    target = urljoin(document["source_url"], links[0].attrs["href"])
    if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9][A-Za-z0-9_-]*\.html?", target):
        raise PassageError("Exhibit 13 must be an HTML document in the same SEC filing.")
    if target == document["source_url"]:
        raise PassageError("Exhibit 13 cannot point back to the primary document.")
    return {"policy": POLICY, "ticker": "IBM", "fiscal_year": document["fiscal_year"],
        "filing_identifier": document["filing_identifier"], "source_url": target,
        "parent_source_url": document["source_url"], "parent_source_sha256": document["source_sha256"],
        "parent_html_path": document["html_path"], "references": refs,
        "exhibit_link_source": _location(links[0])}


def _report_pages(raw, plan):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_HTML_BYTES:
        raise PassageError("Missing or oversized Exhibit 13 HTML.")
    try:
        text = raw.decode("utf-8-sig")
        if not re.search(r"<html(?:\s|>)", text, re.I):
            raise PassageError("Exhibit 13 is not HTML.")
        dom = _DOM(text)
    except (UnicodeError, RecursionError):
        raise PassageError("Unsupported Exhibit 13 encoding or structure.") from None
    pages = []
    for node in _nodes(dom.root):
        if node.tag != "table" or node.hidden or len(node.text) > 240:
            continue
        if node.text == f"Report of Financials {COMPANY} Exhibit 13 5":
            page, title = 5, "Report of Financials"
        else:
            m = re.fullmatch(r"(?:(\d+) (.+) " + re.escape(COMPANY)
                + r"|(.+) " + re.escape(COMPANY) + r" (\d+))", node.text)
            if not m:
                continue
            page, title = int(m[1] or m[4]), m[2] or m[3]
            if title not in PAGE_TITLES:
                raise PassageError("Unsupported Exhibit 13 printed page heading.")
        pages.append({"page": page, "title": title, "source": _location(node)})
    if (len(pages) < 10 or pages[0]["title"] != "Report of Financials"
            or [p["page"] for p in pages] != list(range(5, 5 + len(pages)))
            or pages[-1]["title"] != "Stockholder Information"):
        raise PassageError("Exhibit 13 page sequence is missing, duplicated or out of order.")
    overviews = [n.text for n in _nodes(dom.root) if not n.hidden and not n.contains_blocks
        and n.text.startswith("The financial section of the International Business Machines Corporation")]
    years = [re.search(r"\b(\d{4}) Annual Report includes the Management Discussion", t) for t in overviews]
    if not years or any(m is None or int(m[1]) != plan["fiscal_year"] for m in years):
        raise PassageError("Exhibit 13 company/year identity could not be corroborated.")
    by_page = {p["page"]: p for p in pages}
    for ref in plan["references"]:
        selected = [by_page.get(p) for p in range(ref["page_start"], ref["page_end"] + 1)]
        if not all(selected):
            raise PassageError("An incorporated page is missing from Exhibit 13.")
        titles = {p["title"] for p in selected}
        following = by_page.get(ref["page_end"] + 1, {}).get("title")
        if ref["item"] == "7":
            valid = ref["page_start"] == 6 and titles == {"Management Discussion"} and following == "Report of Management"
        else:
            valid = (selected[0]["title"] == "Consolidated Income Statement"
                and titles == FINANCIAL_TITLES and following == "Performance Graphs")
        if not valid:
            raise PassageError(f"Exhibit page headings disagree with the Item {ref['item']} reference.")
    return dom, pages


def _manifest(folder):
    path = folder / "manifest.json"
    if not path.exists():
        return []
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("policy") != POLICY or not isinstance(value.get("documents"), list)):
        raise PassageError("Invalid supplement manifest.")
    seen = set()
    for r in value["documents"]:
        if not isinstance(r, dict) or r.get("ticker") != "IBM" or type(r.get("fiscal_year")) is not int:
            raise PassageError("Invalid supplement manifest record.")
        if r["fiscal_year"] in seen:
            raise PassageError("Duplicate supplement company/year.")
        seen.add(r["fiscal_year"])
    return value["documents"]


def _read(folder, record, plan):
    if not isinstance(record, dict) or record.get("plan") != plan:
        raise PassageError("Supplement provenance differs from its verified primary filing; prepare with --refresh.")
    path = _object_path(folder, record["source_sha256"])
    if record.get("html_path") != "supplements/" + path.relative_to(folder).as_posix():
        raise PassageError("Unexpected supplement HTML path.")
    if not path.exists() or path.stat().st_size > MAX_HTML_BYTES:
        raise PassageError("Missing or oversized saved supplement.")
    raw = path.read_bytes()
    if digest(raw) != record["source_sha256"] or len(raw) != record.get("size_bytes"):
        raise PassageError("Supplement fingerprint or size changed; prepare with --refresh.")
    datetime.fromisoformat(record["downloaded_at"])
    _report_pages(raw, plan)
    return raw


def _plans(tickers, years, data_dir, corpus_dir, as_of):
    sources = _load_sources(tickers, years, data_dir, corpus_dir, as_of)
    plans = [p for doc, raw in sources if (p := supplement_plan(doc, raw)) is not None]
    return sources, plans


def prepare_supplements(tickers, years, user_agent="", *, data_dir=DATA_DIR,
        corpus_dir=DEFAULT_CORPUS_DIR, as_of=None, refresh=False, verify_only=False, plan_only=False):
    """Resumable downloads; verify-only and plan-only never use the network/write."""
    if (refresh and (verify_only or plan_only)) or (verify_only and plan_only):
        raise PassageError("Choose only one supplement mode.")
    sources, plans = _plans(tickers, years, data_dir, corpus_dir, as_of)
    if plan_only:
        return {"status": "ok", "supplements": len(plans), "documents": plans}
    folder = Path(corpus_dir) / "supplements"
    indexed = {r["fiscal_year"]: r for r in _manifest(folder)}
    completed, downloaded, cached = [], 0, 0
    for plan in plans:
        receipt = folder / "receipts" / f"IBM_{plan['fiscal_year']}_{plan['filing_identifier']}.json"
        if verify_only:
            record = indexed.get(plan["fiscal_year"])
            if record is None:
                raise PassageError("Required IBM supplement is missing. Run prepare_filing_supplements.py first.")
            _read(folder, record, plan)
        elif receipt.exists() and not refresh:
            record = json.loads(receipt.read_bytes())
            _read(folder, record, plan)
            cached += 1
        else:
            raw = fetch_html(plan["source_url"], user_agent)
            _report_pages(raw, plan)
            sha = digest(raw)
            record = {"ticker": "IBM", "fiscal_year": plan["fiscal_year"], "plan": plan,
                "source_sha256": sha, "size_bytes": len(raw), "html_path": f"supplements/objects/{sha}.html",
                "downloaded_at": datetime.now(timezone.utc).isoformat()}
            _atomic_write(_object_path(folder, sha), raw)
            _atomic_write(receipt, _json_bytes(record))
            downloaded += 1
        completed.append(record)
    if sources != _load_sources(tickers, years, data_dir, corpus_dir, as_of):
        raise PassageError("Primary corpus changed during supplement preparation; no manifest published.")
    for record in completed:
        _read(folder, record, record["plan"])
    if not verify_only:
        indexed.update({r["fiscal_year"]: r for r in completed})
        _atomic_write(folder / "manifest.json", _json_bytes({"schema_version": 1, "policy": POLICY,
            "documents": [indexed[y] for y in sorted(indexed)]}))
    return {"status": "ok", "verified_supplements": len(completed), "downloaded": downloaded,
        "cached": cached, "items": ["7", "8"] if completed else [], "manifest": str(folder / "manifest.json")}


def _extra_passages(raw, record, document, chunk_words):
    plan = record["plan"]
    dom, pages = _report_pages(raw, plan)
    blocks, _ = _blocks(dom, record["source_sha256"])
    offsets = [p["source"]["html_char_start"] for p in pages]
    page_titles = {p["page"]: p["title"] for p in pages}
    result = []
    meta = {k: document[k] for k in META_KEYS}
    meta.update(source_url=plan["source_url"], source_sha256=record["source_sha256"], html_path=record["html_path"])
    for ref in plan["references"]:
        found_pages = set()
        pending, spans, word_count, context, last_page = [], [], 0, "", None
        def emit(kind, text, locations, table=None):
            value = {**meta, "parser_version": PARSER_VERSION, "supplement_parser_version": POLICY,
                "source_document_type": "EX-13", "parent_source_url": plan["parent_source_url"],
                "parent_source_sha256": plan["parent_source_sha256"], "parent_html_path": plan["parent_html_path"],
                "item": ref["item"], "item_heading": f"Item {ref['item']} (incorporated Exhibit 13)",
                "item_assignment": "explicit_exhibit_page_reference", "item_reference_sources": [ref],
                "exhibit_link_source": plan["exhibit_link_source"], "kind": kind, "text": text,
                "source_page_title": page_titles[locations[0]["printed_page"]],
                "source_spans": locations, "table": table, "context_before": context if table else "",
                "untrusted_source": True}
            value["passage_id"] = "p_" + digest(_json_bytes(value))
            result.append(value)
        def flush():
            nonlocal pending, spans, word_count
            if pending:
                emit("text", "\n\n".join(pending), spans)
            pending, spans, word_count = [], [], 0
        for block_index, block in enumerate(blocks):
            loc = block["source"]
            index = bisect_right(offsets, loc["html_char_start"]) - 1
            if index < 0:
                continue
            page = pages[index]
            number = page["page"]
            if not ref["page_start"] <= number <= ref["page_end"]:
                continue
            if loc == page["source"]:
                continue  # printed page header, not a financial table
            if index + 1 < len(pages) and loc["html_char_end"] > offsets[index + 1]:
                raise PassageError("Exhibit block crosses a printed page boundary; review required.")
            if block["linked"] and block["text"].lower() == "table of contents":
                continue
            if last_page != number:
                flush()
                context = page["title"]
                last_page = number
            found_pages.add(number)
            source = {**loc, "block_index": block_index, "printed_page": number,
                      "page_heading_source": page["source"]}
            if block["kind"] == "table":
                flush()
                emit("table", block["text"], [source], block["table"])
                continue
            tokens = list(re.finditer(r"\S+", block["text"]))
            for offset in range(0, len(tokens), chunk_words):
                group = tokens[offset:offset + chunk_words]
                if pending and word_count + len(group) > chunk_words:
                    flush()
                a, b = group[0].start(), group[-1].end()
                pending.append(block["text"][a:b])
                spans.append({**source, "block_text_start": a, "block_text_end": b})
                word_count += len(group)
            context = block["text"][-600:]
        flush()
        if found_pages != set(range(ref["page_start"], ref["page_end"] + 1)):
            raise PassageError("An incorporated page has no extracted content.")
        if not any(p["kind"] == "table" and p["item"] == ref["item"] for p in result):
            raise PassageError("Incorporated Item has no structured tables.")
    return result


def include_supplement(value, primary_raw, *, corpus_dir):
    """Replay saved exhibits, preserving original passages and their stable IDs."""
    plan = supplement_plan(value["document"], primary_raw, value)
    if plan is None:
        return value
    folder = Path(corpus_dir) / "supplements"
    record = next((r for r in _manifest(folder) if r["fiscal_year"] == plan["fiscal_year"]), None)
    if record is None:
        raise PassageError("IBM Items 7/8 require Exhibit 13. Run prepare_filing_supplements.py IBM --years "
                           + str(plan["fiscal_year"]) + " first, then rebuild IBM passages with --refresh.")
    raw = _read(folder, record, plan)
    extra = _extra_passages(raw, record, value["document"], value["chunk_words"])
    return {**value, "passages": value["passages"] + extra, "supplements": [record]}


def verify_dependencies(value, corpus_dir):
    """Detect saved exhibit/manifest changes during a passage preparation run."""
    records = value.get("supplements", [])
    if records:
        folder = Path(corpus_dir) / "supplements"
        current = {r["fiscal_year"]: r for r in _manifest(folder)}
        for record in records:
            if current.get(record["fiscal_year"]) != record:
                raise PassageError("Supplement manifest changed during extraction.")
            _read(folder, record, record["plan"])
