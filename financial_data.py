"""Annual financial facts for supported companies, read from local SEC JSON.

Run: python financial_data.py MSFT revenue 2025
This module makes no network or AI calls. The existing /ask API is separate.
"""

import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
import re


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data" / "raw"

# Only add a company after checking its calendar and concept mappings.
# GOOG and GOOGL both refer to the same consolidated Alphabet accounts.
COMPANIES = {
    "MSFT": {"name": "Microsoft", "cik": "0000789019", "year_end": (6, 30)},
    "GOOGL": {"name": "Alphabet", "cik": "0001652044", "year_end": (12, 31)},
}
ALIASES = {"GOOG": "GOOGL"}
METRICS = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
}


class FinancialDataError(ValueError):
    """A downloaded data file has invalid structure or matching records."""


def normalise_ticker(ticker: str) -> str:
    if not isinstance(ticker, str):
        raise ValueError("Ticker must be a string.")
    ticker = ticker.strip().upper()
    ticker = ALIASES.get(ticker, ticker)
    if ticker not in COMPANIES:
        raise ValueError("Supported tickers: MSFT, GOOGL (or GOOG).")
    return ticker


def companyfacts_url(ticker: str) -> str:
    ticker = normalise_ticker(ticker)
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{COMPANIES[ticker]['cik']}.json"


def annual_period(ticker: str, year: int) -> tuple[str, str]:
    ticker = normalise_ticker(ticker)
    if type(year) is not int or not 1900 <= year <= 2100:
        raise ValueError("Year must be an integer between 1900 and 2100.")
    month, day = COMPANIES[ticker]["year_end"]
    # Derive the requested period; the year is never hard-coded.
    start = date(year - 1, month, day) + timedelta(days=1)
    end = date(year, month, day)
    return start.isoformat(), end.isoformat()


def _iso_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use an ISO date such as 2025-08-01.")
    date.fromisoformat(value)
    return value


def validate_companyfacts(facts: dict, ticker: str) -> None:
    ticker = normalise_ticker(ticker)
    if not isinstance(facts, dict):
        raise FinancialDataError("The file must contain a JSON object.")
    cik = facts.get("cik")
    if type(cik) not in (int, str) or not re.fullmatch(r"\d{1,10}", str(cik)):
        raise FinancialDataError("The file has an invalid company identifier.")
    if int(cik) != int(COMPANIES[ticker]["cik"]):
        raise FinancialDataError("The file belongs to a different company.")
    if not isinstance(facts.get("entityName"), str) or not facts["entityName"].strip():
        raise FinancialDataError("The file is missing its company name.")
    if not isinstance(facts.get("facts"), dict):
        raise FinancialDataError("The file is missing financial facts.")
    if not isinstance(facts["facts"].get("us-gaap"), dict):
        raise FinancialDataError("The file is missing US-GAAP financial facts.")


def _filing_url(ticker: str, accession: str) -> str:
    cik = int(COMPANIES[ticker]["cik"])
    folder = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{accession}-index.html"


