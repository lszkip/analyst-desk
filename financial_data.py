"""Look up annual SEC financial facts using actual filing periods.

Run: python financial_data.py MSFT net_income 2025
Read-only: no network, AI calls, API keys or company calendar formulas.
"""

import argparse
import hashlib
import json
from pathlib import Path

from company_registry import (
    PROJECT_DIR, DATA_DIR, COMPANIES, normalise_ticker,
    companyfacts_url, submissions_url, filing_url,
)
from sec_periods import (
    FinancialDataError, METRICS, ANNUAL_FORMS, iso_date, validate_year,
    validate_companyfacts, read_filings, metric_records, checked_filing,
    select_annual_period,
)


def validate_arguments(ticker, metric, year, as_of):
    ticker = normalise_ticker(ticker)
    validate_year(year)
    if not isinstance(metric, str) or metric not in METRICS:
        raise ValueError("Metric must be revenue, operating_income or net_income.")
    if as_of is not None:
        iso_date(as_of)
    return ticker


def select_annual_fact(facts, ticker, metric, year, *, history, as_of=None):
    """Identify the period first, then select its latest reported annual value.

The filing that establishes the fiscal period need not be the later filing
that supplies the value: subsequent 10-Ks may restate comparative figures.
"""
    ticker = validate_arguments(ticker, metric, year, as_of)
    validate_companyfacts(facts, ticker)
    filings = read_filings(history, ticker)
    base = {
        "ticker": ticker, "company": facts["entityName"],
        "cik": COMPANIES[ticker]["cik"], "metric": metric,
        "fiscal_year": year, "unit": "USD", "as_of": as_of,
        "selection_policy": "latest_matching_annual_filing_in_snapshot",
        "companyfacts_url": companyfacts_url(ticker),
        "submissions_url": submissions_url(ticker),
    }
    period = select_annual_period(facts, filings, ticker, year, as_of)
    if period["status"] != "ok":
        return {**base, **period}
    base.update({key: value for key, value in period.items() if key != "status"})
    start, end = base["period_start"], base["period_end"]
    candidates = []
    for tag, record in metric_records(facts, metric):
        if (record.get("form") not in ANNUAL_FORMS
                or record.get("start") != start or record.get("end") != end):
            continue
        filed = iso_date(record.get("filed"))
        if filed < end:
            raise FinancialDataError("Filing date precedes the reporting period.")
        if as_of is not None and filed > as_of:
            continue
        metadata = checked_filing(record, filings)
        if metadata.get("reportDate") and metadata["reportDate"] < end:
            raise FinancialDataError("Fact period is later than the filing report date.")
        if type(record.get("val")) is not int:
            raise FinancialDataError("Annual USD values must be integers; booleans are invalid.")
        candidates.append({"concept": tag, "value": record["val"], "filed": filed,
                           "filing_identifier": record["accn"], "form": record["form"],
                           "url": filing_url(ticker, record["accn"])})
    if not candidates:
        return {"status": "not_found", **base, "code": "metric_not_found",
                "message": "The annual period was identified, but no matching supported USD fact was found."}
    latest_date = max(item["filed"] for item in candidates)
    latest = [item for item in candidates if item["filed"] == latest_date]
    if len({item["value"] for item in latest}) != 1:
        return {"status": "conflict", **base, "filed": latest_date,
                "message": "Latest matching annual records disagree; no value selected.",
                "candidates": latest}
    chosen_tag = latest[0]["concept"]
    sources = {item["filing_identifier"]: item for item in latest if item["concept"] == chosen_tag}
    return {"status": "ok", **base, "concept": chosen_tag, "value": latest[0]["value"],
            "filed": latest_date, "filing_identifiers": sorted(sources),
            "sources": [sources[key] for key in sorted(sources)]}


def get_annual_financial_fact(ticker, metric, year, *, as_of=None, data_dir=None):
    ticker = validate_arguments(ticker, metric, year, as_of)
    folder = Path(data_dir if data_dir is not None else DATA_DIR)
    try:
        raw = (folder / f"{ticker}_companyfacts.json").read_bytes()
        history_raw = (folder / f"{ticker}_filings.json").read_bytes()
    except FileNotFoundError:
        return {"status": "error", "code": "data_file_missing", "ticker": ticker,
                "message": f"Both facts and filing history are required. Run: python download_company_facts.py {ticker}"}
    except OSError:
        return {"status": "error", "code": "data_file_unreadable", "ticker": ticker,
                "message": "A local SEC data file could not be read."}
    try:
        result = select_annual_fact(json.loads(raw), ticker, metric, year,
                                    history=json.loads(history_raw), as_of=as_of)
    except (UnicodeError, json.JSONDecodeError, FinancialDataError):
        return {"status": "error", "code": "invalid_data_file", "ticker": ticker,
                "message": "The SEC files are invalid or inconsistent. Check them, or rerun the downloader with --refresh."}
    result["snapshot_sha256"] = hashlib.sha256(raw).hexdigest()
    result["filings_snapshot_sha256"] = hashlib.sha256(history_raw).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker", help="Ticker or alias from companies.json")
    parser.add_argument("metric", choices=list(METRICS))
    parser.add_argument("year", type=int, help="Company fiscal-year label")
    parser.add_argument("--as-of", help="Only use filings dated on/before YYYY-MM-DD")
    args = parser.parse_args()
    try:
        result = get_annual_financial_fact(args.ticker, args.metric, args.year, as_of=args.as_of)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
