"""Offline, source-linked 10-K Item and table extraction.

This prepares retrieval inputs. It does not search, call a model, execute HTML,
or verify numerical claims. Original filing bytes remain in the corpus store.
"""

from dataclasses import dataclass, field
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from company_registry import DATA_DIR
from download_company_facts import _atomic_write
from filing_corpus import (DEFAULT_CORPUS_DIR, MAX_HTML_BYTES,
    _json_bytes, _load_manifest, _object_path, _read_document, digest, plan_corpus)

PARSER_VERSION = "items_tables_v1"
DEFAULT_PASSAGES_DIR = DATA_DIR.parent / "passages"
DEFAULT_CHUNK_WORDS = 350
MAX_OUTPUT_BYTES = 80 * 1024 * 1024
VOID = set("area base br col embed hr img input link meta param source track wbr".split())
BLOCK = set("div p section article main header footer h1 h2 h3 h4 h5 h6 li ul ol pre blockquote td th tr".split())
SKIP = {"head", "script", "style", "noscript", "template", "ix:header", "ix:hidden"}
ITEM_ORDER = "1 1A 1B 1C 2 3 4 4A 5 6 7 7A 8 9 9A 9B 9C 10 11 12 13 14 15 16".split()
ITEM_TITLES = {
    "1": r"business", "1A": r"risk factors", "1B": r"unresolved staff comments",
    "1C": r"cybersecurity", "2": r"properties", "3": r"legal proceedings",
    "4": r"mine safety", "4A": r"information about.*executive officers",
    "5": r"market for", "6": r"\[?reserved|selected financial data",
    "7": r"managements discussion", "7A": r"quantitative and qualitative",
    # Comcast places its name before the standard Item 8 title. Keep this
    # observed variant explicit rather than accepting arbitrary prefix text.
    "8": r"(?:comcast corporation )?financial statements", "9": r"changes in and disagreements",
    "9A": r"controls and procedures", "9B": r"other information",
    "9C": r"disclosure regarding foreign jurisdictions", "10": r"directors",
    "11": r"executive compensation", "12": r"security ownership",
    "13": r"certain relationships", "14": r"principal account",
    "15": r"exhibits?", "16": r"form 10.?k summary",
}
META_KEYS = ("ticker", "company", "sector", "cik", "fiscal_year", "period_start",
             "period_end", "filed", "form", "filing_identifier", "source_url", "source_sha256", "html_path")
# Observed same-document reference chain: Item 8 identifies the placement,
# Item 15 confirms ownership, then the financial index starts the appendix.
# A generic mention of financial statements is not enough to assign an Item.
PART_IV_REFERENCE = re.compile(
    r"the consolidated financial statements and accompanying notes listed in part iv, "
    r"item\s+15\s*\(a\)\s*\(1\) of this annual report on form 10[-–]k "
    r"are included immediately following part iv hereof and incorporated by reference herein\.?", re.I)
PART_IV_CONFIRMATION = re.compile(
    r'the financial statements are filed as part of this annual report on form 10[-–]k '
    r'under [“"]item\s+8\.\s*financial statements and supplementary data\.[”"]\.?', re.I)


class PassageError(ValueError):
    """Source parsing or saved passage validation failed."""


def clean(value):
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return " ".join(value.split())


@dataclass
class _Node:
    tag: str
    attrs: dict
    path: str
    start: int
    end: int
    hidden: bool = False
    children: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    text: str = ""
    raw_text: str = ""
    contains_blocks: bool = False


class _DOM(HTMLParser):
    """Small bounded HTML tree; never loads URLs, CSS, scripts or XML entities."""
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.lines = [0]
        for match in re.finditer("\n", source):
            self.lines.append(match.end())
        self.root = _Node("root", {}, "", 0, len(source))
        self.stack = [self.root]
        self.count = 0
        self.feed(source)
        self.close()
        self._finish(self.root)

    def source_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 500000 or len(self.stack) > 256:
            raise PassageError("HTML exceeds the supported node/depth limit.")
        attrs = {key: value or "" for key, value in attrs}
        parent = self.stack[-1]
        parent.counts[tag] = parent.counts.get(tag, 0) + 1
        hidden = parent.hidden or tag in SKIP or "hidden" in attrs or attrs.get("aria-hidden") == "true"
        hidden = hidden or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)\b", attrs.get("style") or "", re.I))
        node = _Node(tag, attrs, f"{parent.path}/{tag}[{parent.counts[tag]}]",
                     self.source_offset(), len(self.source), hidden)
        parent.children.append(node)
        if tag in VOID:
            node.end = node.start + len(self.get_starttag_text())
        else:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                end = self.source.find(">", self.source_offset())
                for node in self.stack[i:]:
                    node.end = len(self.source) if end < 0 else end + 1
                del self.stack[i:]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)

    def _finish(self, node):
        parts = []
        for child in node.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                self._finish(child)
                node.contains_blocks |= child.tag in BLOCK or child.tag == "table" or child.contains_blocks
                if child.tag in BLOCK or child.tag in ("table", "br"):
                    parts.extend((" ", child.raw_text, " "))
                else:
                    parts.append(child.raw_text)
        node.raw_text = "" if node.hidden else "".join(parts)
        node.text = clean(node.raw_text)


