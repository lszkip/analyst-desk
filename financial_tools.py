import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def get_msft_annual_net_income(year: int) -> dict:
    # Read our existing Microsoft data. No new download is needed.
    facts_path = PROJECT_DIR / "data" / "raw" / "MSFT_companyfacts.json"
    facts = json.loads(facts_path.read_text(encoding="utf-8"))

    records = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]

    # For the Microsoft years we are working with:
    # fiscal year 2026 runs from July 2025 through June 2026.
    start = f"{year - 1}-07-01"
    end = f"{year}-06-30"

    # Keep annual-report entries covering exactly the requested period.
    matches = [
        record
        for record in records
        if record["form"] in ("10-K", "10-K/A")
        and record.get("start") == start
        and record["end"] == end
    ]

    if not matches:
        return {
            "status": "not_found",
            "message": f"No annual net income found for Microsoft FY{year}.",
        }

    # The same period may appear again in later annual reports.
    # Our policy here is to use the most recently filed version.
    latest_date = max(record["filed"] for record in matches)
    latest = [
        record for record in matches
        if record["filed"] == latest_date
    ]

    # If those entries disagree, report the problem instead of guessing.
    values = {record["val"] for record in latest}
    if len(values) != 1:
        return {
            "status": "conflict",
            "message": "Matching filings contain different net income values.",
            "records": latest,
        }

    return {
        "status": "ok",
        "company": facts["entityName"],
        "concept": "NetIncomeLoss",
        "fiscal_year": year,
        "period_start": start,
        "period_end": end,
        "value": latest[0]["val"],
        "unit": "USD",
        "filed": latest_date,
        "filing_identifiers": sorted({record["accn"] for record in latest}),
    }


# This section runs only when we execute this file directly.
# Later, other files can import the function without running these examples.
if __name__ == "__main__":
    for year in (2024, 2025, 2026):
        result = get_msft_annual_net_income(year)
        print(json.dumps(result, indent=2))