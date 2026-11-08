"""Prepare cached SEC facts AND annual-filing history for configured companies.

Run: python download_company_facts.py MSFT GOOGL
Use --all for the 15 configured companies, --refresh to replace snapshots.
No LLM calls or OpenRouter key are involved.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from company_registry import (
    PROJECT_DIR, DATA_DIR, COMPANIES, normalise_ticker,
    companyfacts_url, submissions_url,
)
from sec_periods import (
    FinancialDataError, ANNUAL_FORMS, validate_identity, validate_companyfacts, read_filings,
)

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_HISTORY_FILES = 50
_last_request_started = 0.0


def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="sec_download_", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _download(url, user_agent):
    global _last_request_started
    if not isinstance(user_agent, str) or "@" not in user_agent or any(c in user_agent for c in "\r\n"):
        raise ValueError("Set SEC_USER_AGENT in .env to your project name and contact email.")
    # Rate limiting applies to every request, including filing-history pages.
    delay = .3 - (time.monotonic() - _last_request_started)
    if delay > 0:
        time.sleep(delay)
    _last_request_started = time.monotonic()
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        raw = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise ValueError("SEC download exceeded the 25 MiB limit.")
    return raw


def _cached_json(path, url, user_agent, refresh, validator):
    cached = path.exists() and not refresh
    raw = path.read_bytes() if cached else _download(url, user_agent)
    obj = json.loads(raw)
    validator(obj)  # Never replace a good file with a bad response.
    if not cached:
        _atomic_write(path, raw)
        metadata = {"source_url": url, "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": hashlib.sha256(raw).hexdigest()}
        # Retain the original facts metadata filename for compatibility.
        metadata_path = path.with_name(path.stem + "_metadata.json")
        _atomic_write(metadata_path, json.dumps(metadata, indent=2).encode())
    return obj, {"source_url": url, "sha256": hashlib.sha256(raw).hexdigest(),
                 "cache_file": path.name}, "cached" if cached else "downloaded"


def _annual_rows(table):
    columns = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")
    if not isinstance(table, dict) or any(not isinstance(table.get(key), list) for key in columns):
        raise FinancialDataError("Invalid SEC submissions table.")
    size = len(table["form"])
    if any(len(table[key]) != size for key in columns):
        raise FinancialDataError("SEC submission columns have different lengths.")
    return [{key: table[key][i] for key in columns} for i in range(size) if table["form"][i] in ANNUAL_FORMS]


def _validate_root(obj, ticker):
    validate_identity(obj, ticker)
    if not isinstance(obj.get("filings"), dict) or not isinstance(obj["filings"].get("files"), list):
        raise FinancialDataError("Missing SEC submission history.")
    _annual_rows(obj["filings"].get("recent"))
    pages = obj["filings"]["files"]
    if len(pages) > MAX_HISTORY_FILES:
        raise FinancialDataError("Too many submission history pages for this downloader.")
    cik = COMPANIES[ticker]["cik"]
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("name"), str):
            raise FinancialDataError("Invalid submission history page.")
        if not re.fullmatch(rf"CIK{cik}-submissions-\d{{3}}\.json", page["name"]):
            raise FinancialDataError("Unexpected SEC history filename.")


def download_company_facts(ticker, user_agent, *, refresh=False, data_dir=None):
    ticker = normalise_ticker(ticker)
    folder = Path(data_dir if data_dir is not None else DATA_DIR)
    facts_path = folder / f"{ticker}_companyfacts.json"
    _, _, facts_status = _cached_json(facts_path, companyfacts_url(ticker), user_agent, refresh,
                                      lambda obj: validate_companyfacts(obj, ticker))
    history_path = folder / f"{ticker}_filings.json"
    if history_path.exists() and not refresh:
        read_filings(json.loads(history_path.read_bytes()), ticker)
        history_status = "cached"
    else:
        cik = COMPANIES[ticker]["cik"]
        raw_folder = folder / "sec_submissions"
        root, root_source, _ = _cached_json(raw_folder / f"CIK{cik}.json", submissions_url(ticker),
                                           user_agent, refresh, lambda obj: _validate_root(obj, ticker))
        rows = _annual_rows(root["filings"]["recent"])
        source_snapshots = [root_source]
        # Older 10-Ks may be on separate history pages, particularly for
        # companies that file many ownership reports. Follow every listed page.
        for page in root["filings"]["files"]:
            name = page["name"]
            obj, source, _ = _cached_json(raw_folder / name,
                f"https://data.sec.gov/submissions/{name}", user_agent, refresh, _annual_rows)
            rows.extend(_annual_rows(obj))
            source_snapshots.append(source)
        history = {"cik": cik, "complete": True, "annual_filings": rows,
                   "source_snapshots": source_snapshots,
                   "prepared_at": datetime.now(timezone.utc).isoformat()}
        indexed = read_filings(history, ticker)
        history["annual_filings"] = [indexed[key] for key in sorted(indexed)]
        _atomic_write(history_path, json.dumps(history, indent=2).encode())
        history_status = "prepared"
    return {"status": facts_status, "ticker": ticker, "path": str(facts_path),
            "history_status": history_status, "history_path": str(history_path)}


def main():
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="*", help="Tickers or aliases from companies.json")
    parser.add_argument("--all", action="store_true", help="Prepare all configured companies")
    parser.add_argument("--refresh", action="store_true", help="Replace existing snapshots")
    args = parser.parse_args()
    if args.all == bool(args.tickers):
        parser.error("Supply company tickers OR --all.")
    try:
        tickers = list(COMPANIES) if args.all else list(dict.fromkeys(normalise_ticker(t) for t in args.tickers))
    except ValueError as exc:
        parser.error(str(exc))
    for ticker in tickers:
        print(f"Preparing {ticker} facts and filing history...", flush=True)
        try:
            result = download_company_facts(ticker, os.getenv("SEC_USER_AGENT", ""), refresh=args.refresh)
        except HTTPError as exc:
            print(f"SEC download failed: HTTP {exc.code}. Check SEC_USER_AGENT; retry later if rate-limited.")
            return 1
        except (OSError, URLError, ValueError) as exc:
            print(f"Could not prepare {ticker} ({type(exc).__name__}). Check connectivity, SEC_USER_AGENT and cached files.")
            return 1
        print(f"Facts {result['status']}: {result['path']}")
        print(f"Filing history {result['history_status']}: {result['history_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
