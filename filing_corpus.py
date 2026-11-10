"""Prepare original 10-K HTML for later retrieval, without changing the /ask API.

Fiscal years come from the existing evidence-based period resolver. This is
document acquisition, NOT passage extraction, semantic search or an evaluation.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from company_registry import COMPANIES, DATA_DIR, normalise_ticker
from download_company_facts import _atomic_write
from sec_periods import read_filings, select_annual_period, validate_companyfacts, validate_year
from sec_fiscal_years import (
    _FilingText, evidence_path, extract_year_evidence, named_date, primary_document_url,
    verify_cached_year_evidence,
)

SCHEMA_VERSION = 1
MAX_HTML_BYTES = 25 * 1024 * 1024
DEFAULT_CORPUS_DIR = DATA_DIR.parent / "filings"
POLICY = "one_original_10k_per_verified_company_fiscal_year_v1"
_last_request = 0.0


class CorpusError(ValueError):
    """A document or its provenance cannot be safely included in the corpus."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _selection(tickers, years):
    if not tickers or not years:
        raise CorpusError("Supply at least one company and one explicit fiscal year.")
    companies = sorted({normalise_ticker(t) for t in tickers})
    for year in years:
        if type(year) is not int:
            raise CorpusError("Fiscal years must be integers.")
        validate_year(year)
    return companies, sorted(set(years))


def plan_corpus(tickers, years, *, data_dir=DATA_DIR, as_of=None):
    """Read existing facts/history only. Return every requested pair or fail.

    10-K/A amendments are often partial documents; they never replace the base
    10-K here. Financial-value lookup retains its separate amendment policy.
    """
    from sec_periods import iso_date
    tickers, years = _selection(tickers, years)
    cutoff = iso_date(as_of) if as_of is not None else None
    folder = Path(data_dir)
    documents = []
    for ticker in tickers:
        try:
            facts_raw = (folder / f"{ticker}_companyfacts.json").read_bytes()
            history_raw = (folder / f"{ticker}_filings.json").read_bytes()
            facts, history = json.loads(facts_raw), json.loads(history_raw)
            validate_companyfacts(facts, ticker)
            filings = read_filings(history, ticker)
            verify_cached_year_evidence(history, ticker, folder)
            for year in years:
                period = select_annual_period(facts, filings, ticker, year, cutoff)
                if period["status"] != "ok":
                    raise CorpusError(f"{ticker} FY{year}: {period['status']} ({period.get('code')}).")
                # Only a filing which actually establishes this year is eligible.
                source_ids = {s["filing_identifier"] for s in period["period_sources"]}
                candidates = [row for accession, row in filings.items()
                              if accession in source_ids and row["form"] == "10-K"
                              and row.get("reportDate") == period["period_end"]
                              and (cutoff is None or row["filingDate"] <= cutoff)]
                if len(candidates) != 1:
                    raise CorpusError(f"{ticker} FY{year}: expected one evidenced original 10-K; found {len(candidates)}.")
                row = candidates[0]
                company = COMPANIES[ticker]
                documents.append({
                    "ticker": ticker, "company": company["name"], "cik": company["cik"],
                    "sector": company["sector"], "fiscal_year": year,
                    "period_start": period["period_start"], "period_end": period["period_end"],
                    "filing_identifier": row["accessionNumber"], "filed": row["filingDate"],
                    "form": row["form"], "primary_document": row["primaryDocument"],
                    "source_url": primary_document_url(ticker, row), "as_of": cutoff,
                    "facts_snapshot_sha256": digest(facts_raw),
                    "filings_snapshot_sha256": digest(history_raw),
                    "period_sources": period["period_sources"],
                    "fiscal_year_evidence": row.get("fiscal_year_evidence"),
                })
        except CorpusError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CorpusError(f"{ticker}: unable to verify cached financial facts and filing history ({type(exc).__name__}).") from None
    return {"schema_version": SCHEMA_VERSION, "policy": POLICY,
            "requested_pairs": len(tickers) * len(years), "documents": documents}


