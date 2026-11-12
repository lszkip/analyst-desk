"""Evidence checks and sentence-level critic receipts, not a semantic-proof claim."""
import re
import unicodedata

from pydantic import Field

from company_registry import COMPANIES
from workflow_contracts import Strict, WorkflowError, digest


SUPPORT_VERSION = "sentence-evidence-v2-catalog"


class SupportReference(Strict):
    evidence_id: str
    quote: str = Field(max_length=300)


class SentenceSupport(Strict):
    sentence_index: int = Field(ge=0)
    supported: bool
    references: list[SupportReference] = Field(max_length=8)
    reason: str = Field(min_length=1, max_length=400)


class ParagraphSupport(Strict):
    sentences: list[SentenceSupport] = Field(min_length=1, max_length=8)
    issues: list[str] = Field(max_length=6)
    approved: bool


class SentenceSelection(Strict):
    sentence_index: int = Field(ge=0)
    supported: bool
    reference_ids: list[str] = Field(max_length=8)
    reason: str = Field(min_length=1, max_length=400)


class ParagraphSelection(Strict):
    """The model selects checked references; it never transcribes quotations."""
    sentences: list[SentenceSelection] = Field(min_length=1, max_length=8)
    issues: list[str] = Field(max_length=6)
    approved: bool


def support_catalog(paragraph, records):
    sources, references = {}, {}
    for key in sorted(set(paragraph["evidence_ids"])):
        if key not in records:
            raise WorkflowError("unknown_evidence", "A review source was never loaded.")
        record = records[key]
        source = {"kind": record["kind"], "ticker": record["ticker"], "year": record["year"]}
        if record["kind"] == "passage":
            text = record["passage"].get("text") or ""
            visible = text[:3500]
            source.update(item=record["item"], source_characters=len(text),
                          excerpt_truncated=len(text)>len(visible), untrusted_data=True)
            # Overlapping windows preserve original characters and cover the
            # visible source. Every selectable quote stays within the old bound.
            spans = set()
            for start in range(0, len(visible), 220):
                end = min(start+280, len(visible))
                if end-start < 20:
                    start = max(0, end-280)
                quote = visible[start:end]
                if len(normalise(quote)) < 20 or (start,end) in spans:
                    continue
                spans.add((start,end))
                ref = "r_"+digest([SUPPORT_VERSION,paragraph,key,record["source_sha256"],start,end,quote])[:16]
                references[ref] = {"evidence_id":key, "quote":quote,
                                   "source_text_start":start, "source_text_end":end}
        elif record["kind"] in {"fact", "calculation"}:
            source.update(value=record["value"], unit=record["unit"])
            if record["kind"] == "calculation":
                inputs = [records[k] for k in record["inputs"]]
                period = inputs[0]["fact"]
                source.update(metric="operating_margin", formula=record["formula"],
                    input_values=[{"metric":r["metric"],"value":r["value"],"unit":r["unit"]} for r in inputs])
            else:
                period = record["fact"]
                source.update(metric=record["metric"])
            source.update(period_start=period["period_start"],period_end=period["period_end"])
            ref = "r_"+digest([SUPPORT_VERSION,paragraph,key,source])[:16]
            references[ref] = {"evidence_id":key, "quote":""}
        else:
            raise WorkflowError("invalid_evidence_kind", "Unsupported critic source kind.")
        sources[key] = source
    catalog = {"version":SUPPORT_VERSION,"sources":sources,"references":references}
    from workflow_contracts import canonical
    if len(canonical(catalog).encode()) > 34000:
        raise WorkflowError("evidence_context_limit", "Too many review references. Narrow the draft's cited source scope.")
    return catalog


