"""Load company identities from configuration; no financial calendars live here."""

import json
from pathlib import Path
import re

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data" / "raw"


def load_companies(path=None):
    document = json.loads(Path(path or PROJECT_DIR / "companies.json").read_text(encoding="utf-8"))
    companies, aliases, ciks = {}, {}, set()
    for company in document["companies"]:
        ticker, cik = company["ticker"], company["cik"]
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", ticker):
            raise ValueError("Invalid company ticker in companies.json.")
        if not isinstance(cik, str) or not re.fullmatch(r"\d{10}", cik):
            raise ValueError("Company CIK must be a ten-digit string.")
        if ticker in companies or cik in ciks:
            raise ValueError("Duplicate company identity in companies.json.")
        companies[ticker] = company
        ciks.add(cik)
        for alias in [ticker, *company.get("aliases", [])]:
            alias = alias.strip().upper()
            if alias in aliases:
                raise ValueError("Duplicate company alias in companies.json.")
            aliases[alias] = ticker
    return companies, aliases


COMPANIES, ALIASES = load_companies()


def normalise_ticker(ticker):
    if not isinstance(ticker, str) or ticker.strip().upper() not in ALIASES:
        raise ValueError("Choose a company from companies.json using its ticker or alias.")
    return ALIASES[ticker.strip().upper()]


def companyfacts_url(ticker):
    cik = COMPANIES[normalise_ticker(ticker)]["cik"]
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def submissions_url(ticker):
    cik = COMPANIES[normalise_ticker(ticker)]["cik"]
    return f"https://data.sec.gov/submissions/CIK{cik}.json"


def filing_url(ticker, accession):
    if not isinstance(accession, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        raise ValueError("Invalid SEC filing identifier.")
    cik = int(COMPANIES[normalise_ticker(ticker)]["cik"])
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.html"