def validate_html(raw, document):
    """Verify inline filing identity; keep the original bytes, including tables.

    No external text is executed. This narrow validator deliberately rejects
    unsupported/non-inline documents rather than accepting an error page.
    """
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_HTML_BYTES:
        raise CorpusError("The HTML is empty or exceeds the 25 MiB limit.")
    try:
        text = raw.decode("utf-8-sig")
        if not re.search(r"<html(?:\s|>)", text, re.I):
            raise ValueError("Not an HTML document")
        parser = _FilingText()
        parser.feed(text)
        parser.close()
        identity = {name: parser.fact(name) for name in (
            "EntityCentralIndexKey", "DocumentType", "DocumentPeriodEndDate",
            "DocumentFiscalYearFocus", "DocumentFiscalPeriodFocus")}
        cik = identity["EntityCentralIndexKey"][0]
        if not re.fullmatch(r"\d{1,10}", cik) or int(cik) != int(document["cik"]):
            raise ValueError("Wrong company")
        if len({value[1] for value in identity.values()}) != 1:
            raise ValueError("Inconsistent identity contexts")
        if identity["DocumentType"][0] != "10-K" or identity["DocumentFiscalPeriodFocus"][0] != "FY":
            raise ValueError("Not an original annual 10-K")
        if named_date(identity["DocumentPeriodEndDate"][0]) != document["period_end"]:
            raise ValueError("Wrong report date")
        focus = identity["DocumentFiscalYearFocus"][0]
        if not re.fullmatch(r"\d{4}", focus):
            raise ValueError("Invalid fiscal-year label")
        evidence = document["fiscal_year_evidence"]
        if evidence:
            row = {"accessionNumber": document["filing_identifier"],
                   "primaryDocument": document["primary_document"], "form": "10-K",
                   "reportDate": document["period_end"]}
            if digest(raw) != evidence["source_sha256"]:
                raise ValueError("HTML fingerprint differs from the previously verified fiscal-year source")
            if extract_year_evidence(raw, document["ticker"], row) != evidence:
                raise ValueError("Fiscal-year resolution differs from original document")
        elif int(focus) != document["fiscal_year"]:
            raise ValueError("Fiscal-year label disagrees with plan")
        return {"cik": str(int(cik)), "form": "10-K", "report_date": document["period_end"],
                "tagged_fiscal_year": int(focus), "fiscal_year": document["fiscal_year"]}
    except (UnicodeError, ValueError, KeyError, TypeError) as exc:
        # ValueError messages here are fixed local validation messages. Do not
        # emit document text, credentials, or potentially huge decoding errors.
        reason = str(exc) if type(exc) is ValueError else type(exc).__name__
        raise CorpusError(f"{document.get('ticker', '?')} FY{document.get('fiscal_year', '?')}: "
                          f"original filing identity could not be verified: {reason}.") from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_html(url, user_agent):
    """Declared SEC-only downloads, no redirects/proxies, <= 3.34 requests/s."""
    global _last_request
    if not isinstance(url, str) or not re.fullmatch(
        r"https://www\.sec\.gov/Archives/edgar/data/\d{1,10}/\d{18}/[A-Za-z0-9][A-Za-z0-9_-]*\.html?", url
    ):
        raise CorpusError("Only a validated SEC primary-document URL is allowed.")
    if (not isinstance(user_agent, str) or "@" not in user_agent
            or any(ord(c) < 32 or ord(c) == 127 for c in user_agent)):
        raise CorpusError("Set SEC_USER_AGENT in .env to your project name and contact email.")
    delay = 0.3 - (time.monotonic() - _last_request)
    if delay > 0:
        time.sleep(delay)
    _last_request = time.monotonic()
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "text/html, application/xhtml+xml"})
    with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(MAX_HTML_BYTES + 1)
    if not raw or len(raw) > MAX_HTML_BYTES:
        raise CorpusError("SEC response is empty or exceeds the 25 MiB limit.")
    return raw


def _object_path(folder, sha256):
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise CorpusError("Invalid HTML fingerprint.")
    return Path(folder) / "objects" / (sha256 + ".html")


def _receipt_path(folder, document):
    # Plan values have already passed the company/filing validators.
    return Path(folder) / "receipts" / f"{document['ticker']}_{document['fiscal_year']}_{document['filing_identifier']}.json"


def _read_document(folder, record, expected):
    if not isinstance(record, dict) or any(record.get(key) != value for key, value in expected.items()):
        raise CorpusError("Cached document metadata differs from the current filing plan. Use --refresh to prepare it again.")
    try:
        path = _object_path(folder, record["source_sha256"])
        if record["html_path"] != path.relative_to(folder).as_posix():
            raise CorpusError("Unexpected HTML path in corpus metadata.")
        raw = path.read_bytes()
        if digest(raw) != record["source_sha256"] or len(raw) != record["size_bytes"]:
            raise CorpusError("Cached HTML fingerprint or length changed. Use --refresh to repair this document.")
        if validate_html(raw, expected) != record["inline_identity"]:
            raise CorpusError("Saved inline filing identity does not match the document.")
        if record["downloaded_at"] is None:
            if record.get("source_acquisition") != "fiscal_year_cache" or not expected["fiscal_year_evidence"]:
                raise CorpusError("Missing download provenance.")
            datetime.fromisoformat(record["prepared_at"])
        else:
            datetime.fromisoformat(record["downloaded_at"])
        return record
    except CorpusError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CorpusError(f"Cached document cannot be verified ({type(exc).__name__}). Use --refresh to repair it.") from None