def _nodes(node):
    yield node
    for child in node.children:
        if isinstance(child, _Node):
            yield from _nodes(child)


def _heading(text):
    if len(text) > 240:
        return None
    match = re.fullmatch(r"item\s+(\d{1,2}[A-C]?)\s*[.\-–—:]?\s*(.*)", text, re.I)
    if not match:
        return None
    item, title = match[1].upper(), match[2]
    normal = title.lower().replace("’", "").replace("'", "")
    if item not in ITEM_TITLES or not re.match(ITEM_TITLES[item], normal):
        return None
    if re.search(r"\b(?:see also|refer to|of this report|of this form|on page|above|below)\b", normal):
        return None
    return item


def _location(node):
    return {"dom_path": node.path, "html_char_start": node.start, "html_char_end": node.end}


def _table(node, source_sha, depth=0):
    if depth > 4:
        raise PassageError("Table nesting exceeds the supported depth.")
    table_id = "t_" + digest((source_sha + "|" + node.path).encode())
    row_nodes = []
    def collect(current):
        for child in current.children:
            if not isinstance(child, _Node) or child.hidden or child.tag == "table":
                continue
            if child.tag == "tr":
                row_nodes.append(child)
            else:
                collect(child)
    collect(node)
    occupied, rows = {}, []
    for r, row in enumerate(row_nodes):
        cells, col = [], 0
        for cell in row.children:
            if not isinstance(cell, _Node) or cell.hidden or cell.tag not in ("td", "th"):
                continue
            while occupied.get((r, col)):
                col += 1
            try:
                colspan = int(cell.attrs.get("colspan", "1"))
                rowspan = int(cell.attrs.get("rowspan", "1"))
                if rowspan == 0:
                    rowspan = len(row_nodes) - r
                if not 1 <= colspan <= 1000 or not 1 <= rowspan <= 1000:
                    raise ValueError
            except (ValueError, TypeError):
                raise PassageError("Invalid or unsupported table cell span.") from None
            for rr in range(r, r + rowspan):
                for cc in range(col, col + colspan):
                    if cc >= 1000 or len(occupied) >= 200000:
                        raise PassageError("Table exceeds the supported grid limit.")
                    if occupied.get((rr, cc)):
                        raise PassageError("Overlapping table cells.")
                    occupied[rr, cc] = True
            nested = []
            def inner(current):
                for child in current.children:
                    if isinstance(child, _Node) and not child.hidden:
                        if child.tag == "table":
                            nested.append(_table(child, source_sha, depth + 1))
                        else:
                            inner(child)
            inner(cell)
            cells.append({"cell_id": f"{table_id}:r{r}:c{col}", "row": r, "column": col,
                          "rowspan": rowspan, "colspan": colspan, "is_header": cell.tag == "th",
                          "text": cell.text, "source": _location(cell), "nested_tables": nested})
            col += colspan
        if not cells and row.text:
            raise PassageError("Table row has text but no readable cells.")
        rows.append(cells)
    if not rows and node.text:
        raise PassageError("Table has text but no readable rows.")
    caption = next((child.text for child in node.children if isinstance(child, _Node) and child.tag == "caption"), "")
    return {"table_id": table_id, "caption": caption, "rows": rows,
            "source": _location(node), "column_count": max((c + 1 for _, c in occupied), default=0)}


