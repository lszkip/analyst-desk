"""Extract Item-labelled passages and structured tables from the verified corpus.

This command is offline. It neither downloads filings nor calls a model.
"""

import argparse
import json
from pathlib import Path

from company_registry import COMPANIES, DATA_DIR, normalise_ticker
from filing_corpus import DEFAULT_CORPUS_DIR
from filing_passages import (DEFAULT_CHUNK_WORDS, DEFAULT_PASSAGES_DIR, ITEM_ORDER,
                             prepare_passages, preview_passages)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--years", nargs="+", type=int, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--verify-only", action="store_true", help="Replay extraction and compare saved passages; no writes")
    modes.add_argument("--preview", action="store_true", help="Show saved passages for one company/year; no writes")
    parser.add_argument("--item", choices=ITEM_ORDER, default="1A", help="Item to display with --preview")
    parser.add_argument("--limit", type=int, default=2, help="Preview 1–5 passages")
    parser.add_argument("--refresh", action="store_true", help="Rebuild passage outputs; never changes original HTML")
    parser.add_argument("--chunk-words", type=int, default=DEFAULT_CHUNK_WORDS)
    parser.add_argument("--as-of")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--passages-dir", type=Path, default=DEFAULT_PASSAGES_DIR)
    args = parser.parse_args(argv)
    if args.all == bool(args.tickers):
        parser.error("Supply company tickers OR --all.")
    if args.refresh and (args.verify_only or args.preview):
        parser.error("--refresh is only valid when preparing passages.")
    if args.preview and (args.all or len(args.tickers) != 1 or len(args.years) != 1):
        parser.error("Preview requires exactly one company and one fiscal year.")
    if not 1 <= args.limit <= 5:
        parser.error("--limit must be from 1 to 5.")
    try:
        if args.preview:
            result = preview_passages(normalise_ticker(args.tickers[0]), args.years[0],
                passages_dir=args.passages_dir, item=args.item, limit=args.limit)
        else:
            def progress(ticker, year, action, counts):
                print(f"{ticker} FY{year}: {action}; {counts['text_passages']} text passages, "
                      f"{counts['table_passages']} tables, {len(counts['items'])} Items", flush=True)
            result = prepare_passages(list(COMPANIES) if args.all else args.tickers, args.years,
                data_dir=args.data_dir, corpus_dir=args.corpus_dir, passages_dir=args.passages_dir,
                as_of=args.as_of, chunk_words=args.chunk_words, refresh=args.refresh,
                verify_only=args.verify_only, progress=progress)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Passage preparation stopped ({type(exc).__name__}): {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