def _load_manifest(folder):
    path = Path(folder) / "manifest.json"
    if not path.exists():
        return []
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION
            or value.get("policy") != POLICY or not isinstance(value.get("documents"), list)):
        raise CorpusError("Unsupported or damaged corpus manifest.")
    rows = value["documents"]
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise CorpusError("Invalid manifest entry.")
        key = (normalise_ticker(row.get("ticker")), row.get("fiscal_year"))
        validate_year(key[1])
        if key in seen:
            raise CorpusError("Duplicate company/year in corpus manifest.")
        seen.add(key)
    return rows


def prepare_corpus(tickers, years, user_agent, *, data_dir=DATA_DIR,
                   corpus_dir=DEFAULT_CORPUS_DIR, as_of=None, refresh=False, progress=None):
    """Prepare all requested documents, then atomically publish the manifest.

    A failed run leaves the earlier manifest intact. Verified per-document
    receipts let a retry reuse completed downloads. Old HTML objects are retained.
    """
    plan = plan_corpus(tickers, years, data_dir=data_dir, as_of=as_of)
    folder = Path(corpus_dir)
    previous = _load_manifest(folder)
    completed, downloaded, cached, reused = [], 0, 0, 0
    for document in plan["documents"]:
        receipt_path = _receipt_path(folder, document)
        if receipt_path.exists() and not refresh:
            record = _read_document(folder, json.loads(receipt_path.read_bytes()), document)
            cached += 1
            action = "cached"
        else:
            if document["fiscal_year_evidence"]:
                # This exact snapshot already establishes the selected fiscal
                # year. A fresh HTTP delivery may contain a different injected
                # script despite unchanged filing content. Keep the original
                # bytes and all existing fingerprint checks, including refresh.
                raw = evidence_path(data_dir, document["fiscal_year_evidence"]).read_bytes()
                acquisition = "fiscal_year_cache"
                action = "reused original fiscal-year source"
            else:
                raw = fetch_html(document["source_url"], user_agent)
                acquisition = "download"
                action = "downloaded"
            identity = validate_html(raw, document)
            sha256 = digest(raw)
            path = _object_path(folder, sha256)
            _atomic_write(path, raw)
            prepared_at = datetime.now(timezone.utc).isoformat()
            record = {**document, "source_sha256": sha256, "size_bytes": len(raw),
                      "html_path": path.relative_to(folder).as_posix(), "inline_identity": identity,
                      "source_acquisition": acquisition, "prepared_at": prepared_at,
                      "downloaded_at": prepared_at if acquisition == "download" else None}
            _atomic_write(receipt_path, _json_bytes(record))
            downloaded += acquisition == "download"
            reused += acquisition == "fiscal_year_cache"
        completed.append(record)
        if progress:
            progress(document["ticker"], document["fiscal_year"], action)
    # Reject a cache changed during a long download run before publishing it.
    if plan != plan_corpus(tickers, years, data_dir=data_dir, as_of=as_of):
        raise CorpusError("Financial snapshots changed during preparation. No new manifest published.")
    by_pair = {(r["ticker"], r["fiscal_year"]): r for r in previous}
    by_pair.update({(r["ticker"], r["fiscal_year"]): r for r in completed})
    manifest = {"schema_version": SCHEMA_VERSION, "policy": POLICY,
                "documents": [by_pair[key] for key in sorted(by_pair)]}
    _atomic_write(folder / "manifest.json", _json_bytes(manifest))
    return {"status": "ok", "requested_pairs": len(completed), "downloaded": downloaded,
            "cached": cached, "reused_fiscal_year_documents": reused, "manifest_documents": len(by_pair),
            "manifest": str(folder / "manifest.json")}


def verify_corpus(tickers, years, *, data_dir=DATA_DIR, corpus_dir=DEFAULT_CORPUS_DIR, as_of=None):
    """Check every requested pair against its current plan and original HTML."""
    plan = plan_corpus(tickers, years, data_dir=data_dir, as_of=as_of)
    folder = Path(corpus_dir)
    indexed = {(r["ticker"], r["fiscal_year"]): r for r in _load_manifest(folder)}
    for document in plan["documents"]:
        key = (document["ticker"], document["fiscal_year"])
        if key not in indexed:
            raise CorpusError(f"{key[0]} FY{key[1]} is not in the corpus manifest.")
        _read_document(folder, indexed[key], document)
    return {"status": "ok", "verified_company_years": len(plan["documents"]),
            "companies": len({d["ticker"] for d in plan["documents"]}),
            "sectors": sorted({d["sector"] for d in plan["documents"]}),
            "fiscal_years": sorted({d["fiscal_year"] for d in plan["documents"]})}