def _blocks(dom, source_sha):
    blocks, stats = [], {"excluded_toc_tables": 0, "excluded_empty_or_page_blocks": 0}
    def emit(text, node, kind="text", table=None):
        text = clean(text)
        if not text or re.fullmatch(r"(?:\d{1,4}|table of contents|part [ivx]+)", text, re.I):
            stats["excluded_empty_or_page_blocks"] += 1
            return
        links = [n for n in _nodes(node) if n.tag == "a" and n.attrs.get("href") and not n.hidden]
        linked_chars = sum(len(n.text) for n in links)
        blocks.append({"kind": kind, "text": text, "source": _location(node), "table": table,
                       "linked": bool(links) and linked_chars >= len(text) * .8})
    def walk(node):
        if node.hidden:
            return
        if node.tag == "table":
            text = node.text
            links = sum(n.tag == "a" and str(n.attrs.get("href", "")).startswith("#") for n in _nodes(node))
            item_count = len(re.findall(r"\bitem\s+\d+[A-C]?\b", text, re.I))
            if links >= 3 and item_count >= 3 and len(text.split()) <= 1200:
                stats["excluded_toc_tables"] += 1
            elif _heading(text):
                emit(text, node)
            elif text:
                emit(text, node, "table", _table(node, source_sha))
            return
        buffer = []
        def flush():
            if buffer:
                emit("".join(buffer), node)
                buffer.clear()
        for child in node.children:
            if isinstance(child, str):
                buffer.append(child)
            elif child.hidden:
                continue
            elif child.tag in BLOCK or child.tag == "table" or child.contains_blocks:
                flush()
                walk(child)
            elif child.tag == "br":
                buffer.append(" ")
            else:
                buffer.append(child.raw_text)
        flush()
    walk(dom.root)
    # Some publishers put 'Item 1.' and 'Business' in adjacent paragraphs.
    merged, i = [], 0
    while i < len(blocks):
        block = blocks[i]
        if block["kind"] == "text" and re.fullmatch(r"item\s+\d+[A-C]?\s*\.?", block["text"], re.I) and i + 1 < len(blocks):
            following = blocks[i + 1]
            joined = block["text"] + " " + following["text"]
            if following["kind"] == "text" and _heading(joined):
                block = {**block, "text": joined, "source": {**block["source"],
                    "html_char_end": following["source"]["html_char_end"]}, "linked": block["linked"] or following["linked"]}
                i += 1
        if block["kind"] == "text" and re.fullmatch(r"item\s+\d+[A-C]?\s*\.?", block["text"], re.I):
            stats["excluded_empty_or_page_blocks"] += 1
        else:
            merged.append(block)
        i += 1
    return merged, stats


