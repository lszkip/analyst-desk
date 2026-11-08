"""Check financial claims against tool evidence, then render trusted values."""

from datetime import date
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class GuardrailError(ValueError):
    """The draft must not be returned as an answer."""


class FinancialClaim(BaseModel):
    # These fields describe one proposed financial claim from the AI.
    model_config = ConfigDict(extra="forbid", strict=True)

    evidence_id: str
    company: str
    concept: str
    fiscal_year: int
    period_start: str
    period_end: str
    value: int
    unit: str


class AnswerDraft(BaseModel):
    # There is deliberately no unrestricted answer-text field.
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal[
        "financial", "ask_year", "future_year", "unsupported",
        "define_net_income", "define_revenue", "define_profit",
        "compare_revenue_profit",
    ]
    claims: list[FinancialClaim]


# The AI selects an appropriate reply; Python supplies its actual text.
DIRECT_REPLIES = {
    "ask_year": (
        "Which Microsoft fiscal year would you like? "
        "Please send the full question again with the year included."
    ),
    "future_year": (
        "Actual reported financial results are not available for future "
        "fiscal years. This tool does not provide forecasts."
    ),
    "unsupported": (
        "This version supports Microsoft's annual net income for up to "
        "three specified fiscal years, plus basic definitions of revenue "
        "and profit. It does not yet support other companies, metrics, "
        "or calculated comparisons."
    ),
    "define_net_income": (
        "Net income is the profit or loss remaining after a company's "
        "expenses, including interest and taxes, are accounted for."
    ),
    "define_revenue": (
        "Revenue is the income a business earns from selling goods "
        "or services, before deducting its expenses."
    ),
    "define_profit": (
        "Profit is income minus the relevant costs. Gross profit, "
        "operating profit and net income subtract different sets of costs."
    ),
    "compare_revenue_profit": (
        "Revenue is income from selling goods or services before expenses "
        "are deducted. Profit is what remains after the relevant costs "
        "are deducted."
    ),
}


def check_and_render(draft_json: str, evidence: list[dict]) -> tuple[str, dict]:
    # Local validation is required even when the provider promises JSON.
    try:
        draft = AnswerDraft.model_validate_json(draft_json)
    except ValidationError:
        raise GuardrailError("The AI returned an invalid answer structure.") from None

    if not evidence:
        if draft.kind == "financial" or draft.claims:
            raise GuardrailError("A financial claim has no retrieved evidence.")
        return DIRECT_REPLIES[draft.kind], {
            "status": "not_applicable",
            "checked_claims": 0,
        }

    # After a lookup, the draft must account for the retrieved results.
    if draft.kind != "financial":
        raise GuardrailError("The draft did not account for the tool results.")

    expected = {}
    lines = []
    seen_evidence = set()
    claim_fields = set(FinancialClaim.model_fields) - {"evidence_id"}

    for item in evidence:
        try:
            evidence_id = item["id"]
            year = item["arguments"]["year"]
            result = item["result"]
            status = result["status"]

            if (
                item["tool"] != "get_msft_annual_net_income"
                or type(year) is not int
                or not 1900 <= year <= 2100
                or not isinstance(evidence_id, str)
                or not evidence_id
                or evidence_id in seen_evidence
            ):
                raise ValueError("Invalid evidence metadata")
            seen_evidence.add(evidence_id)

            if status == "not_found":
                lines.append(
                    f"No matching annual net income record for Microsoft "
                    f"FY{year} was found in the downloaded data. [{evidence_id}]"
                )
                continue

            if status == "conflict":
                lines.append(
                    f"The matching Microsoft FY{year} records contain "
                    f"conflicting net income values. No figure is reported. "
                    f"[{evidence_id}]"
                )
                continue

            if status != "ok":
                raise ValueError("Unexpected tool status")

            # Validate the evidence's types before comparing it with the draft.
            fact = FinancialClaim.model_validate({
                "evidence_id": evidence_id,
                **{field: result[field] for field in claim_fields},
            })

            # These rules deliberately match our current Microsoft-only tool.
            if (
                fact.company != "MICROSOFT CORP"
                or fact.concept != "NetIncomeLoss"
                or fact.unit != "USD"
                or fact.fiscal_year != year
                or fact.period_start != f"{year - 1}-07-01"
                or fact.period_end != f"{year}-06-30"
            ):
                raise ValueError("Unexpected company, concept or period")

            filed = result["filed"]
            date.fromisoformat(filed)
            filing_ids = result["filing_identifiers"]
            if (
                not isinstance(filing_ids, list)
                or not filing_ids
                or any(
                    not isinstance(value, str)
                    or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", value)
                    for value in filing_ids
                )
            ):
                raise ValueError("Invalid filing identifiers")

            expected[evidence_id] = fact
            # Render from the tool evidence, never from unchecked AI prose.
            lines.append(
                f"Microsoft's annual net income for FY{year} was "
                f"{fact.unit} {fact.value:,}. Reporting period: "
                f"{fact.period_start} to {fact.period_end}. "
                f"Source filing(s): {', '.join(filing_ids)}; "
                f"filed {filed}. [{evidence_id}]"
            )
        except (KeyError, TypeError, ValueError):
            raise GuardrailError("The tool evidence is incomplete or invalid.") from None

    # Each successful result must be represented exactly once in the draft.
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

    return "\n\n".join(lines), {
        "status": "passed",
        "checked_claims": len(seen_claims),
    }
