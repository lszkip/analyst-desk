"""Shared measure definitions. Similar accounting labels are not aliases."""

from typing import Literal

MetricName = Literal[
    "revenue", "operating_income", "net_income", "net_income_consolidated", "pretax_income",
]

# Keep the original 135-check baseline stable when new measures are added.
CORE_METRICS = ("revenue", "operating_income", "net_income")

METRICS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "net_income_consolidated": ("ProfitLoss",),
    "pretax_income": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",),
}

METRIC_LABELS = {
    "revenue": "revenue",
    "operating_income": "GAAP operating income",
    "net_income": "net income attributable to the parent",
    "net_income_consolidated": "consolidated net income (including noncontrolling interests)",
    "pretax_income": "GAAP income from continuing operations before income taxes",
}

METRIC_DEFINITIONS = {
    "revenue": "Reported annual sales or service revenue under a supported US-GAAP concept.",
    "operating_income": "Reported GAAP operating profit or loss; not adjusted operating earnings, pretax income or EBIT.",
    "net_income": "After-tax profit or loss belonging to the parent, excluding the share belonging to noncontrolling owners.",
    "net_income_consolidated": "After-tax profit or loss for the consolidated group, including any share belonging to noncontrolling owners.",
    "pretax_income": "GAAP pretax profit or loss from continuing operations, including equity-method investment results and before allocation to noncontrolling owners. This is not operating income.",
}

# These are suggestions for a NEW request, never replacements for a missing fact.
METRIC_ALTERNATIVES = {
    "net_income": ("net_income_consolidated",),
    "net_income_consolidated": ("net_income",),
    "operating_income": ("pretax_income",),
}