def extract_passages(raw, document, *, chunk_words=DEFAULT_CHUNK_WORDS):
    """Return deterministic passages and structured table cells from one report."""
    if type(chunk_words) is not int or not 50 <= chunk_words <= 1000:
        raise PassageError("chunk_words must be an integer from 50 to 1000.")
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_HTML_BYTES:
        raise PassageError("Missing or oversized source HTML.")
    if digest(raw) != document["source_sha256"]:
        raise PassageError("Source HTML fingerprint differs from corpus metadata.")
    try:
        dom = _DOM(raw.decode("utf-8-sig"))
    except (UnicodeError, RecursionError) as exc:
        raise PassageError(f"Unsupported HTML ({type(exc).__name__}).") from None
    blocks, stats = _blocks(dom, document["source_sha256"])
    candidates = {i: _heading(b["text"]) for i, b in enumerate(blocks)
                  if b["kind"] == "text" and not b["linked"] and _heading(b["text"])}
    starts = [i for i, item in candidates.items() if item == "1"]
    if not starts:
        raise PassageError("Cannot identify the body heading for Item 1 (Business).")
    # Select the Item 1 run with the most content, avoiding plain-text TOCs.
    runs = [(sum(len(b["text"]) for b in blocks[start:(starts[n + 1] if n + 1 < len(starts) else len(blocks))]), start)
            for n, start in enumerate(starts)]
    start = max(runs)[1]
    stats["excluded_preamble_blocks"] = start
    sections, selected, current = [], [], None
    index_pattern = r"index to (?:consolidated )?financial statements(?: and supplement(?:al|ary) data)?"
    references = []
    part_iv_reference, part_iv_confirmations = None, []
    last_numbered = None
    for i in range(start, len(blocks)):
        block = blocks[i]
        item = candidates.get(i)
        if item:
            if current and item == current["item"]:
                continue  # repeated page heading within the same Item
            if last_numbered and ITEM_ORDER.index(item) <= ITEM_ORDER.index(last_numbered):
                raise PassageError(f"Ambiguous Item order: {last_numbered} then {item}.")
            last_numbered = item
            current = {"item": item, "heading": block["text"], "heading_source": block["source"],
                       "assignment": "explicit_item_heading", "reference_sources": []}
            sections.append(current)
            continue
        if current and current["item"] == "8":
            match = re.search(r"\bsee\s+(" + index_pattern + r")\s+on page\s+\d+\.?$", block["text"], re.I)
            if match:
                references.append({"target_heading": match[1].lower(), "text": block["text"], "source": block["source"]})
            if PART_IV_REFERENCE.fullmatch(block["text"]):
                part_iv_reference = {"target_heading": "index to financial statements",
                                     "text": block["text"], "source": block["source"]}
        if current and current["item"] == "15" and PART_IV_CONFIRMATION.fullmatch(block["text"]):
            part_iv_confirmations.append({"text": block["text"], "source": block["source"]})
        if last_numbered in ("15", "16") and block["text"].lower() == "signatures":
            # Preserve post-Item material without falsely labelling it Item 16.
            current = {"item": None, "heading": "Signatures and appended material",
                       "heading_source": block["source"], "assignment": "unassigned_post_item",
                       "reference_sources": []}
            sections.append(current)
        # Linked page-navigation text (as in Oracle) is not a new appendix.
        if last_numbered in ("15", "16") and not block["linked"] and re.fullmatch(index_pattern, block["text"], re.I):
            matches = [ref for ref in references if ref["target_heading"] == block["text"].lower()]
            assignment = "explicit_index_reference"
            if not matches and part_iv_reference and part_iv_confirmations and block["text"].lower() == part_iv_reference["target_heading"]:
                matches = [{**part_iv_reference, "corroborating_item15_sources": part_iv_confirmations.copy()}]
                assignment = "explicit_part_iv_reference"
            if not matches:
                raise PassageError("Appended financial index has no matching explicit Item 8 reference; review required.")
            current = {"item": "8", "heading": "Item 8. Financial statements (referenced appendix)",
                       "heading_source": block["source"], "assignment": assignment,
                       "reference_sources": matches}
            sections.append(current)
        if current:
            selected.append((current, block, i))
    missing = {"1", "1A", "7", "8"} - {s["item"] for s in sections}
    if missing:
        raise PassageError("Required body Items not identified: " + ", ".join(sorted(missing)) + ".")
    meta = {key: document[key] for key in META_KEYS}
    passages, pending, spans, words, pending_section = [], [], [], 0, None
    context_before = ""
    def publish(kind, text, section, sources, table=None, context=""):
        value = {**meta, "parser_version": PARSER_VERSION, "item": section["item"],
                 "item_heading": section["heading"], "kind": kind, "text": text,
                 "source_spans": sources, "table": table, "context_before": context,
                 "item_assignment": section["assignment"], "item_reference_sources": section["reference_sources"],
                 "untrusted_source": True}
        value["passage_id"] = "p_" + digest(_json_bytes(value))
        passages.append(value)
    def flush():
        nonlocal pending, spans, words, pending_section
        if pending:
            publish("text", "\n\n".join(pending), pending_section, spans)
        pending, spans, words, pending_section = [], [], 0, None
    last_item = None
    for section, block, block_index in selected:
        if section["item"] != last_item:
            flush()
            context_before = ""
            last_item = section["item"]
        if block["kind"] == "table":
            flush()
            publish("table", block["text"], section,
                    [{**block["source"], "block_index": block_index}], block["table"], context_before)
            continue
        text = block["text"]
        tokens = list(re.finditer(r"\S+", text))
        for offset in range(0, len(tokens), chunk_words):
            group = tokens[offset:offset + chunk_words]
            if pending and words + len(group) > chunk_words:
                flush()
            a, b = group[0].start(), group[-1].end()
            pending.append(text[a:b])
            spans.append({**block["source"], "block_index": block_index, "block_text_start": a, "block_text_end": b})
            words += len(group)
            pending_section = section
        context_before = text[-600:]
    flush()
    for item in ("1", "1A", "7", "8"):
        if not any(p["item"] == item for p in passages):
            raise PassageError(f"Item {item} has no extracted content.")
    if not any(p["kind"] == "table" for p in passages):
        raise PassageError("No body tables identified; review the filing format.")
    return {"schema_version": 1, "parser_version": PARSER_VERSION, "chunk_words": chunk_words,
            "document": meta, "sections": sections, "passages": passages, "extraction_stats": stats}


