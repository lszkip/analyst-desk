"""Offline tests of frozen case splits, source selection and adversarial scoring."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("final_case_authoring", Path(__file__).with_name("build_cases.py"))
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def source_fixture(folder, *, conflict=False):
    """Current FY2023, comparative FY2023 in FY2024; no company-name calendar rule."""
    base = {"form": "10-K", "fp": "FY", "start": "2022-07-01", "end": "2023-06-30"}
    one = {**base, "fy": 2023, "filed": "2023-07-20", "accn": "0000000001-23-000001"}
    comparative = {**base, "fy": 2024, "filed": "2024-07-20", "accn": "0000000001-24-000001"}
    current = {**comparative, "start": "2023-07-01", "end": "2024-06-30"}
    revenue = [{**one, "val": 100}, {**comparative, "val": 120}, {**current, "val": 140}]
    income = [{**one, "val": 20}, {**comparative, "val": 30}, {**current, "val": 49}]
    if conflict:
        income.append({**comparative, "val": 31})
    facts = {"cik": 1, "entityName": "Fixture Corp", "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": revenue}},
        "OperatingIncomeLoss": {"units": {"USD": income}}}}}
    filings = {"cik": "0000000001", "complete": True, "annual_filings": [
        {"accessionNumber": one["accn"], "filingDate": one["filed"], "reportDate": "2023-06-30", "form": "10-K", "primaryDocument": "fixture2023.htm"},
        {"accessionNumber": current["accn"], "filingDate": current["filed"], "reportDate": "2024-06-30", "form": "10-K", "primaryDocument": "fixture2024.htm"}]}
    (folder / "TEST_companyfacts.json").write_text(json.dumps(facts))
    (folder / "TEST_filings.json").write_text(json.dumps(filings))
    return facts, filings


def output_fixture(target, *, risk=False):
    """Synthetic output generated directly from a test source; never an LLM output."""
    records = {}
    for metric in ("revenue", "operating_income"):
        expected = target[metric]
        if expected["status"] != "ok":
            continue
        fact = {"status": "ok", "ticker": target["ticker"], "fiscal_year": target["fiscal_year"], "metric": metric,
            "value": int(expected["value"]), "unit": "USD", "concept": expected["concept"],
            "period_start": target["period_start"], "period_end": target["period_end"],
            "filed": expected["filed"], "filing_identifiers": expected["filing_identifiers"],
            "snapshot_sha256": expected["snapshot_sha256"], "filings_snapshot_sha256": expected["filings_snapshot_sha256"]}
        key = expected["expected_fact_id_prefix"] + build.digest(fact)[:12]
        records[key] = {"kind": "fact", "ticker": target["ticker"], "year": target["fiscal_year"], "metric": metric,
            "value": expected["value"], "unit": "USD", "source_sha256": expected["snapshot_sha256"],
            "source_url": expected["source_url"], "fact": fact}
    keys = {r["metric"]: k for k, r in records.items()}
    ticker, year = target["ticker"], target["fiscal_year"]
    if target["margin"]["status"] == "ok":
        rid, iid = keys["revenue"], keys["operating_income"]
        value = target["margin"]["value"]
        mid = "margin_" + build.digest([rid, iid, value])[:32]
        records[mid] = {"kind": "calculation", "ticker": ticker, "year": year, "value": value, "unit": "%", "inputs": [iid, rid],
            "formula": "100 * operating_income / revenue", "source_url": records[iid]["source_url"], "source_sha256": records[iid]["source_sha256"]}
        body = (f"| {ticker} | {year} | {target['period_start']} to {target['period_end']} | "
                f"{int(target['revenue']['value']):,} [{rid}] | {int(target['operating_income']['value']):,} [{iid}] | {value}% [{mid}] |")
    else:
        body = f"Unavailable calculations (no substitute measure used): {ticker} FY{year}."
    citations = [{"evidence_id": key, "source_sha256": r["source_sha256"], "source_url": r["source_url"]} for key, r in records.items()]
    result = {"status": "completed", "note": {"body_markdown": body, "citations": citations}}
    gold = {"case_id": "synthetic", "numeric_targets": [target], "requires_human_adjudication": risk}
    return result, records, gold


class CasesTest(unittest.TestCase):
    def test_sixty_distinct_company_disjoint_cases(self):
        cases = build.author_cases()
        self.assertEqual(len(cases), 60)
        self.assertEqual(len({r["request"]["question"] for r in cases}), 60)
        self.assertEqual(sum(r["split"] == "dev" for r in cases), 20)
        groups = {}
        companies = {}
        for case in cases:
            groups.setdefault(case["scenario_id"], set()).add(case["split"])
            for ticker in case["request"]["tickers"]:
                companies.setdefault(ticker, set()).add(case["split"])
            self.assertEqual(set(case["request"]), {"question", "tickers", "years", "include_risks"})
            self.assertFalse(any(key in case["request"] for key in ("gold", "expected_outcome", "numeric_targets")))
        self.assertEqual(len(companies), 15)
        self.assertTrue(all(len(v) == 1 for v in [*groups.values(), *companies.values()]))
        self.assertEqual(sum(r["request"]["include_risks"] for r in cases), 30)

    def test_related_variant_cannot_cross_split(self):
        cases = build.author_cases()
        cases[0]["scenario_id"] = cases[20]["scenario_id"]
        with self.assertRaisesRegex(ValueError, "Related variants"):
            build.validate_cases(cases)

    def test_decimal_rounding_exact_not_binary_float(self):
        self.assertEqual(build.decimal_margin("2469", "20000"), "12.34")
        self.assertEqual(build.decimal_margin("2471", "20000"), "12.36")
        self.assertEqual(build.decimal_margin("-1", "3"), "-33.33")
        with self.assertRaises(ValueError):
            build.decimal_margin("1", "0")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source_fixture(self.root)
        self.target = build.resolve_company(self.root, "TEST", (2023,))[2023]

    def tearDown(self):
        self.temp.cleanup()

    def test_period_comes_from_current_filing_not_comparative_fy(self):
        self.assertEqual(self.target["period_start"], "2022-07-01")
        self.assertEqual(self.target["period_end"], "2023-06-30")
        self.assertEqual(self.target["revenue"]["value"], "120")
        self.assertEqual(self.target["operating_income"]["value"], "30")
        self.assertEqual(self.target["margin"]["value"], "25.00")
        self.assertEqual(self.target["revenue"]["filed"], "2024-07-20")

    def test_conflicting_latest_source_facts_fail_closed(self):
        source_fixture(self.root, conflict=True)
        with self.assertRaisesRegex(ValueError, "conflict"):
            build.resolve_company(self.root, "TEST", (2023,))

    def test_source_metadata_hash_mismatch_is_rejected(self):
        (self.root / "TEST_companyfacts_metadata.json").write_text(json.dumps({"sha256": "0"*64}))
        with self.assertRaisesRegex(ValueError, "metadata"):
            build.resolve_company(self.root, "TEST", (2023,))

    def test_exact_output_passes_without_critic_self_score(self):
        result, records, gold = output_fixture(self.target)
        result["verification"] = {"verified_share": 0.0}
        result["critique"] = {"approved": False}
        score = build.evaluate_case("synthetic", result, records, gold)
        self.assertTrue(score["numeric_success"], score)
        self.assertTrue(score["task_success"])

    def test_right_records_wrong_delivered_value_fails(self):
        result, records, gold = output_fixture(self.target)
        result["note"]["body_markdown"] = result["note"]["body_markdown"].replace("25.00%", "95.00%")
        result["verification"] = {"verified_share": 1.0}
        self.assertFalse(build.evaluate_case("synthetic", result, records, gold)["numeric_success"])

    def test_matching_value_wrong_source_hash_fails(self):
        result, records, gold = output_fixture(self.target)
        key = next(k for k, r in records.items() if r["kind"] == "fact")
        records[key]["fact"]["snapshot_sha256"] = "a" * 64
        self.assertFalse(build.evaluate_case("synthetic", result, records, gold)["numeric_success"])

    def test_matching_value_wrong_fiscal_period_fails(self):
        result, records, gold = output_fixture(self.target)
        key = next(k for k, r in records.items() if r["kind"] == "fact")
        records[key]["fact"]["period_start"] = "2023-01-01"
        self.assertFalse(build.evaluate_case("synthetic", result, records, gold)["numeric_success"])

    def test_wrong_fact_identifier_fails(self):
        result, records, gold = output_fixture(self.target)
        key = next(k for k, r in records.items() if r["kind"] == "fact")
        records[key + "bad"] = records.pop(key)
        self.assertFalse(build.evaluate_case("synthetic", result, records, gold)["numeric_success"])

    def test_missing_final_citation_fails(self):
        result, records, gold = output_fixture(self.target)
        result["note"]["citations"].pop()
        self.assertFalse(build.evaluate_case("synthetic", result, records, gold)["numeric_success"])

    def test_unfinished_run_never_passes_numeric_check(self):
        result, records, gold = output_fixture(self.target)
        result["status"] = "timeout"
        self.assertFalse(build.evaluate_case("synthetic", result, records, gold)["numeric_success"])

    def test_risk_case_requires_real_human_adjudication(self):
        result, records, gold = output_fixture(self.target, risk=True)
        result["critique"] = {"approved": True}
        score = build.evaluate_case("synthetic", result, records, gold)
        self.assertTrue(score["numeric_success"])
        self.assertIsNone(score["task_success"])
        self.assertIsNone(score["risk_semantic_success"])

    def test_missing_metric_cannot_be_substituted(self):
        target = deepcopy(self.target)
        target["operating_income"] = {"status": "not_found", "forbid_substitution": True}
        target["margin"] = {"status": "unavailable"}
        result, records, gold = output_fixture(target)
        self.assertTrue(build.evaluate_case("synthetic", result, records, gold)["numeric_success"])
        records["pretax_forgery"] = {"kind": "calculation", "ticker": "TEST", "year": 2023, "value": "25.00", "unit": "%"}
        self.assertFalse(build.evaluate_case("synthetic", result, records, gold)["numeric_success"])


if __name__ == "__main__":
    unittest.main()
