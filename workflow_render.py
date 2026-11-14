"""Code renders financial figures; free narrative cannot supply numeric literals."""
import re
import unicodedata
from decimal import Decimal

from research_portal import Citation, ResearchNote
from workflow_contracts import Draft, WorkflowError, guard_text
from workflow_support import support_issues


NUMBER_WORDS = re.compile(r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|trillion|half|quarter|double|triple|percent)\b", re.I)


def check_prose(text, field="narrative"):
    text = unicodedata.normalize("NFKC", text)
    guard_text(text)
    if any(c.isdigit() for c in text) or NUMBER_WORDS.search(text):
        tokens = re.findall(r"\S*\d\S*", text) + [m.group() for m in NUMBER_WORDS.finditer(text)]
        examples = ", ".join(repr(t[:60]) for t in tokens[:6])
        raise WorkflowError("unverified_number", f"{field}: numeric text {examples} is forbidden, including years and Item labels. Rewrite this field without digits or number words. Use a title such as 'Company research comparison'; refer to 'the requested fiscal periods' and 'the risk factors section'. Code supplies financial figures, periods and citations.")
    if re.search(r"https?://|[<>\[\]]", text) or any(ord(c)<32 and c not in "\n\t" for c in text):
        raise WorkflowError("unsafe_markup", "Narrative cannot add links, HTML, citation syntax or terminal controls.")


def draft_prose_issues(draft):
    """Report every affected field in the same bounded critic revision."""
    fields = [("title", draft["title"]), *[(f"paragraphs[{i}].text", p["text"])
              for i,p in enumerate(draft["paragraphs"])]]
    issues = []
    for field, text in fields:
        try:
            check_prose(text, field)
        except WorkflowError as exc:
            issues.append(f"{field} [{exc.code}]: {exc.message}")
    return issues


def selected_evidence(draft, records):
    ids = set()
    for paragraph in draft["paragraphs"]:
        ids.update(paragraph["evidence_ids"])
    # The fixed comparison table includes every successfully checked margin.
    for key, r in records.items():
        if r["kind"] == "calculation":
            ids.add(key)
            ids.update(r["inputs"])
    if ids-records.keys():
        raise WorkflowError("unknown_evidence", "A draft cites an unknown source.")
    return sorted(ids)


def render_note(draft, records, request):
    draft = Draft.model_validate(draft).model_dump()
    check_prose(draft["title"], "title")
    for i,p in enumerate(draft["paragraphs"]):
        check_prose(p["text"], f"paragraphs[{i}].text")
    ids = selected_evidence(draft, records)
    issues = support_issues(draft, records, request)
    if issues:
        raise WorkflowError("unsupported_narrative", "\n".join(issues))
    if not ids:
        raise WorkflowError("no_evidence", "No verified evidence supports this note.")
    body = [f"Companies: {', '.join(request['tickers'])}. Fiscal years: {', '.join(map(str, request['years']))}.",
            "Fiscal years are company reporting periods and may differ from calendar years. Compare the actual start/end dates below."]
    table = ["| Company | FY | Reporting period | Revenue (USD) | Operating income (USD) | Operating margin |",
             "|---|---|---|---:|---:|---:|"]
    figures = 0
    covered = set()
    for key in sorted(ids, key=lambda k: (records[k]["ticker"], records[k]["year"], k)):
        r = records[key]
        if r["kind"] != "calculation":
            continue
        income_id, revenue_id = r["inputs"]
        income, revenue = records[income_id], records[revenue_id]
        f = income["fact"]
        table.append(f"| {r['ticker']} | {r['year']} | {f['period_start']} to {f['period_end']} | {Decimal(revenue['value']):,.0f} [{revenue_id}] | {Decimal(income['value']):,.0f} [{income_id}] | {r['value']}% [{key}] |")
        figures += 3
        covered.add((r["ticker"], r["year"]))
    missing = [(t,y) for t in request["tickers"] for y in request["years"] if (t,y) not in covered]
    body.append("\n".join(table))
    if missing:
        body.append("Unavailable calculations (no substitute measure used): "+", ".join(f"{t} FY{y}" for t,y in missing)+".")
    else:
        body.append("Financial coverage: saved calculations cover every requested company/fiscal-year pair.")
    body.append("Margin formula: operating income divided by revenue, multiplied by one hundred; rounded to two decimal places using Decimal half-even rounding.")
    for p in draft["paragraphs"]:
        body.append(p["text"]+" "+" ".join(f"[{key}]" for key in p["evidence_ids"]))
    if request["include_risks"]:
        body.append("Risk comparisons describe the cited retrieved passages. Retrieval alone cannot establish that a risk was added or removed from an entire filing; complete-section review remains necessary for such claims.")
    citations = [Citation(evidence_id=key, source_sha256=records[key]["source_sha256"], source_url=records[key]["source_url"]) for key in ids]
    body.append("Sources:\n"+"\n".join(f"- [{c.evidence_id}]({c.source_url})" for c in citations))
    note = ResearchNote(title=draft["title"], body_markdown="\n\n".join(body), tickers=request["tickers"], fiscal_years=request["years"], citations=citations)
    return note, {"rendered_financial_figures": figures, "verified_financial_figures": figures,
                  "verified_share": 1.0 if figures else None, "missing_company_years": [list(p) for p in missing],
                  "metric_scope": "Code-rendered financial table values; qualitative support is a separate critic check."}
