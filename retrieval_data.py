"""Verified passage catalogue and bounded, table-aware search representations.

Search windows are an index representation only. get_passage returns the original
whole passage/table, including blank cells, spans and stable cell identifiers.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

from company_registry import COMPANIES, DATA_DIR
from filing_corpus import DEFAULT_CORPUS_DIR, _json_bytes, digest, MAX_HTML_BYTES
from filing_passages import (DEFAULT_PASSAGES_DIR, MAX_OUTPUT_BYTES, _manifest,
                             _output_path, _record, _coverage_warnings, prepare_passages)

POLICY = "table_rows_token_windows_v2"


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class RetrievalError(ValueError):
    pass


def bounded_json(path, limit):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise RetrievalError(f"Missing or oversized {path.name}.")
    return json.loads(path.read_bytes())


def source_path(corpus_dir, sha, relative):
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RetrievalError("Invalid source fingerprint.")
    if relative not in (f"objects/{sha}.html", f"supplements/objects/{sha}.html"):
        raise RetrievalError("Source path does not match its fingerprint.")
    root = Path(corpus_dir).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise RetrievalError("Source path leaves the corpus directory.")
    return path


class PassageCatalogue:
    def __init__(self, passages_dir=DEFAULT_PASSAGES_DIR, corpus_dir=DEFAULT_CORPUS_DIR):
        self.folder, self.corpus = Path(passages_dir), Path(corpus_dir)
        manifest_path = self.folder / "manifest.json"
        bounded_json(manifest_path, 4 * 1024 * 1024)
        self.manifest_sha = sha_file(manifest_path)
        self.records = _manifest(self.folder)
        if not self.records or len(self.records) > 256:
            raise RetrievalError("Passage manifest must contain 1–256 company-years.")
        self.passages, self.documents, self.files, self.source_files = {}, {}, {}, {}
        self.files[manifest_path] = self.manifest_sha
        self.warnings = []
        total = 0
        for record in self.records:
            ticker, year = record["ticker"], record["fiscal_year"]
            if ticker not in COMPANIES or not 1900 <= year <= 2100:
                raise RetrievalError("Unknown company or invalid fiscal year in passage manifest.")
            path = _output_path(self.folder, record["output_sha256"])
            if not path.resolve().is_relative_to(self.folder.resolve()):
                raise RetrievalError("Passage object path leaves the passage directory.")
            if record["output_path"] != f"objects/{record['output_sha256']}.json":
                raise RetrievalError("Invalid passage object path.")
            value = bounded_json(path, MAX_OUTPUT_BYTES)
            total += path.stat().st_size
            if total > 768 * 1024 * 1024:
                raise RetrievalError("Passage corpus exceeds the 768 MiB loading limit.")
            raw = path.read_bytes()
            if digest(raw) != record["output_sha256"] or _record(value, raw) != record:
                raise RetrievalError("Passage object or metadata changed; replay-verify preparation first.")
            self.files[path] = record["output_sha256"]
            self.documents[(ticker, year)] = value
            self.warnings.extend(_coverage_warnings(value))
            for passage in value["passages"]:
                pid = passage.get("passage_id")
                without_id = {k: v for k, v in passage.items() if k != "passage_id"}
                if pid != "p_" + digest(_json_bytes(without_id)) or pid in self.passages:
                    raise RetrievalError("Invalid or duplicate stable passage ID.")
                if (passage["ticker"], passage["fiscal_year"]) != (ticker, year) or passage.get("untrusted_source") is not True:
                    raise RetrievalError("Passage company/year or trust metadata does not match.")
                self.passages[pid] = passage
                source = source_path(self.corpus, passage["source_sha256"], passage["html_path"])
                self.source_files[source] = passage["source_sha256"]
                if "parent_source_sha256" in passage:
                    parent = source_path(self.corpus, passage["parent_source_sha256"], passage["parent_html_path"])
                    self.source_files[parent] = passage["parent_source_sha256"]
        if len(self.passages) > 100000:
            raise RetrievalError("Too many passages for this local deployment.")
        self.files.update(self.source_files)
        self.verify_unchanged()
        self.stamps = {p: self._stamp(p) for p in self.files}

    @staticmethod
    def _stamp(path):
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def verify_unchanged(self):
        for path, sha in self.files.items():
            limit = MAX_HTML_BYTES if path in self.source_files else MAX_OUTPUT_BYTES
            if not path.is_file() or path.stat().st_size > limit or sha_file(path) != sha:
                raise RetrievalError("Saved passage or source bytes changed; no index/result published.")

    def check_live(self):
        if any(not p.exists() or self._stamp(p) != stamp for p, stamp in self.stamps.items()):
            raise RetrievalError("Corpus changed since this process loaded it. Verify/rebuild the index and restart.")

    def replay(self, data_dir=DATA_DIR):
        """Group identical year/settings selections; avoid inventing missing pairs."""
        groups = defaultdict(list)
        by_company = defaultdict(list)
        for record in self.records:
            by_company[(record["ticker"], record["chunk_words"])].append(record["fiscal_year"])
        for (ticker, words), years in by_company.items():
            groups[(tuple(sorted(years)), words)].append(ticker)
        for (years, words), tickers in groups.items():
            result = prepare_passages(tickers, list(years), chunk_words=words, data_dir=data_dir,
                corpus_dir=self.corpus, passages_dir=self.folder, verify_only=True)
            if result["coverage_review_required"]:
                raise RetrievalError("Unresolved passage coverage warnings; resolve them before indexing.")
        self.verify_unchanged()

    def get(self, pid):
        if not isinstance(pid, str) or not re.fullmatch(r"p_[0-9a-f]{64}", pid):
            raise RetrievalError("Use a stable passage ID returned by search.")
        self.check_live()
        if pid not in self.passages:
            raise RetrievalError("Passage ID is not present in this verified corpus.")
        return self.passages[pid]

    def section(self, ticker, year, item, offset=0, limit=5):
        self.check_live()
        if (ticker, year) not in self.documents:
            raise RetrievalError("Requested company/year is absent from the verified corpus.")
        selected = [p for p in self.documents[(ticker, year)]["passages"] if p["item"] == item]
        return {"ticker": ticker, "fiscal_year": year, "item": item,
                "passages": selected[offset:offset + limit], "total": len(selected),
                "next_offset": offset + limit if offset + limit < len(selected) else None}

    def summary(self):
        return {"company_years": len(self.records), "companies": sorted({r["ticker"] for r in self.records}),
                "pairs": [[a, b] for a, b in sorted(self.documents)],
                "passages": len(self.passages), "tables": sum(p["kind"] == "table" for p in self.passages.values()),
                "unassigned_passages": sum(p["item"] is None for p in self.passages.values()),
                "coverage_warnings": self.warnings, "passage_manifest_sha256": self.manifest_sha}


def table_rows(table):
    """Compact search text; link every cell, including blanks and merged spans.

    Original cells and spans remain untouched in the complete parent table.
    Omitting layout blanks from *search text* avoids drowning out financial labels.
    """
    for row in table["rows"]:
        if not row:
            continue
        cells = []
        for cell in row:
            if cell["text"]:
                cells.append(f"c{cell['column']}: {cell['text']}")
        yield f"row {row[0]['row']}: " + (" | ".join(cells) or "[blank cells]"), [c["cell_id"] for c in row]
        for cell in row:
            for nested in cell["nested_tables"]:
                yield from table_rows(nested)


def search_units(catalogue, models, *, plain_text=False):
    result = []
    for pid in sorted(catalogue.passages):
        p = catalogue.passages[pid]
        prefix = f"{p['ticker']} {p['company']} fiscal year {p['fiscal_year']}; Item {p['item'] or 'unassigned'}; {p['item_heading']}. "
        if p["kind"] == "table" and not plain_text:
            rows = list(table_rows(p["table"]))
            # Header/context repeat aids matching; originals remain fully preserved.
            context = p.get("source_page_title", "") + " " + p["table"]["caption"] + " " + p["context_before"]
            header_text = " ".join(text for text, _ in rows[:2])
            prefix += (p.get("source_page_title", "") + " " + p["table"]["caption"] +
                       " Headers: " + header_text + " Context: " + context)
            body, ranges = "", []
            for text, ids in rows:
                start = len(body)
                body += text + "\n"
                ranges.append((start, len(body), ids))
            if not body.strip():
                body, ranges = p["text"], []
        else:
            body, ranges = p["text"], []
        prefix_parts = models.windows(prefix, size=getattr(models, "settings", {}).get("prefix_tokens", 90), overlap=0)
        prefix = prefix_parts[0][0] + "\n" if prefix_parts else ""
        for ordinal, (text, start, end) in enumerate(models.windows(body)):
            cell_ids = list(dict.fromkeys(c for a, b, ids in ranges if a < end and b > start for c in ids))
            unit = {"passage_id": pid, "ordinal": ordinal, "text": prefix + text,
                    "ticker": p["ticker"], "fiscal_year": p["fiscal_year"],
                    "item": p["item"], "kind": p["kind"], "cell_ids": cell_ids,
                    "source_sha256": p["source_sha256"]}
            unit["unit_id"] = "u_" + digest(_json_bytes(unit))
            result.append(unit)
        if len(result) > 250000:
            raise RetrievalError("Search representation exceeds 250,000 windows.")
    if not result:
        raise RetrievalError("No searchable content was extracted.")
    return result