def selection_issues(review, catalog, paragraph, records):
    issues = []
    expected = list(range(len(sentences(paragraph["text"]))))
    if [r["sentence_index"] for r in review["sentences"]] != expected:
        issues.append("final.sentences: review every supplied sentence exactly once in order.")
    for i,row in enumerate(review["sentences"]):
        refs = row["reference_ids"]
        label = f"final.sentences[{i}].reference_ids"
        if row["supported"] and not refs:
            issues.append(label+": a supported verdict needs at least one catalog reference ID, including claims about reporting dates.")
        if len(refs) != len(set(refs)):
            issues.append(label+": select each reference ID at most once.")
        if any(ref not in catalog["references"] for ref in refs):
            issues.append(label+": select only keys from reference_catalog.references. Source evidence IDs and another paragraph's reference IDs are not selectable.")
    if not issues:
        expanded = resolve_selection(review,catalog,paragraph,records)
        issues.extend(receipt_issues(expanded,paragraph,records))
    return issues


def resolve_selection(review, catalog, paragraph, records):
    """Assemble immutable receipts from selected IDs; never choose support for a model."""
    if catalog != support_catalog(paragraph,records):
        raise WorkflowError("changed_support_catalog", "The paragraph's source reference catalog changed during review.")
    rows = []
    for row in review["sentences"]:
        if any(ref not in catalog["references"] for ref in row["reference_ids"]):
            raise WorkflowError("unknown_support_reference", "The critic selected an unknown reference.")
        rows.append({"sentence_index":row["sentence_index"], "supported":row["supported"],
            "reason":row["reason"], "references":[
                {k:catalog["references"][ref][k] for k in ("evidence_id","quote")}
                for ref in row["reference_ids"]]})
    return ParagraphSupport.model_validate({"sentences":rows,"issues":review["issues"],
                                          "approved":review["approved"]}).model_dump()


