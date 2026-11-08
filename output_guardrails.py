"""Verify tool evidence against local snapshots, check claims, render facts.

The cache replay detects changed/mislabelled tool results. It reuses the same
deterministic lookup; it is NOT an independent audit of that lookup or SEC data.
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from company_registry import COMPANIES, DATA_DIR, filing_url
from financial_contracts import FinancialFactArguments, TOOL_NAME, MAX_LOOKUPS
from financial_data import get_annual_financial_fact


class GuardrailError(ValueError):
    """A draft or its evidence must not be returned as an answer."""


class FinancialClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    evidence_id: str
    ticker: str
    company: str
    cik: str
    metric: str
    concept: str
    fiscal_year: int
    period_start: str
    period_end: str
    value: int
    unit: str


class AnswerDraft(BaseModel):
    # The model selects claims or a fixed reply; it cannot add arbitrary prose.
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal[
        "financial", "ask_company", "ask_metric", "ask_year", "clarify_request",
        "unsupported", "define_net_income", "define_operating_income",
        "define_revenue", "define_profit", "compare_revenue_profit",
    ]
    claims: list[FinancialClaim]


METRIC_LABELS = {
    "revenue": "revenue",
    "operating_income": "operating income",
    "net_income": "net income attributable to the parent",
}

DIRECT_REPLIES = {
    "ask_company": "Which company would you like? Please send the full question again with the company included.",
    "ask_metric": "Which measure would you like: revenue, operating income or net income? Please send the full question again.",
    "ask_year": "Which fiscal year would you like? Please send the full question again with the year included.",
    "clarify_request": "Please specify the company, annual financial measure and fiscal year in one complete question.",
    "unsupported": (
        f"This version retrieves annual revenue, operating income and net income for configured companies, "
        f"with up to {MAX_LOOKUPS} company/measure/year lookups per question. Available data varies by company. "
        "It does not yet calculate growth, margins or differences, retrieve quarters, or provide forecasts. "
        "Configured tickers: " + ", ".join(COMPANIES) + "."
    ),
    "define_net_income": (
        "Net income is profit or loss after expenses, including interest and taxes. "
        "This lookup uses net income attributable to the parent, excluding the portion attributable to noncontrolling interests."
    ),
    "define_operating_income": "Operating income is profit or loss from operations after operating expenses; it generally excludes interest and income taxes.",
    "define_revenue": "Revenue is income from selling goods or services before the associated expenses are deducted.",
    "define_profit": "Profit is income minus the relevant costs. Gross profit, operating income and net income subtract different sets of costs.",
    "compare_revenue_profit": "Revenue is income from selling goods or services before expenses. Profit is what remains after the relevant costs are deducted.",
}


def _canonical_json(value):
    # Unlike Python equality, JSON distinguishes True from 1 and 1.0 from 1.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def verify_evidence(evidence, *, data_dir=None):
    """Check request/result binding and exact agreement with the current cache.

No network or model calls. Snapshot hashes are included in the comparison,
so a cache change during a request causes rejection rather than mixed evidence.
"""
    if not isinstance(evidence, list) or len(evidence) > MAX_LOOKUPS:
        raise GuardrailError("Invalid evidence collection.")
    seen_ids, seen_requests = set(), set()
    for item in evidence:
        try:
            if not isinstance(item, dict) or set(item) != {"id", "tool", "arguments", "result"}:
                raise ValueError("Invalid evidence envelope")
            evidence_id = item["id"]
            if (not isinstance(evidence_id, str) or evidence_id != f"e{len(seen_ids) + 1}"
                    or evidence_id in seen_ids or item["tool"] != TOOL_NAME):
                raise ValueError("Invalid evidence identity")
            arguments = FinancialFactArguments.model_validate(item["arguments"]).model_dump()
            if _canonical_json(arguments) != _canonical_json(item["arguments"]):
                raise ValueError("Arguments must be canonical")
            request_key = _canonical_json(arguments)
            if request_key in seen_requests:
                raise ValueError("Duplicate financial request")
            result = item["result"]
            if not isinstance(result, dict) or result.get("status") not in {"ok", "not_found", "conflict", "unsupported_period"}:
                raise ValueError("Invalid result status")
            verified = get_annual_financial_fact(**arguments, data_dir=DATA_DIR if data_dir is None else data_dir)
            if verified.get("status") == "error" or _canonical_json(result) != _canonical_json(verified):
                raise ValueError("Result differs from the local snapshots")
            seen_ids.add(evidence_id)
            seen_requests.add(request_key)
        except (OSError, ValueError, TypeError, KeyError):
            raise GuardrailError("The tool evidence does not match its request and local SEC snapshots.") from None


def check_and_render(draft_json: str, evidence: list[dict], *, data_dir=None) -> tuple[str, dict]:
    try:
        draft = AnswerDraft.model_validate_json(draft_json)
    except (ValidationError, TypeError):
        raise GuardrailError("The AI returned an invalid answer structure.") from None

    if not evidence:
        if draft.kind == "financial" or draft.claims:
            raise GuardrailError("A financial claim has no retrieved evidence.")
        return DIRECT_REPLIES[draft.kind], {"status": "not_applicable", "checked_claims": 0}

    if draft.kind != "financial":
        raise GuardrailError("The draft did not account for the tool results.")
    verify_evidence(evidence, data_dir=data_dir)

    expected, lines = {}, []
    fields = set(FinancialClaim.model_fields) - {"evidence_id"}
    for item in evidence:
        evidence_id, result, arguments = item["id"], item["result"], item["arguments"]
        ticker, year, metric = arguments["ticker"], arguments["year"], arguments["metric"]
        subject = f"{COMPANIES[ticker]['name']} ({ticker}), FY{year} {METRIC_LABELS[metric]}"
        cutoff = f" using filings dated on or before {arguments['as_of']}" if arguments["as_of"] else ""
        status = result["status"]
        if status == "not_found":
            lines.append(f"{subject}: no supported matching annual record was found in the cached SEC data{cutoff}. [{evidence_id}]")
        elif status == "conflict":
            lines.append(f"{subject}: conflicting records or reporting periods prevent a reliable selection{cutoff}. No figure is reported. [{evidence_id}]")
        elif status == "unsupported_period":
            lines.append(f"{subject}: a supported full-year reporting period could not be established{cutoff}. No figure is reported. [{evidence_id}]")
        else:
            try:
                expected[evidence_id] = FinancialClaim.model_validate({
                    "evidence_id": evidence_id, **{key: result[key] for key in fields},
                })
            except (KeyError, ValidationError):
                raise GuardrailError("The successful result has invalid financial fields.") from None
            links = "; ".join(
                f"[SEC filing {accession}]({filing_url(ticker, accession)})"
                for accession in result["filing_identifiers"]
            )
            lines.append(
                f"{subject}: {result['unit']} {result['value']:,}. "
                f"Reporting period: {result['period_start']} to {result['period_end']}. "
                f"Latest matching annual filing in the cache{cutoff}, filed {result['filed']}. "
                f"{links}. [{evidence_id}]"
            )

    seen_claims = set()
    for claim in draft.claims:
        evidence_id = claim.evidence_id
        if evidence_id in seen_claims or evidence_id not in expected:
            raise GuardrailError("A claim has a duplicate or unsupported evidence reference.")
        if claim.model_dump() != expected[evidence_id].model_dump():
            raise GuardrailError("A financial claim does not match its evidence.")
        seen_claims.add(evidence_id)
    if seen_claims != set(expected):
        raise GuardrailError("The draft omitted a retrieved financial result.")

    return "\n\n".join(lines), {"status": "passed", "checked_claims": len(seen_claims)}
