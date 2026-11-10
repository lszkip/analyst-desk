"""Download IBM's explicitly referenced financial-report exhibits before extraction."""

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError

from company_registry import DATA_DIR, PROJECT_DIR
from filing_corpus import DEFAULT_CORPUS_DIR
from filing_supplements import prepare_supplements


def main(argv=None):
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--years", nargs="+", type=int, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--as-of")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    args = parser.parse_args(argv)
    if args.refresh and (args.plan_only or args.verify_only):
        parser.error("--refresh is only valid when downloading.")
    try:
        result = prepare_supplements(args.tickers, args.years, os.getenv("SEC_USER_AGENT", ""),
            data_dir=args.data_dir, corpus_dir=args.corpus_dir, as_of=args.as_of,
            refresh=args.refresh, plan_only=args.plan_only, verify_only=args.verify_only)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except HTTPError as exc:
        print(f"SEC returned HTTP {exc.code}. Check SEC_USER_AGENT; wait before retrying rate limits.")
    except (ValueError, KeyError, TypeError, OSError, URLError) as exc:
        print(f"Supplement preparation stopped ({type(exc).__name__}): {exc}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