def sentences(text):
    # These exact units and indices are supplied to the critic; no model-chosen
    # subset can silently omit an inconvenient sentence from the review.
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def normalise(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def mentioned_companies(text, request):
    found = set()
    for ticker, company in COMPANIES.items():
        name = re.sub(r"\s+(?:INC\.?|CORP\.?|CORPORATION|LTD\.?)$", "", company["name"], flags=re.I)
        for alias in (ticker, name, *company.get("aliases", [])):
            if re.search(r"\b"+re.escape(alias)+r"\b", text, re.I):
                found.add(ticker)
    if re.search(r"\b(?:both|either|each|all)\s+(?:companies|company|firms|firm)\b", text, re.I):
        found.update(request["tickers"])
    return found


def support_issues(draft, records, request):
    """Conservative checks for known failure classes; the model still judges meaning."""
    covered = {(r["ticker"], r["year"]) for r in records.values() if r["kind"] == "calculation"}
    complete = all((t,y) in covered for t in request["tickers"] for y in request["years"])
    issues = []
    for index, p in enumerate(draft["paragraphs"]):
        prefix = f"paragraphs[{index}]"
        if set(p["evidence_ids"])-records.keys():
            issues.append(f"{prefix}: cites evidence that was never loaded.")
            continue
        cited = [records[k] for k in p["evidence_ids"]]
        tickers = {r["ticker"] for r in cited}
        text = normalise(p["text"])
        units = sentences(text)
        if len(units) > 8:
            issues.append(f"{prefix}: shorten to at most eight sentences for a complete bounded review.")
        missing_companies = mentioned_companies(text, request)-tickers
        if missing_companies:
            issues.append(f"{prefix}: company claims lack citations for {sorted(missing_companies)}. Cite each company discussed or narrow the claim.")
        for unit in units:
            financial = re.search(r"\b(?:financial\s+data|operating\s+margin\s+data|margin\s+data|operating\s+margins?)\b", unit, re.I)
            absence = re.search(r"\b(?:incomplete|unavailable|missing|not\s+(?:yet\s+)?(?:retrieved|available|verified)|insufficient)\b", unit, re.I)
            if complete and financial and absence:
                issues.append(f"{prefix}: all requested operating-margin pairs have saved calculations. Remove the conflicting missing/incomplete financial-data claim; distinguish sampled risk coverage.")
            causal = re.search(r"\b(?:reflecting|reflects|driven\s+by|because|due\s+to|attribut\w*\s+to|influenced\s+by|result\w*\s+from|caused\s+by)\b", unit, re.I)
            if causal and not any(r["kind"] == "passage" for r in cited):
                issues.append(f"{prefix}: calculation/fact citations do not support business causes. Remove the causal explanation or cite a source that explicitly supports it.")
            risk = re.search(r"\b(?:risk|risks|disclosures?)\b", unit, re.I)
            global_absence = re.search(r"\bno\b.{0,65}\b(?:changes?|evolution|differences?)\b|\b(?:unchanged|identical|uniquely|newly\s+(?:added|introduced)|removed)\b", unit, re.I)
            uncertainty = re.search(r"\b(?:cannot|can't|does\s+not|do\s+not|not\s+enough|insufficient)\b.{0,65}\b(?:establish|determine|infer|prove|conclude)\b", unit, re.I)
            if risk and global_absence and not uncertainty:
                issues.append(f"{prefix}: sampled passages cannot establish filing-wide risk absence, uniqueness, additions/removals or no change. Describe the cited wording and explicitly limit the comparison to the samples.")
        if re.search(r"\b(?:risk|risks|disclosures?)\b", text, re.I) and re.search(r"\b(?:across|throughout|over)\b.{0,45}\b(?:requested\s+periods|requested\s+years|all\s+years)\b", text, re.I):
            subjects = mentioned_companies(text, request) or tickers
            slots = {(r["ticker"],r["year"]) for r in cited if r["kind"] == "passage" and r.get("item") == "1A"}
            missing = [(t,y) for t in sorted(subjects) for y in request["years"] if (t,y) not in slots]
            if missing:
                issues.append(f"{prefix}: the cross-period risk claim has no cited risk passage for {missing}. Narrow its period scope or supply the missing citations.")
    return list(dict.fromkeys(issues))


def receipt_issues(review, paragraph, records):
    """Repair malformed review receipts separately from unsupported draft claims."""
    units = sentences(paragraph["text"])
    rows = review["sentences"]
    issues = []
    if [r["sentence_index"] for r in rows] != list(range(len(units))):
        issues.append("final.sentences: review every supplied sentence exactly once in order.")
    cited = set(paragraph["evidence_ids"])
    for index, row in enumerate(rows):
        label = f"final.sentences[{index}]"
        if row["supported"] and not row["references"]:
            issues.append(label+": a supported verdict requires cited evidence.")
        for ref_index, ref in enumerate(row["references"]):
            ref_label = label+f".references[{ref_index}]"
            key = ref["evidence_id"]
            if key not in cited or key not in records:
                issues.append(ref_label+".evidence_id: use only this paragraph's cited source IDs.")
                continue
            record = records[key]
            quote = normalise(ref["quote"])
            if record["kind"] == "passage":
                source = normalise(record["passage"].get("text", ""))
                if len(quote) < 20 or quote not in source:
                    issues.append(ref_label+".quote: select twenty to three hundred characters verbatim from that exact source. Do not paraphrase or substitute words; mark unsupported if it cannot support the claim.")
            elif quote:
                issues.append(ref_label+".quote: fact/calculation receipts require an empty string; explain the checked relation in reason.")
    return issues


def validate_support(review, paragraph, records):
    """Validate completeness/provenance and retain the model's semantic rejections."""
    issues = [*review["issues"], *receipt_issues(review, paragraph, records)]
    for row in review["sentences"]:
        if not row["supported"]:
            issues.append(f"sentence {row['sentence_index']}: "+row["reason"])
    if not review["approved"] and not issues:
        issues.append("The critic did not approve this paragraph.")
    return list(dict.fromkeys(issues))


def require_current_support(state):
    critique = state.get("critique", {})
    if (critique.get("check_version") != SUPPORT_VERSION or not critique.get("approved")
            or critique.get("draft_sha256") != digest(state.get("draft"))):
        raise WorkflowError("stale_critique", "This draft needs the current sentence-level evidence check. Use review --action edit with a corrected draft before approving publication.")
