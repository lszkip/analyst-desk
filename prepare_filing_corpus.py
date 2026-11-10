"""Download original annual-report HTML after an evidence-based fiscal-year plan.

First: python prepare_filing_corpus.py MSFT GOOGL --years 2023 2024 2025 --plan-only
Then:  same command without --plan-only downloads and verifies the six reports.
No model calls, OpenRouter credits, passage search or financial-cache updates.
"""

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError

from company_registry import COMPANIES, DATA_DIR, PROJECT_DIR
from filing_corpus import DEFAULT_CORPUS_DIR, plan_corpus, prepare_corpus, verify_corpus


def main(argv=None):
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--years", nargs="+", type=int, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan-only", action="store_true", help="Read-only: no downloads or writes")
    mode.add_argument("--verify-only", action="store_true", help="Verify prepared documents without networking")
    parser.add_argument("--refresh", action="store_true", help="Reprepare selected documents; preserve verified fiscal-year source snapshots")
    parser.add_argument("--as-of", help="Only filings published on/before YYYY-MM-DD")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    args = parser.parse_args(argv)
    if args.all == bool(args.tickers):
        parser.error("Supply company tickers OR --all.")
    if args.refresh and (args.plan_only or args.verify_only):
        parser.error("--refresh is only valid when downloading.")
    tickers = list(COMPANIES) if args.all else args.tickers
    kwargs = {"data_dir": args.data_dir, "as_of": args.as_of}
    try:
        if args.plan_only:
            plan = plan_corpus(tickers, args.years, **kwargs)
            for d in plan["documents"]:
                print(f"{d['ticker']:5} FY{d['fiscal_year']}  {d['period_start']} to {d['period_end']}  {d['filing_identifier']}")
                print(f"  {d['source_url']}")
            print(f"Plan ready: {plan['requested_pairs']} company-years. No files downloaded or changed.")
            return 0
        if args.verify_only:
            result = verify_corpus(tickers, args.years, corpus_dir=args.corpus_dir, **kwargs)
        else:
            result = prepare_corpus(tickers, args.years, os.getenv("SEC_USER_AGENT", ""),
                corpus_dir=args.corpus_dir, refresh=args.refresh,
                progress=lambda t, y, action: print(f"{t} FY{y}: {action} and verified", flush=True), **kwargs)
        print(json.dumps(result, indent=2))
        return 0
    except HTTPError as exc:
        print(f"SEC returned HTTP {exc.code}. Check SEC_USER_AGENT; for rate limits, wait before retrying. No new manifest published.")
    except (OSError, URLError, ValueError, KeyError, TypeError) as exc:
        # These messages originate in trusted local code, not the model.
        print(f"Corpus preparation stopped ({type(exc).__name__}): {exc}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