def select_annual_fact(
    facts: dict, ticker: str, metric: str, year: int, *, as_of: str | None = None
) -> dict:
    """Select one annual value, with filing evidence, from a parsed SEC file.

    Selection policy: latest matching 10-K/10-K/A filing date in the snapshot,
    optionally restricted to filings available on or before as_of.
    """
    ticker = normalise_ticker(ticker)
    start, end = annual_period(ticker, year)
    if not isinstance(metric, str) or metric not in METRICS:
        raise ValueError("Metric must be revenue, operating_income or net_income.")
    if as_of is not None:
        _iso_date(as_of)
    validate_companyfacts(facts, ticker)

    base = {
        "ticker": ticker,
        "company": facts["entityName"],
        "cik": COMPANIES[ticker]["cik"],
        "metric": metric,
        "fiscal_year": year,
        "period_start": start,
        "period_end": end,
        "unit": "USD",
        "as_of": as_of,
        "selection_policy": "latest_matching_annual_filing_in_snapshot",
        "companyfacts_url": companyfacts_url(ticker),
    }
    candidates = []
    concepts = facts["facts"]["us-gaap"]
    for tag in METRICS[metric]:
        if tag not in concepts:
            continue
        concept = concepts[tag]
        if not isinstance(concept, dict) or not isinstance(concept.get("units"), dict):
            raise FinancialDataError("Invalid concept or units structure.")
        records = concept["units"].get("USD", [])
        if not isinstance(records, list):
            raise FinancialDataError("USD records must be a list.")
        for record in records:
            if not isinstance(record, dict):
                raise FinancialDataError("A financial record must be an object.")
            # Match the actual reporting dates, not the filing's 'fy' field.
            # A 2026 filing can repeat a full-year result for 2024 or 2025.
            if (
                record.get("form") not in ("10-K", "10-K/A")
                or record.get("start") != start
                or record.get("end") != end
            ):
                continue
            try:
                filed = _iso_date(record.get("filed"))
                if filed < end:
                    raise ValueError("Filing predates the reported year end.")
                if as_of is not None and filed > as_of:
                    continue
                # bool is a Python int subclass; it is not a financial value.
                if type(record.get("val")) is not int:
                    raise ValueError("An annual USD value must be an integer.")
                accession = record.get("accn")
                if not isinstance(accession, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
                    raise ValueError("Invalid filing identifier.")
            except ValueError as exc:
                raise FinancialDataError("An annual matching record is invalid.") from exc
            candidates.append({
                "concept": tag,
                "value": record["val"],
                "filed": filed,
                "filing_identifier": accession,
                "form": record["form"],
                "url": _filing_url(ticker, accession),
            })

    if not candidates:
        return {"status": "not_found", **base, "message": (
            "No matching annual USD record was found in the downloaded data "
            "within the requested filing-date cutoff."
        )}

    # Find the latest date across ALL mapped tags, before choosing a tag.
    # This handles companies that change their revenue tag between filings.
    latest_date = max(item["filed"] for item in candidates)
    latest = [item for item in candidates if item["filed"] == latest_date]
    if len({item["value"] for item in latest}) != 1:
        return {"status": "conflict", **base, "filed": latest_date,
                "message": "Latest matching annual records disagree; no value selected.",
                "candidates": latest}

    # Choose the first configured tag at that latest date, then deduplicate
    # its filing references. Other mapped tags were checked for disagreement.
    chosen_tag = latest[0]["concept"]
    sources = {item["filing_identifier"]: item for item in latest if item["concept"] == chosen_tag}
    filing_ids = sorted(sources)
    return {
        "status": "ok", **base,
        "concept": chosen_tag,
        "value": latest[0]["value"],
        "filed": latest_date,
        "filing_identifiers": filing_ids,
        "sources": [sources[key] for key in filing_ids],
    }


def get_annual_financial_fact(
    ticker: str, metric: str, year: int, *, as_of: str | None = None,
    data_dir: Path | None = None,
) -> dict:
    """Read a cached file and select a value. Missing files are errors, not missing facts."""
    ticker = normalise_ticker(ticker)
    annual_period(ticker, year)
    if not isinstance(metric, str) or metric not in METRICS:
        raise ValueError("Metric must be revenue, operating_income or net_income.")
    if as_of is not None:
        _iso_date(as_of)
    path = Path(data_dir if data_dir is not None else DATA_DIR) / f"{ticker}_companyfacts.json"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {"status": "error", "code": "data_file_missing", "ticker": ticker,
                "message": f"Run: python download_company_facts.py {ticker}"}
    except OSError:
        return {"status": "error", "code": "data_file_unreadable", "ticker": ticker,
                "message": "The local company data file could not be read."}
    try:
        facts = json.loads(raw)
        result = select_annual_fact(facts, ticker, metric, year, as_of=as_of)
    except (UnicodeError, json.JSONDecodeError, FinancialDataError):
        return {"status": "error", "code": "invalid_data_file", "ticker": ticker,
                "message": "The local SEC data is invalid or belongs to another company."}
    # This fingerprint identifies the exact local snapshot used, including
    # formatting. It is useful for reproducibility, not proof of authenticity.
    result["snapshot_sha256"] = hashlib.sha256(raw).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker", help="MSFT, GOOGL or GOOG")
    parser.add_argument("metric", choices=list(METRICS))
    parser.add_argument("year", type=int, help="Fiscal year, for example 2025")
    parser.add_argument("--as-of", help="Optional latest filing date: YYYY-MM-DD")
    args = parser.parse_args()
    try:
        result = get_annual_financial_fact(args.ticker, args.metric, args.year, as_of=args.as_of)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
