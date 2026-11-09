"""Shared tool arguments and limits for the API and its output guardrail."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from company_registry import COMPANIES, normalise_ticker
from sec_periods import iso_date
from financial_metrics import MetricName

TOOL_NAME = "get_annual_financial_fact"
MAX_LOOKUPS = 6


class FinancialFactArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ticker: str = Field(
        description="Canonical company ticker from the configured coverage list.",
        json_schema_extra={"enum": list(COMPANIES)},
    )
    metric: MetricName
    year: int = Field(ge=1900, le=2100, description="Company fiscal-year label, not necessarily calendar year.")
    # Required but nullable: strict tool schemas require every property.
    as_of: str | None = Field(description="YYYY-MM-DD filing-date cutoff if requested; otherwise null.")

    @field_validator("ticker")
    @classmethod
    def canonical_ticker(cls, value):
        return normalise_ticker(value)

    @field_validator("as_of")
    @classmethod
    def valid_cutoff(cls, value):
        return iso_date(value) if value is not None else None
