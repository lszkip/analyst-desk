"""Check cached company/year/metric coverage without calling the network or AI.

This is a data-availability and consistency check, not the capstone's golden
evaluation or an independent accounting audit.
"""

import argparse
from collections import Counter
import json
from pathlib import Path

from company_registry import DATA_DIR, COMPANIES, normalise_ticker
from financial_data import METRICS, get_annual_financial_fact
from sec_periods import validate_year
from financial_metrics import CORE_METRICS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", nargs="+", help="Default: all companies in companies.json")
    parser.add_argument("--years", nargs="+", type=int, default=[2023, 2024, 2025])
    parser.add_argument("--metrics", nargs="+", choices=list(METRICS), default=list(CORE_METRICS),
                        help="Default: the original revenue, operating_income and net_income baseline")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    try:
        tickers = list(dict.fromkeys(normalise_ticker(t) for t in (args.tickers or COMPANIES)))
        years = list(dict.fromkeys(args.years))
        for year in years:
            validate_year(year)
    except ValueError as exc:
        parser.error(str(exc))
    results = []
    for ticker in tickers:
        for year in years:
            for metric in dict.fromkeys(args.metrics):
                result = get_annual_financial_fact(ticker, metric, year, data_dir=args.data_dir)
                results.append({"request": {"ticker": ticker, "metric": metric, "year": year}, "result": result})
                period = f"{result.get('period_start', '?')} to {result.get('period_end', '?')}"
                print(f"{ticker:5} FY{year} {metric:17} {result['status']:18} {period}")
    counts = Counter(item["result"]["status"] for item in results)
    print(f"\nChecked {len(results)} lookups: " + ", ".join(f"{status}={count}" for status, count in sorted(counts.items())))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"counts": dict(counts), "checks": results}, indent=2) + "\n", encoding="utf-8")
        print(f"Saved report: {args.output}")
    return 0 if counts.get("ok", 0) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
