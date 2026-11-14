"""Financial prose assembled from checked receipts, never generated quantities.

Comparisons use the same rounded Decimal margins displayed in the financial
table. Source replay and paragraph-level semantic review remain separate gates.
"""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import re
import unicodedata

from company_registry import filing_url
from workflow_contracts import ResearchRequest, WorkflowError, digest


def _require(condition, message):
    if not condition:
        raise WorkflowError("invalid_financial_comparisons", message)


def _fact(key, record, ticker, year, metric):
    fact = record["fact"]
    _require(record["kind"] == "fact" and record["ticker"] == ticker
             and type(record["year"]) is int and record["year"] == year
             and record["metric"] == metric and record["unit"] == "USD",
             "A comparison input has incompatible scope, measure or units.")
    _require(fact["status"] == "ok" and fact["ticker"] == ticker
             and type(fact["fiscal_year"]) is int and fact["fiscal_year"] == year
             and fact["metric"] == metric and fact["unit"] == "USD"
             and type(fact["value"]) is int and record["value"] == str(fact["value"]),
             "A comparison input differs from its financial source receipt.")
    _require(date.fromisoformat(fact["period_start"]) < date.fromisoformat(fact["period_end"]),
             "A comparison input has invalid reporting dates.")
    accession = fact["filing_identifiers"][0]
    expected_id = f"f_{fact['concept']}_{fact['period_start']}_{fact['period_end']}_{accession}_{digest(fact)[:12]}"
    _require(key == expected_id and record["source_url"] == filing_url(ticker, accession)
             and record["source_sha256"] == fact["snapshot_sha256"]
             and re.fullmatch(r"[a-f0-9]{64}", fact["snapshot_sha256"]) is not None
             and record["arguments"] == {"ticker": ticker, "year": year, "metric": metric, "as_of": None},
             "A comparison input has inconsistent identity or source provenance.")
    number = Decimal(record["value"])
    _require(number.is_finite(), "A comparison input is not finite.")
    return number