def _summary(value):
    passages = value["passages"]
    return {"passages": len(passages), "text_passages": sum(p["kind"] == "text" for p in passages),
            "table_passages": sum(p["kind"] == "table" for p in passages),
            "items": list(dict.fromkeys(s["item"] for s in value["sections"] if s["item"] is not None)),
            "unassigned_passages": sum(p["item"] is None for p in passages)}


def _coverage_warnings(value):
    """Flag short Items that point to material outside the loaded HTML.

    This is a targeted coverage check, not a complete incorporation resolver.
    Warnings are run-summary metadata; they do not change saved passage IDs.
    """
    warnings = []
    for item in ("7", "8"):
        selected = [p for p in value["passages"] if p["item"] == item]
        text = " ".join(p["text"] for p in selected)
        if (len(text.split()) < 150 and not any(p["kind"] == "table" for p in selected)
                and re.search(r"annual report to (?:stockholders|shareholders)", text, re.I)
                and re.search(r"incorporated .*?by reference", text, re.I)):
            warnings.append({"ticker": value["document"]["ticker"],
                "fiscal_year": value["document"]["fiscal_year"], "item": item,
                "reason": "This short Item refers to a separate annual report. That referenced material is not loaded by this command."})
    return warnings


def _load_sources(tickers, years, data_dir, corpus_dir, as_of):
    plan = plan_corpus(tickers, years, data_dir=data_dir, as_of=as_of)
    records = {(r["ticker"], r["fiscal_year"]): r for r in _load_manifest(corpus_dir)}
    selected = []
    for expected in plan["documents"]:
        key = expected["ticker"], expected["fiscal_year"]
        if key not in records:
            raise PassageError(f"{key[0]} FY{key[1]} is missing from the verified filing corpus.")
        record = _read_document(corpus_dir, records[key], expected)
        raw = _object_path(corpus_dir, record["source_sha256"]).read_bytes()
        if digest(raw) != record["source_sha256"]:
            raise PassageError("Source changed while loading it.")
        selected.append((record, raw))
    return selected


def _manifest(folder):
    path = Path(folder) / "manifest.json"
    if not path.exists():
        return []
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("parser_version") != PARSER_VERSION or not isinstance(value.get("documents"), list):
        raise PassageError("Unsupported passage manifest.")
    seen = set()
    for row in value["documents"]:
        if not isinstance(row, dict) or not isinstance(row.get("ticker"), str) or type(row.get("fiscal_year")) is not int:
            raise PassageError("Invalid passage manifest record.")
        pair = row["ticker"], row["fiscal_year"]
        if pair in seen:
            raise PassageError("Duplicate company/year in passage manifest.")
        seen.add(pair)
    return value["documents"]


def _output_path(folder, sha):
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise PassageError("Invalid passage-object fingerprint.")
    return Path(folder) / "objects" / (sha + ".json")


def _receipt(record, expected_bytes, value, folder):
    if not isinstance(record, dict):
        raise PassageError("Invalid passage receipt.")
    expected = _record(value, expected_bytes)
    if record != expected:
        raise PassageError("Passage metadata or parser settings changed; rebuild with --refresh.")
    path = _output_path(folder, record["output_sha256"])
    if not path.exists() or path.stat().st_size > MAX_OUTPUT_BYTES or path.read_bytes() != expected_bytes:
        raise PassageError("Saved passages differ from their verified source; rebuild with --refresh.")


def _record(value, raw):
    doc = value["document"]
    sha = digest(raw)
    return {"ticker": doc["ticker"], "fiscal_year": doc["fiscal_year"],
            "filing_identifier": doc["filing_identifier"], "source_sha256": doc["source_sha256"],
            "parser_version": PARSER_VERSION, "chunk_words": value["chunk_words"],
            "output_sha256": sha, "output_path": f"objects/{sha}.json", **_summary(value)}


