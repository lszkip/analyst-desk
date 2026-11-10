"""Resolve fiscal-year labels from explicit definitions in original SEC filings.

This is a deliberately narrow text extractor, not a general XBRL parser. It
accepts an explicit 'References to fiscal YYYY ... refer to ... ending DATE'
definition, cross-checks the document identity and report date, and retains
the original bytes. An end date alone never determines the fiscal-year label.
"""

from datetime import date
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re

from company_registry import COMPANIES, filing_url

METHOD = "explicit_fiscal_year_definition_v1"
MONTHS = {name.lower(): i for i, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"), 1)}
DATE_PATTERN = r"(?:" + "|".join(MONTHS) + r")\s+\d{1,2}\s*,\s*\d{4}"
DEFINITION = re.compile(
    r"\bReferences to fiscal(?: year)? (?P<year>\d{4})"
    r"(?:, for example,)? refer to the fiscal year (?:ending|ended) "
    r"(?P<end>" + DATE_PATTERN + r")\.", re.I)


def normal_text(value):
    return " ".join(value.split())


def named_date(value):
    value = normal_text(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value).isoformat()
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2})\s*,\s*(\d{4})", value)
    if not match or match[1].lower() not in MONTHS:
        raise ValueError("Unsupported date in original filing.")
    return date(int(match[3]), MONTHS[match[1].lower()], int(match[2])).isoformat()


def primary_document_url(ticker, row):
    # Build the URL from validated identifiers, never from arbitrary input URLs.
    index = filing_url(ticker, row["accessionNumber"])
    name = row.get("primaryDocument", "")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*\.html?", name):
        raise ValueError("Unsupported SEC primary document filename.")
    return index.rsplit("/", 1)[0] + "/" + name


def _inline_report_date(row):
    """Canonicalise the supported date formats before comparing identity facts.

    Numeric dates require a recognised, namespace-qualified transformation.
    This is a narrow four-digit-year subset, not a general iXBRL transform engine.
    """
    value = normal_text("".join(row["parts"]))
    fmt = row["attrs"].get("format")
    if not fmt:
        return named_date(value)
    namespace = row.get("format_namespace")
    modern = {"http://www.xbrl.org/inlineXBRL/transformation/2020-02-12",
              "http://www.xbrl.org/inlineXBRL/transformation/2022-02-16"}
    legacy = {"http://www.xbrl.org/inlineXBRL/transformation/2011-07-31",
              "http://www.xbrl.org/inlineXBRL/transformation/2015-02-26"}
    transforms = {"date-month-day-year": "mdy", "date-monthname-day-year-en": "english",
                  "date-year-month-day": "ymd"} if namespace in modern else {
                  "datemonthdayyear": "mdy", "datemonthdayyearen": "english",
                  "dateyearmonthday": "ymd"} if namespace in legacy else {}
    kind = transforms.get(fmt.split(":", 1)[1]) if fmt.count(":") == 1 else None
    if kind == "english":
        # Keep the existing full English month / four-digit year policy.
        if re.fullmatch(r"[A-Za-z]+\s+[0-9]{1,2}\s*,\s*[0-9]{4}", value):
            return named_date(value)
    elif kind in ("mdy", "ymd"):
        pattern = (r"([0-9]{1,2})[^0-9]+([0-9]{1,2})[^0-9]+([0-9]{4})"
                   if kind == "mdy" else r"([0-9]{4})[^0-9]+([0-9]{1,2})[^0-9]+([0-9]{1,2})")
        match = re.fullmatch(pattern, value)
        if match:
            a, b, c = map(int, match.groups())
            return date(c, a, b).isoformat() if kind == "mdy" else date(a, b, c).isoformat()
    raise ValueError("Unsupported inline report-date format or value.")


