"""Read-only live prerequisites for the workflow; no model call or publication."""
import asyncio
import json
from decimal import Decimal

from company_registry import COMPANIES
from research_tools_client import credentials
from workflow_contracts import WorkflowError
from workflow_tools import MCPBackend


def require(condition, code):
    if not condition:
        raise WorkflowError(code, "A workflow prerequisite failed at " + code + ". Check the service data and configuration.")


def validate_coverage(coverage, expected_pairs):
    """Use the KB's actual summary contract and check exact, unique coverage."""
    expected = {tuple(pair) for pair in expected_pairs}
    pairs = coverage.get("pairs")
    require(isinstance(pairs, list) and all(isinstance(p, list) and len(p) == 2 and isinstance(p[0],str) and type(p[1]) is int for p in pairs), "coverage_pairs")
    require(type(coverage.get("company_years")) is int and coverage["company_years"] == len(expected)
            and len(pairs) == len(expected) and {tuple(p) for p in pairs} == expected, "coverage_scope")
    require(coverage.get("coverage_warnings") == [], "coverage_warnings")
    return coverage["company_years"]


async def check(*, backend=None, expected_pairs=None):
    backend = backend if backend is not None else MCPBackend(credentials())
    expected_pairs = expected_pairs if expected_pairs is not None else [(ticker,year) for ticker in COMPANIES for year in (2023,2024,2025)]
    coverage = await backend.call("kb", "get_coverage", {})
    company_years = validate_coverage(coverage, expected_pairs)
    section = await backend.call("edgar", "get_section", {"ticker": "MSFT", "year": 2025, "item": "1A", "offset": 0, "limit": 1})
    require(bool(section.get("passages")) and section["passages"][0].get("source_sha256"), "edgar_section")
    fact = await backend.call("edgar", "get_annual_financial_fact", {"ticker": "MSFT", "year": 2025, "metric": "revenue", "as_of": None})
    require(fact.get("status") == "ok" and type(fact.get("value")) is int and fact.get("ticker") == "MSFT"
            and fact.get("fiscal_year") == 2025 and fact.get("metric") == "revenue" and fact.get("unit") == "USD", "edgar_fact")
    sandbox = await backend.call("sandbox", "execute_python", {"code": 'result = Decimal("25") / Decimal("100") * 100', "inputs": {}})
    require(Decimal(sandbox["result"]) == Decimal("25"), "sandbox_calculation")
    probes = sandbox.get("kernel_probes", {})
    require(all(probes.get(name) is True for name in ("network_denied", "file_open_denied", "process_creation_denied")), "sandbox_probes")
    notes = await backend.call("portal", "list_notes", {}, "read")
    require(type(notes.get("total")) is int and notes["total"] >= 0, "portal_read")
    return {"status": "ok", "company_years": company_years, "edgar_section_http": True,
            "edgar_fact_http": True, "sandbox_kernel_probes": probes, "portal_read": True,
            "model_called": False, "note_created_or_published": False}


def main():
    try:
        print(json.dumps(asyncio.run(check()), indent=2))
    except WorkflowError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "message": exc.message}))
        raise SystemExit(1)
    except Exception as exc:
        print(json.dumps({"status": "error", "code": type(exc).__name__, "message": "A workflow prerequisite failed. Check Compose health and service configuration."}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
