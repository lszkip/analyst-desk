"""Identify a fiscal year by joining XBRL facts to SEC annual-filing metadata.

The XBRL 'fy' field labels the filing, including its comparative figures.
We therefore require the fact's end date to equal that filing's reportDate
before using 'fy' to identify the requested year's CURRENT annual period.
After identification, value selection can use later comparative filings.
When explicitly prepared, definitions from original filings can resolve an
incorrect 'fy' label. Their evidence is returned in fiscal_year_resolutions.
"""

from datetime import date
import re

from company_registry import COMPANIES, normalise_ticker, filing_url
from sec_fiscal_years import validate_year_evidence

ANNUAL_FORMS = ("10-K", "10-K/A")
METRICS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
}


class FinancialDataError(ValueError):
    """A local SEC file is invalid, inconsistent or incomplete."""


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise FinancialDataError("Expected a date in YYYY-MM-DD format.")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise FinancialDataError("Invalid calendar date.") from exc
    return value


def validate_year(year):
    if type(year) is not int or not 1900 <= year <= 2100:
        raise ValueError("Year must be an integer between 1900 and 2100.")


def validate_identity(document, ticker):
    if not isinstance(document, dict):
        raise FinancialDataError("Expected a JSON object.")
    cik = document.get("cik")
    if type(cik) not in (str, int) or not re.fullmatch(r"\d{1,10}", str(cik)):
        raise FinancialDataError("Invalid company identifier.")
    if int(cik) != int(COMPANIES[normalise_ticker(ticker)]["cik"]):
        raise FinancialDataError("The file belongs to another company.")


def validate_companyfacts(facts, ticker):
    validate_identity(facts, ticker)
    if not isinstance(facts.get("entityName"), str) or not facts["entityName"].strip():
        raise FinancialDataError("Missing company name.")
    if not isinstance(facts.get("facts"), dict):
        raise FinancialDataError("Missing financial facts.")
    if not isinstance(facts["facts"].get("us-gaap"), dict):
        raise FinancialDataError("This lookup requires US-GAAP facts.")


def read_filings(history, ticker):
    validate_identity(history, ticker)
    if history.get("complete") is not True or not isinstance(history.get("annual_filings"), list):
        raise FinancialDataError("Annual filing history is incomplete. Run the downloader.")
    filings = {}
    for row in history["annual_filings"]:
        if not isinstance(row, dict) or row.get("form") not in ANNUAL_FORMS:
            raise FinancialDataError("Invalid annual filing metadata.")
        accession = row.get("accessionNumber")
        try:
            filing_url(ticker, accession)
            iso_date(row.get("filingDate"))
            # Some historic amendments do not supply a report date. Such
            # entries can be retained, but cannot identify an annual period.
            if row.get("reportDate"):
                iso_date(row["reportDate"])
                if row["filingDate"] < row["reportDate"]:
                    raise FinancialDataError("Filing date precedes report date.")
            if "fiscal_year_evidence" in row:
                validate_year_evidence(row["fiscal_year_evidence"], normalise_ticker(ticker), row)
        except ValueError as exc:
            raise FinancialDataError("Invalid annual filing metadata.") from exc
        if accession in filings and filings[accession] != row:
            raise FinancialDataError("Conflicting metadata for one filing.")
        filings[accession] = row
    return filings


def metric_records(facts, metric):
    """Yield only USD records, validating the structure of supported concepts."""
    for tag in METRICS[metric]:
        concept = facts["facts"]["us-gaap"].get(tag)
        if concept is None:
            continue
        if not isinstance(concept, dict) or not isinstance(concept.get("units"), dict):
            raise FinancialDataError("Invalid concept structure.")
        rows = concept["units"].get("USD", [])
        if not isinstance(rows, list):
            raise FinancialDataError("USD records must be a list.")
        for row in rows:
            if not isinstance(row, dict):
                raise FinancialDataError("Invalid financial record.")
            yield tag, row


def checked_filing(record, filings):
    accession = record.get("accn")
    if not isinstance(accession, str) or accession not in filings:
        raise FinancialDataError("Fact is missing from filing history. Refresh the SEC cache.")
    metadata = filings[accession]
    if record.get("form") != metadata["form"] or record.get("filed") != metadata["filingDate"]:
        raise FinancialDataError("Fact and filing metadata disagree.")
    return metadata


def select_annual_period(facts, filings, ticker, year, as_of=None):
    """Return a period with evidence; never invent a calendar for an unknown year."""
    validate_year(year)
    periods, short_periods, resolutions = {}, set(), {}
    for metric in METRICS:
        for tag, record in metric_records(facts, metric):
            if record.get("form") not in ANNUAL_FORMS or record.get("fp") != "FY":
                continue
            accession = record.get("accn")
            metadata = filings.get(accession, {}) if isinstance(accession, str) else {}
            resolution = metadata.get("fiscal_year_evidence")
            effective_year = resolution["fiscal_year"] if resolution else record.get("fy")
            if record.get("fy") != year and effective_year != year:
                continue
            filed = iso_date(record.get("filed"))
            if as_of is not None and filed > as_of:
                continue
            metadata = checked_filing(record, filings)
            if not metadata.get("reportDate") or record.get("end") != metadata["reportDate"]:
                continue  # A comparative figure, not this filing's current year.
            if resolution:
                resolutions[accession] = {"filing_identifier": accession, "filed": filed, **resolution}
            if effective_year != year:
                continue
            start, end = iso_date(record.get("start")), iso_date(record.get("end"))
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
            if days <= 0 or filed < end:
                raise FinancialDataError("Invalid period in an annual fact.")
            if not 350 <= days <= 380:
                short_periods.add((start, end))
                continue  # Exclude quarters; unusual transition years are unsupported.
            evidence = {"filing_identifier": record["accn"], "filed": filed,
                        "form": record["form"], "fiscal_year_focus": year,
                        "report_date": metadata["reportDate"], "concept": tag,
                        "url": filing_url(ticker, record["accn"])}
            if resolution:
                evidence.update({"fiscal_year_focus": record.get("fy"),
                                 "resolved_fiscal_year": year,
                                 "fiscal_year_basis": resolution["method"]})
            periods.setdefault((start, end), {})[(record["accn"], tag)] = evidence
    audit = {"fiscal_year_resolutions": [resolutions[key] for key in sorted(resolutions)]} if resolutions else {}
    if len(periods) > 1:
        return {**audit, "status": "conflict", "code": "annual_period_conflict",
                "message": "More than one annual reporting period matches this fiscal year; no period selected.",
                "candidate_periods": [{"start": p[0], "end": p[1]} for p in sorted(periods)]}
    if not periods:
        if short_periods:
            return {**audit, "status": "unsupported_period", "code": "nonstandard_annual_period",
                    "message": "Only nonstandard or shorter periods were found. This version requires a full 350–380 day year."}
        return {**audit, "status": "not_found", "code": "annual_period_not_found",
                "message": "No supported annual period could be established for this fiscal year from the cached filings and USD facts."}
    (start, end), evidence = next(iter(periods.items()))
    return {**audit, "status": "ok", "period_start": start, "period_end": end,
            "period_days": (date.fromisoformat(end) - date.fromisoformat(start)).days + 1,
            "period_selection_policy": "filing_fiscal_year_and_report_date_with_full_year_duration",
            "period_sources": [evidence[key] for key in sorted(evidence)]}