def _checked_margins(records, request):
    try:
        validated = ResearchRequest.model_validate(request).model_dump()
        _require(validated == request, "The financial comparison request is not canonical.")
        allowed = {(ticker, year) for ticker in request["tickers"] for year in request["years"]}
        margins = {}
        for key, record in records.items():
            if record.get("kind") != "calculation":
                continue
            pair = (record["ticker"], record["year"])
            _require(type(pair[1]) is int and pair in allowed,
                     "A margin calculation is outside the approved scope.")
            _require(pair not in margins, "Duplicate margin calculations exist for a company and fiscal period.")
            inputs = record["inputs"]
            _require(isinstance(inputs, list) and len(inputs) == 2 and len(set(inputs)) == 2,
                     "A margin needs distinct operating-income and revenue input IDs.")
            income_id, revenue_id = inputs
            income, revenue = records[income_id], records[revenue_id]
            numerator = _fact(income_id, income, *pair, "operating_income")
            denominator = _fact(revenue_id, revenue, *pair, "revenue")
            _require(denominator > 0 and all(income["fact"][field] == revenue["fact"][field]
                     for field in ("period_start", "period_end")),
                     "Margin inputs have different reporting periods or nonpositive revenue.")
            with localcontext() as context:
                context.prec = 28
                expected = str((numerator / denominator * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
            value = Decimal(record["value"])
            _require(value.is_finite() and record["value"] == expected and record["unit"] == "%"
                     and record["formula"] == "100 * operating_income / revenue",
                     "A margin does not match its independently recomputed displayed value.")
            _require(key == "margin_" + digest([revenue_id, income_id, expected])[:32]
                     and record["source_sha256"] == income["source_sha256"]
                     and record["source_url"] == income["source_url"],
                     "A margin identity or source provenance differs from its input receipts.")
            margins[pair] = {"id": key, "value": value}
        if not margins:
            raise WorkflowError("missing_financial_comparisons", "No checked margin calculations are available for financial comparison.")
        return margins
    except WorkflowError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, InvalidOperation, ArithmeticError):
        raise WorkflowError("invalid_financial_comparisons", "Financial comparison receipts are malformed or incompatible.") from None


def _names(tickers):
    if len(tickers) == 1:
        return tickers[0]
    return ", ".join(tickers[:-1]) + " and " + tickers[-1]


def financial_paragraphs(records, request):
    """Return a deterministic financial suffix using only checked calculation IDs."""
    margins = _checked_margins(records, request)
    labels = {1: ["requested fiscal period"],
              2: ["earlier requested fiscal period", "later requested fiscal period"],
              3: ["earliest requested fiscal period", "middle requested fiscal period", "latest requested fiscal period"]}[len(request["years"])]
    paragraphs = []
    for index, year in enumerate(request["years"]):
        present = [ticker for ticker in request["tickers"] if (ticker, year) in margins]
        if not present:
            continue
        label = labels[index]
        ordered = sorted(present, key=lambda ticker: (-margins[ticker, year]["value"], ticker))
        groups = []
        for ticker in ordered:
            if groups and margins[groups[-1][0], year]["value"] == margins[ticker, year]["value"]:
                groups[-1].append(ticker)
            else:
                groups.append([ticker])
        if len(present) == 1:
            sentences = [f"An operating-margin calculation is available for {present[0]} for the {label}."]
        elif len(groups) == 1:
            sentences = [f"Among the available calculations for the {label}, {_names(groups[0])} have equal calculated operating margins at the displayed precision."]
        else:
            ranking = ", then ".join(_names(group) + (" (equal at the displayed precision)" if len(group) > 1 else "") for group in groups)
            sentences = [f"Among the available calculations for the {label}, calculated operating margins rank from higher to lower as {ranking}, at the displayed precision."]
        ids = {margins[ticker, year]["id"] for ticker in present}
        if index:
            previous_year = request["years"][index - 1]
            for ticker in present:
                if (ticker, previous_year) not in margins:
                    continue
                current, previous = margins[ticker, year], margins[ticker, previous_year]
                direction = "increased" if current["value"] > previous["value"] else "decreased" if current["value"] < previous["value"] else "was unchanged"
                sentences.append(f"{ticker}'s calculated operating margin {direction} from the {labels[index - 1]} to the {label} at the displayed precision.")
                ids.add(previous["id"])
        paragraphs.append({"text": " ".join(sentences), "evidence_ids": sorted(ids)})
    return paragraphs


def financial_narrative_issues(draft, records, request):
    """Reject altered finance prose and finance claims in the free risk section."""
    try:
        expected = financial_paragraphs(records, request)
    except WorkflowError as exc:
        return [f"financial narrative [{exc.code}]: {exc.message}"]
    paragraphs = draft.get("paragraphs", [])
    if not isinstance(paragraphs, list) or len(paragraphs) < len(expected) or paragraphs[-len(expected):] != expected:
        return ["Financial paragraphs must exactly match the current checked financial narrative as the draft suffix."]
    preceding = paragraphs[:-len(expected)]
    if not request["include_risks"]:
        return [] if not preceding else ["A finance-only draft must contain exactly the deterministic financial paragraphs."]
    issues = []
    if not preceding:
        issues.append("Requested risk analysis is missing before the deterministic financial paragraphs.")
    if len(preceding) > 4:
        issues.append("The free risk section must contain at most four paragraphs.")
    for index, paragraph in enumerate(preceding):
        text = unicodedata.normalize("NFKC", paragraph.get("text", ""))
        if re.search(r"\bmargins?\b", text, re.I):
            issues.append(f"paragraphs[{index}]: operating-margin claims belong only in the checked financial section.")
        if any(records.get(key, {}).get("kind") != "passage" for key in paragraph.get("evidence_ids", [])):
            issues.append(f"paragraphs[{index}]: free risk prose may cite only registered source passages.")
    return issues