class _FilingText(HTMLParser):
    """Capture nested inline facts and text, excluding hidden text from prose."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.active, self.facts, self.parts = [], [], {}, []
        self.namespace_stack = [{}]

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        namespaces = dict(self.namespace_stack[-1])
        namespaces.update({key[6:]: value for key, value in attrs.items() if key.startswith("xmlns:")})
        suppressed = tag in ("head", "script", "style", "ix:hidden", "ix:header")
        suppressed = suppressed or "hidden" in attrs or "display:none" in attrs.get("style", "").replace(" ", "").lower()
        if tag not in ("br", "hr", "img", "meta", "link", "input", "wbr"):
            self.stack.append((tag, suppressed))
            self.namespace_stack.append(namespaces)
        if tag == "ix:nonnumeric" and attrs.get("name", "").lower().startswith("dei:"):
            self.active.append({"depth": len(self.stack), "name": attrs["name"].lower(),
                                "context": attrs.get("contextref"), "attrs": attrs, "parts": [],
                                "format_namespace": namespaces.get((attrs.get("format") or "").split(":", 1)[0].lower())})
        if not any(hidden for _, hidden in self.stack) and tag in ("div", "p", "br", "td", "th"):
            self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, value):
        for active in self.active:
            active["parts"].append(value)
        if not any(hidden for _, hidden in self.stack):
            self.parts.append(value)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            return
        depth = len(self.stack)
        for active in list(self.active):
            if active["depth"] == depth:
                self.facts.setdefault(active["name"], []).append(active)
                self.active.remove(active)
        self.stack.pop()
        self.namespace_stack.pop()

    def fact(self, name):
        rows = self.facts.get("dei:" + name.lower(), [])
        if not rows or any(r["attrs"].get("continuedat") or r["attrs"].get("xsi:nil") for r in rows):
            raise ValueError(f"Missing or unsupported inline filing identity: {name}")
        values = {(_inline_report_date(r) if name.lower() == "documentperiodenddate"
                   else normal_text("".join(r["parts"])), r["context"]) for r in rows}
        if len(values) != 1 or not next(iter(values))[1]:
            raise ValueError(f"Conflicting inline filing identity: {name}")
        return next(iter(values))


def extract_year_evidence(raw, ticker, row):
    """Return verifiable evidence, or None when the narrow rule cannot resolve it."""
    url = primary_document_url(ticker, row)
    parser = _FilingText()
    parser.feed(raw.decode("utf-8-sig"))
    parser.close()
    text = normal_text("".join(parser.parts))
    matches = [m for m in DEFINITION.finditer(text) if named_date(m["end"]) == row["reportDate"]]
    if not matches:
        return None
    years = {int(m["year"]) for m in matches}
    if len(years) != 1 or not 1900 <= next(iter(years)) <= 2100:
        raise ValueError("Conflicting explicit fiscal-year definitions.")
    identity = {name: parser.fact(name) for name in (
        "EntityCentralIndexKey", "DocumentType", "DocumentPeriodEndDate",
        "DocumentFiscalYearFocus", "DocumentFiscalPeriodFocus")}
    cik = identity["EntityCentralIndexKey"][0]
    if not re.fullmatch(r"\d{1,10}", cik) or int(cik) != int(COMPANIES[ticker]["cik"]):
        raise ValueError("Original filing belongs to another company.")
    if len({v[1] for v in identity.values()}) != 1:
        raise ValueError("Filing identity facts have inconsistent contexts.")
    if identity["DocumentType"][0] != row["form"] or identity["DocumentFiscalPeriodFocus"][0] != "FY":
        raise ValueError("Original filing is not the expected annual report.")
    if named_date(identity["DocumentPeriodEndDate"][0]) != row["reportDate"]:
        raise ValueError("Original filing and submissions report dates disagree.")
    tagged = identity["DocumentFiscalYearFocus"][0]
    if not re.fullmatch(r"\d{4}", tagged) or not 1900 <= int(tagged) <= 2100:
        raise ValueError("Invalid fiscal-year tag in original filing.")
    return {"method": METHOD, "fiscal_year": next(iter(years)),
            "report_date": row["reportDate"], "tagged_fiscal_year": int(tagged),
            "quote": matches[0].group(), "source_url": url,
            "source_sha256": hashlib.sha256(raw).hexdigest()}


def validate_year_evidence(evidence, ticker, row):
    if not isinstance(evidence, dict) or set(evidence) != {
        "method", "fiscal_year", "report_date", "tagged_fiscal_year", "quote", "source_url", "source_sha256"
    }:
        raise ValueError("Invalid fiscal-year evidence structure.")
    if evidence["method"] != METHOD or evidence["source_url"] != primary_document_url(ticker, row):
        raise ValueError("Unexpected fiscal-year evidence source.")
    for key in ("fiscal_year", "tagged_fiscal_year"):
        if type(evidence[key]) is not int or not 1900 <= evidence[key] <= 2100:
            raise ValueError("Invalid fiscal-year evidence year.")
    digest = evidence["source_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid fiscal-year evidence fingerprint.")
    quote = evidence["quote"]
    match = DEFINITION.fullmatch(quote) if isinstance(quote, str) else None
    if (not match or int(match["year"]) != evidence["fiscal_year"]
            or named_date(match["end"]) != evidence["report_date"]
            or evidence["report_date"] != row.get("reportDate")):
        raise ValueError("Fiscal-year evidence does not support its label and date.")


def evidence_path(folder, evidence):
    # Caller validates the digest first; no filename from downloaded text is used.
    return Path(folder) / "sec_fiscal_years" / (evidence["source_sha256"] + ".html")


def verify_cached_year_evidence(history, ticker, folder):
    for row in history["annual_filings"]:
        if "fiscal_year_evidence" not in row:
            continue
        evidence = row["fiscal_year_evidence"]
        validate_year_evidence(evidence, ticker, row)
        raw = evidence_path(folder, evidence).read_bytes()
        if hashlib.sha256(raw).hexdigest() != evidence["source_sha256"]:
            raise ValueError("Original filing fingerprint no longer matches.")
        if extract_year_evidence(raw, ticker, row) != evidence:
            raise ValueError("Fiscal-year evidence cannot be reproduced from the original filing.")
