"""Typed, JSON-only contracts at the research workflow's trust boundaries."""
import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from company_registry import COMPANIES, normalise_ticker


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class WorkflowError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ResearchRequest(Strict):
    question: str = Field(min_length=10, max_length=2000)
    tickers: list[str] = Field(min_length=1, max_length=4)
    years: list[int] = Field(min_length=1, max_length=3)
    include_risks: bool = True

    @model_validator(mode="after")
    def implemented_analysis(self):
        if not re.search(r"\boperating\s+margins?\b",self.question,re.I):
            raise ValueError("This first graph supports operating-margin comparisons with optional risk discussion. Other research tasks require a separate workflow extension.")
        return self

    @field_validator("tickers")
    @classmethod
    def companies(cls, values):
        return sorted({normalise_ticker(x) for x in values})

    @field_validator("years")
    @classmethod
    def years_ok(cls, values):
        if any(y not in {2023, 2024, 2025} for y in values):
            raise ValueError("This prepared corpus covers FY2023–2025.")
        return sorted(set(values))


class Plan(Strict):
    objective: str = Field(min_length=5, max_length=1000)
    outline: list[str] = Field(min_length=1, max_length=8)
    research_query: str = Field(min_length=3, max_length=600)
    limitations: list[str] = Field(max_length=8)


class Handoff(Strict):
    summary: str = Field(max_length=5000)
    evidence_ids: list[str] = Field(max_length=80)
    issues: list[str] = Field(max_length=12)


class Paragraph(Strict):
    text: str = Field(min_length=1, max_length=2500)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class Draft(Strict):
    title: str = Field(min_length=1, max_length=160)
    paragraphs: list[Paragraph] = Field(min_length=1, max_length=10)


class Critique(Strict):
    supported: list[bool] = Field(min_length=1, max_length=10)
    issues: list[str] = Field(max_length=12)
    approved: bool


class AgentAction(Strict):
    tool: str
    arguments: dict
    final: dict | None

    @model_validator(mode="after")
    def exclusive(self):
        if (self.tool == "finish") != (self.final is not None):
            raise ValueError("finish requires final; tool calls require final=null.")
        if self.tool == "finish" and self.arguments:
            raise ValueError("finish has no arguments.")
        return self


class HumanDecision(Strict):
    action: Literal["approve", "edit", "reject"]
    expected_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    replacement: dict | None = None

    @model_validator(mode="after")
    def edit_only(self):
        if (self.action == "edit") != (self.replacement is not None):
            raise ValueError("Only edit accepts a replacement.")
        return self


# These patterns are a first layer, not a claim that regex solves injection.
# Credentials, watchlists, arbitrary URLs and filesystem paths are never tools.
# Registry names cover literal advice for the full prepared company scope.
# A security noun distinguishes company products from shares in ordinary prose;
# bare imperative advice is also blocked. Paraphrases still need review.
_advice_names = set()
for _ticker, _company in COMPANIES.items():
    _advice_names.update([_ticker, _company["name"], *_company.get("aliases", [])])
    _advice_names.add(re.sub(r"[,\s]+(?:INC\.?|CORP\.?|CORPORATION|CO\.?|LTD\.?)$",
                             "", _company["name"], flags=re.I).strip())
_advice_company = "(?:" + "|".join(
    r"\s+".join(re.escape(part) for part in name.split())
    for name in sorted(_advice_names, key=lambda value: (-len(value), value)) if name
) + r")(?!\w)"
ADVICE = re.compile(
    r"\b(?:buy|sell|short|hold)\s+(?:(?:the|this|these|more|their)\s+)?(?:stock|shares|MSFT|GOOGL|AAPL)\b"
    + r"|\b(?:buy|sell|short|hold)\s+(?:(?:the|this|these|more|their)\s+)?\$?"
    + _advice_company + r"(?:['’]s)?\s+(?:stock|shares|equity)\b"
    + r"|(?:^|[.!?;]\s+)\s*(?:buy|sell|short|hold)\s+\$?"
    + _advice_company + r"(?=\s*(?:[.!?;]|$))"
    + r"|\b(?:guaranteed?\s+(?:returns?|profits?)|price\s+target|strong\s+buy|investment\s+recommendation)\b", re.I)
INJECTION = re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|reveal\s+(?:the\s+)?(?:secret|key|watchlist)|\b(?:api[_ -]?key|private\s+watchlist)\b", re.I)


def guard_text(text):
    if ADVICE.search(text) or INJECTION.search(text):
        raise WorkflowError("policy_block", "The request or draft contains prohibited advice or a sensitive-data instruction.")


ROLE_TOOLS = {
    "supervisor": (),
    "researcher": ("search_filings", "get_passage", "get_table_rows", "get_section"),
    "quant": ("collect_financials",),
    "risk_diff": ("search_filings", "get_passage", "get_section"),
    "writer": ("get_passage",),
    "critic": ("get_passage",),
    # This role is deterministic. Publication never appears in an LLM catalogue.
    "publisher": ("prepare_note", "get_proposal", "publish_note"),
}
