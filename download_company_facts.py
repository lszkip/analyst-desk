"""Download or reuse SEC company facts for MSFT and Alphabet.

Run: python download_company_facts.py MSFT GOOGL
Use --refresh to explicitly replace a cached snapshot.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from financial_data import (
    DATA_DIR, PROJECT_DIR, COMPANIES, FinancialDataError,
    companyfacts_url, normalise_ticker, validate_companyfacts,
)


MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="sec_download_", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def download_company_facts(
    ticker: str, user_agent: str, *, refresh: bool = False,
    data_dir: Path | None = None,
) -> dict:
    ticker = normalise_ticker(ticker)
    folder = Path(data_dir if data_dir is not None else DATA_DIR)
    path = folder / f"{ticker}_companyfacts.json"
    if path.exists() and not refresh:
        validate_companyfacts(json.loads(path.read_bytes()), ticker)
        # Reusing a file does not change its bytes or original download date.
        return {"status": "cached", "ticker": ticker, "path": str(path)}
    if not isinstance(user_agent, str) or "@" not in user_agent or any(c in user_agent for c in "\r\n"):
        raise ValueError("Set SEC_USER_AGENT in .env to your project name and contact email.")
    request = Request(companyfacts_url(ticker), headers={
        "User-Agent": user_agent,
        "Accept": "application/json",
    })
    with urlopen(request, timeout=30) as response:
        raw = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise ValueError("The SEC download exceeded the 25 MiB limit.")
    # A failed or invalid download never replaces a working cache file.
    facts = json.loads(raw)
    validate_companyfacts(facts, ticker)
    metadata = {
        "ticker": ticker,
        "cik": COMPANIES[ticker]["cik"],
        "source_url": companyfacts_url(ticker),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    folder.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, raw)
    _atomic_write(folder / f"{ticker}_companyfacts_metadata.json",
                  json.dumps(metadata, indent=2).encode("utf-8"))
    return {"status": "downloaded", "ticker": ticker, "path": str(path)}


def main() -> int:
    # python-dotenv is already installed in the analyst-desk environment.
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="+", help="MSFT and/or GOOGL (GOOG is an alias)")
    parser.add_argument("--refresh", action="store_true", help="Replace existing snapshots")
    args = parser.parse_args()
    try:
        tickers = list(dict.fromkeys(normalise_ticker(value) for value in args.tickers))
    except ValueError as exc:
        parser.error(str(exc))
    for index, ticker in enumerate(tickers):
        if index:
            time.sleep(0.25)
        try:
            result = download_company_facts(ticker, os.getenv("SEC_USER_AGENT", ""), refresh=args.refresh)
        except HTTPError as exc:
            print(f"SEC download failed for {ticker}: HTTP {exc.code}. Check SEC_USER_AGENT; retry later if rate-limited.")
            return 1
        except (OSError, URLError, ValueError, FinancialDataError) as exc:
            print(f"Could not prepare {ticker} data ({type(exc).__name__}). Check .env, connectivity and the cached JSON file.")
            return 1
        print(f"{result['status'].capitalize()}: {ticker} -> {result['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