def prepare_passages(tickers, years, *, data_dir=DATA_DIR, corpus_dir=DEFAULT_CORPUS_DIR,
                     passages_dir=DEFAULT_PASSAGES_DIR, as_of=None, chunk_words=DEFAULT_CHUNK_WORDS,
                     refresh=False, verify_only=False, progress=None):
    """Build or replay-verify passages; publish a manifest only after all succeed."""
    if refresh and verify_only:
        raise PassageError("Cannot refresh while verifying.")
    folder = Path(passages_dir)
    previous = _manifest(folder)
    sources = _load_sources(tickers, years, data_dir, corpus_dir, as_of)
    indexed = {(r["ticker"], r["fiscal_year"]): r for r in previous}
    completed, built, cached, coverage_warnings, dependencies = [], 0, 0, [], []
    from filing_supplements import include_supplement, verify_dependencies
    for document, raw in sources:
        label = f"{document['ticker']} FY{document['fiscal_year']}"
        try:
            value = extract_passages(raw, document, chunk_words=chunk_words)
            value = include_supplement(value, raw, corpus_dir=corpus_dir)
            if value.get("supplements"):
                dependencies.append({"supplements": value["supplements"]})
            coverage_warnings.extend(_coverage_warnings(value))
            encoded = _json_bytes(value)
            if len(encoded) > MAX_OUTPUT_BYTES:
                raise PassageError("Extracted document exceeds the 80 MiB limit.")
            record = _record(value, encoded)
            receipt_path = folder / "receipts" / f"{document['ticker']}_{document['fiscal_year']}_{document['filing_identifier']}.json"
            key = document["ticker"], document["fiscal_year"]
            if verify_only:
                if key not in indexed:
                    raise PassageError("Company/year is missing from the passage manifest.")
                _receipt(indexed[key], encoded, value, folder)
                action = "verified"
            elif receipt_path.exists() and not refresh:
                _receipt(json.loads(receipt_path.read_bytes()), encoded, value, folder)
                cached += 1
                action = "cached and verified"
            else:
                _atomic_write(_output_path(folder, record["output_sha256"]), encoded)
                _atomic_write(receipt_path, _json_bytes(record))
                built += 1
                action = "extracted and verified"
            completed.append(record)
            if progress:
                progress(document["ticker"], document["fiscal_year"], action, _summary(value))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise PassageError(f"{label}: {exc}") from None
    # Re-read originals and financial evidence before publishing a new catalogue.
    if sources != _load_sources(tickers, years, data_dir, corpus_dir, as_of):
        raise PassageError("Source corpus changed during extraction; no manifest published.")
    for value in dependencies:
        verify_dependencies(value, corpus_dir)
    if not verify_only:
        indexed.update({(r["ticker"], r["fiscal_year"]): r for r in completed})
        _atomic_write(folder / "manifest.json", _json_bytes({"schema_version": 1,
            "parser_version": PARSER_VERSION, "documents": [indexed[key] for key in sorted(indexed)]}))
    return {"status": "ok", "verified_company_years": len(completed),
            "companies": len({r["ticker"] for r in completed}),
            "sectors": sorted({doc["sector"] for doc, _ in sources}),
            "fiscal_years": sorted({r["fiscal_year"] for r in completed}),
            "built": built, "cached": cached,
            "text_passages": sum(r["text_passages"] for r in completed),
            "table_passages": sum(r["table_passages"] for r in completed),
            "unassigned_passages": sum(r["unassigned_passages"] for r in completed),
            "coverage_review_required": bool(coverage_warnings), "coverage_warnings": coverage_warnings,
            "manifest": str(folder / "manifest.json")}


def preview_passages(ticker, year, *, passages_dir=DEFAULT_PASSAGES_DIR, item="1A", limit=2):
    """Read a small human-review sample; run --verify-only before relying on it."""
    if type(limit) is not int or not 1 <= limit <= 5 or item not in ITEM_ORDER:
        raise PassageError("Invalid preview Item or limit.")
    records = {(r["ticker"], r["fiscal_year"]): r for r in _manifest(passages_dir)}
    if (ticker, year) not in records:
        raise PassageError("Company/year is missing from the passage manifest.")
    record = records[ticker, year]
    path = _output_path(passages_dir, record["output_sha256"])
    if path.stat().st_size > MAX_OUTPUT_BYTES:
        raise PassageError("Oversized passage object.")
    raw = path.read_bytes()
    if digest(raw) != record["output_sha256"]:
        raise PassageError("Passage object fingerprint changed.")
    value = json.loads(raw)
    selected = [p for p in value["passages"] if p["item"] == item][:limit]
    return {"ticker": ticker, "fiscal_year": year, "item": item,
            "items": record["items"], "samples": selected}
