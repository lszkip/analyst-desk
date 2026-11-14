"""Offline final evaluation case authoring, frozen SEC truth and outcome checker.

No network/model calls. No production financial selector import: source facts are
resolved independently from original cached SEC JSON. Keep this module and its
private artifacts out of the agent runtime/tool roots. Only case['request'] may
cross the evaluator/agent boundary. AI-assisted authorship is NOT human review.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import html
import json
from pathlib import Path
import re
import shutil

SCHEMA_VERSION = "analyst-desk-final-cases-v1"
DEV_COMPANIES = {"MSFT", "AAPL", "NVDA", "GOOGL", "META"}
COMPANY_CIK = {"MSFT": 789019, "AAPL": 320193, "NVDA": 1045810, "AVGO": 1730168, "ORCL": 1341439,
               "ADBE": 796343, "CRM": 1108524, "CSCO": 858877, "INTU": 896878, "IBM": 51143,
               "GOOGL": 1652044, "META": 1326801, "NFLX": 1065280, "DIS": 1744489, "CMCSA": 1166691}
REVENUE_TAGS = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")
PERIOD_TAGS = REVENUE_TAGS + ("OperatingIncomeLoss", "NetIncomeLoss", "ProfitLoss", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest")
ANNUAL_FORMS = {"10-K", "10-K/A"}

# Authored individually before any agent run. Four related questions share each
# main-company scenario, and all peers remain in the same company-disjoint split.
# (primary, peer, single-company numeric, peer numeric, focused risk, broad risk)
SCENARIOS = [
("MSFT", "GOOGL",
 "Build a fiscal-year operating margin bridge for Microsoft from FY2023 through FY2025. Show revenue and GAAP operating income beside each margin, with the exact reporting dates and filing evidence. Keep this note to the numerical comparison.",
 "Compare Microsoft and Alphabet operating margins in FY2024 and FY2025. Present each company's own fiscal periods side by side so an analyst can see the June-versus-December year-end limitation. Include the reported inputs; do not annualise or calendarise them.",
 "Compare Microsoft's operating margins in FY2023 and FY2024 and examine what the cited risk-factor passages say about cybersecurity and cloud service reliability. Distinguish a difference in retrieved wording from a demonstrated new risk.",
 "Review Microsoft's FY2024 and FY2025 operating margins together with its risk disclosures about AI development and competition. Explain the limits of attributing a margin movement to a disclosed risk, and cite both years' evidence."),
("AAPL", "NVDA",
 "Prepare an Apple FY2023-FY2025 operating margin table using annual GAAP operating income and revenue. Retain the actual reporting dates, including any different year length, and do not substitute quarterly figures.",
 "Put Apple's and NVIDIA's FY2025 operating margins next to their reported revenues and operating incomes. Make the fiscal-period mismatch visible in the comparison instead of treating the labels as a common calendar year.",
 "Assess Apple's FY2023-to-FY2024 operating margin comparison alongside its supply-chain and geographic concentration risk passages. State which conclusions the two cited disclosures support and which would require complete-section review.",
 "Compare Apple operating margins for FY2024 and FY2025 and review changes in its competition, platform regulation and distribution risks. Avoid inferring that an omitted search result proves a risk disappeared."),
("NVDA", "MSFT",
 "Show NVIDIA operating margins for FY2023, FY2024 and FY2025 with annual revenue and operating income. Use NVIDIA's fiscal labels even when the fiscal year ends early in a calendar year; provide the matched periods.",
 "Compare NVIDIA and Microsoft operating margins across FY2023 and FY2025. Keep the two observations separate for each company, with source-linked GAAP inputs, so the analyst can assess the endpoints without confusing fiscal dates.",
 "Compare NVIDIA's FY2023 and FY2024 operating margins and the risk disclosures about export restrictions and access to international markets. Identify only changes supported by cited source text, not assumptions about subsequent events.",
 "Prepare a FY2024-to-FY2025 operating margin comparison for NVIDIA and discuss its manufacturing, supplier and customer-concentration risk language. Explain whether the retrieved evidence is sufficient to call any risk added or removed."),
("GOOGL", "META",
 "Create Alphabet's FY2023-FY2025 operating margin comparison from consolidated GAAP revenue and operating income. Keep dollar units and fiscal reporting periods explicit, with a fact reference for every table value.",
 "Compare Alphabet and Meta operating margins in FY2023 and FY2024 using the same metric definition. Include both companies' reported inputs and fiscal dates so the peer table can be checked directly against the filings.",
 "Review Alphabet's FY2023 and FY2024 operating margins and compare risk passages about search competition and AI-driven product changes. Separate reported risks from an unsupported causal explanation of profitability.",
 "Compare Alphabet's FY2024 and FY2025 operating margins and its privacy, antitrust and regulatory risk discussion. Use both years' cited disclosures and qualify any claim about a change across the complete filings."),
("META", "AAPL",
 "Show Meta's operating margins for FY2023 through FY2025 with the underlying annual revenue and operating income. Preserve consolidated GAAP definitions and make each numerator and denominator independently traceable.",
 "Compare Meta and Apple operating margins in FY2024 and FY2025. Display the actual year ends and reported USD inputs, and retain that the peer periods differ rather than presenting a calendar-aligned ranking.",
 "Compare Meta operating margins in FY2023 and FY2024 alongside its advertising, privacy and platform-dependence risk language. Discuss the support in the cited passages without assuming better margins mean lower risk.",
 "Examine Meta's FY2024-to-FY2025 operating margins and disclosures about AI spending, competition and product execution. Identify the scope of the source comparison and avoid claiming a risk was newly added from partial retrieval."),
("AVGO", "CSCO",
 "Prepare Broadcom's FY2023-FY2025 operating margin table, showing reported GAAP operating income and revenue. Keep acquisition-era accounting in the reported figures and do not replace them with adjusted earnings.",
 "Compare Broadcom and Cisco operating margins in FY2024 and FY2025 with the underlying GAAP values and exact fiscal periods. The analyst wants the reported peer comparison, including its fiscal-year alignment limitations.",
 "Compare Broadcom's FY2023 and FY2024 operating margins and its acquisition integration and customer-concentration risk disclosures. Ground the discussion in both filings without equating a margin decline with proof of a risk event.",
 "Review Broadcom FY2024 and FY2025 operating margins together with risk passages on intellectual property, suppliers and international trade. Clearly separate supported textual differences from unverified full-filing additions or removals."),
("ORCL", "CRM",
 "Show Oracle operating margins across FY2023, FY2024 and FY2025 with annual GAAP revenue and operating income. Preserve Oracle's May fiscal year end and provide the exact numerator and denominator periods.",
 "Compare Oracle and Salesforce operating margins for FY2024 and FY2025. Use each company's fiscal-year labels and filing-linked GAAP inputs, and make the May-versus-January reporting-period difference visible.",
 "Compare Oracle operating margins in FY2023 and FY2024 while reviewing risk passages on cloud service availability, security and customer commitments. Limit change claims to what the paired filing evidence supports.",
 "Assess Oracle's FY2024 and FY2025 operating margins alongside its competitive and technology-transition risk disclosures. Explain why a forward-looking disclosure cannot by itself establish the cause of a reported margin movement."),
("ADBE", "INTU",
 "Make an Adobe FY2023-FY2025 operating margin comparison from annual reported GAAP figures. Keep fiscal start and end dates and the filing references beside the inputs so a reviewer can check the unusual year-end calendar.",
 "Compare Adobe and Intuit operating margins in FY2024 and FY2025. Show the annual revenue and operating income supporting each percentage, retaining their different fiscal periods and avoiding adjusted measures.",
 "Compare Adobe's FY2023 and FY2024 operating margins and its risk discussion of generative AI, intellectual property and competing products. Cite both years and distinguish a wording change from evidence that exposure increased.",
 "Review Adobe operating margins in FY2024 and FY2025 with its subscription, customer retention and cybersecurity risk disclosures. Make clear when the retrieved passages cannot establish whether a topic is absent from a whole filing."),
("CRM", "ADBE",
 "Present Salesforce operating margins for FY2023 through FY2025 using consolidated annual GAAP revenue and operating income. Keep the fiscal-year labels and January reporting dates attached to the facts.",
 "Compare Salesforce and Adobe operating margins for FY2023 and FY2025 using reported annual inputs. Keep this to a source-linked endpoint table, including each company's actual fiscal periods.",
 "Compare Salesforce FY2023 and FY2024 operating margins alongside its restructuring, workforce and execution risk passages. Do not assume a change in expense structure establishes a risk-factor change without paired evidence.",
 "Review Salesforce's FY2024 and FY2025 operating margins and risk disclosures about AI adoption, data protection and service reliability. Qualify any additions or removals that would need complete-section verification."),
("CSCO", "AVGO",
 "Prepare Cisco's FY2023-FY2025 operating margin comparison with annual GAAP revenue and operating income. Match periods exactly and retain the reported fiscal dates rather than imposing a July month-end convention.",
 "Compare Cisco and Broadcom operating margins in FY2023 and FY2025. Present a reported-GAAP endpoint table with fact references and actual reporting periods, without treating different year ends as identical windows.",
 "Compare Cisco operating margins in FY2023 and FY2024 with its supply-chain, demand and inventory risk language. Cite the paired disclosures and avoid treating a risk warning as proof that it caused the margin change.",
 "Review Cisco's FY2024 and FY2025 operating margins together with acquisition integration, security and competition risk passages. State the evidence boundaries before describing any material change in risk wording."),
("INTU", "ORCL",
 "Show Intuit operating margins for FY2023, FY2024 and FY2025 with annual GAAP operating income and revenue. Preserve the July fiscal periods and show exact reported USD values rather than rounded billions as calculation inputs.",
 "Compare Intuit and Oracle operating margins in FY2024 and FY2025. Give their annual inputs and actual fiscal date ranges, and keep differences in reporting windows visible in the peer table.",
 "Compare Intuit's FY2023 and FY2024 operating margins and the risk disclosures concerning tax-season concentration, regulation and government competition. Describe only changes supported by the source pairs.",
 "Examine Intuit FY2024 and FY2025 operating margins and its AI, sensitive-data and cybersecurity risk language. Explain how partial retrieval limits conclusions about whether a disclosure was introduced or withdrawn."),
("IBM", "ORCL",
 "Assess whether IBM's FY2023-FY2025 GAAP operating margins can be calculated from the supported cached XBRL concepts. Show available annual revenue evidence, and if GAAP operating income is absent, report the missing measure instead of substituting pretax or adjusted earnings.",
 "Compare IBM and Oracle operating margins in FY2024 and FY2025 to the extent supported by annual XBRL facts. Retain Oracle's calculable values and explicitly identify any IBM coverage gaps; do not turn IBM pretax income into operating income.",
 "Review IBM's FY2023 and FY2024 operating margin availability alongside its cybersecurity and AI-related risk disclosures. Keep the missing GAAP metric separate from the qualitative risk comparison and cite the actual source passages.",
 "Compare IBM's FY2024 and FY2025 operating margin coverage and risk language about hybrid cloud competition, acquisitions and execution. A missing numerical fact must remain a limitation, not be filled with a differently defined earnings measure."),
("NFLX", "DIS",
 "Prepare Netflix operating margins for FY2023 through FY2025 from annual revenue and GAAP operating income. Preserve the reported USD precision and provide period and filing references for every numerical input.",
 "Compare Netflix and Disney operating margins in FY2024 and FY2025 using consolidated reported GAAP inputs. Show their different fiscal periods and do not substitute segment profitability or adjusted operating income.",
 "Compare Netflix FY2023 and FY2024 operating margins with its content spending, licensing and competition risk disclosures. Do not infer that an improving margin means those risks diminished without supporting disclosure evidence.",
 "Review Netflix FY2024 and FY2025 operating margins and risk passages about advertising, member retention and international regulation. Use source pairs and qualify any whole-filing addition or removal claim."),
("DIS", "CMCSA",
 "Create Disney's FY2023-FY2025 operating margin table using the supported consolidated annual XBRL revenue and GAAP operating income. Keep the exact fiscal dates and avoid mixing total segment operating income with the requested measure.",
 "Compare Disney and Comcast operating margins for FY2023 and FY2024, with reported GAAP inputs and fiscal date ranges. Keep the September-versus-December period limitation visible and avoid a calendar-year reinterpretation.",
 "Compare Disney FY2023 and FY2024 operating margins and its risk disclosures on streaming competition, content and distribution. Explain what the cited source pairs establish without attributing the numerical change to a risk merely because it is mentioned.",
 "Assess Disney FY2024 and FY2025 operating margins alongside risk passages on consumer demand, parks operations and regulatory exposure. Identify where complete-section review is still needed to establish a new or removed risk."),
("CMCSA", "NFLX",
 "Show Comcast operating margins across FY2023, FY2024 and FY2025 using annual consolidated GAAP revenue and operating income. Provide exact USD inputs, matched reporting dates and source references.",
 "Compare Comcast and Netflix operating margins in FY2023 and FY2025. Use a source-linked annual peer table with both companies' inputs, and keep business-model differences separate from numerical verification.",
 "Compare Comcast FY2023 and FY2024 operating margins and its broadband competition, subscriber and distribution risk disclosures. Support any textual change with both years' passages rather than treating a retrieved omission as a removed risk.",
 "Review Comcast FY2024 and FY2025 operating margins alongside its media, content rights and cybersecurity risk language. Describe the limits of the risk comparison and avoid unsupported explanations of the margin movement."),
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decimal_margin(income, revenue):
    if Decimal(revenue) <= 0:
        raise ValueError("A positive revenue denominator is required")
    with localcontext() as ctx:
        ctx.prec = 28
        return str((Decimal(income) / Decimal(revenue) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def author_cases():
    cases = []
    for primary, peer, single, paired, risk1, risk2 in SCENARIOS:
        split = "dev" if primary in DEV_COMPANIES else "heldout"
        # Explicit endpoint choices occur in the individually authored questions.
        if primary in {"NVDA", "CRM", "CSCO", "CMCSA"}:
            peer_years = [2023, 2025]
        elif primary in {"GOOGL", "DIS"}:
            peer_years = [2023, 2024]
        elif primary == "AAPL":
            peer_years = [2025]
        else:
            peer_years = [2024, 2025]
        rows = [(single, [primary], [2023, 2024, 2025], False, "annual_series"),
                (paired, sorted([primary, peer]), peer_years, False, "peer_comparison"),
                (risk1, [primary], [2023, 2024], True, "risk_comparison"),
                (risk2, [primary], [2024, 2025], True, "risk_comparison")]
        for variant, (question, tickers, years, risk, kind) in enumerate(rows, 1):
            cases.append({"schema_version": SCHEMA_VERSION,
                "case_id": f"F{len(cases)+1:03d}", "scenario_id": f"company_{primary.lower()}",
                "split": split, "task_type": kind, "primary_company": primary,
                "request": {"question": question, "tickers": tickers, "years": years, "include_risks": risk},
                "authorship": "AI-assisted independently assigned case-author agent; not independently human-authored",
                "risk_label_status": "pending_human_label" if risk else "not_applicable"})
    validate_cases(cases)
    return cases


def validate_cases(cases):
    if len(cases) < 60 or len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("Require at least 60 unique case identifiers")
    if Counter(c["split"] for c in cases) != {"dev": 20, "heldout": 40}:
        raise ValueError("Expected 20 development and 40 held-out cases")
    if len({c["request"]["question"] for c in cases}) != len(cases):
        raise ValueError("Requests must be distinct")
    split_by_scenario = {}
    split_by_company = {}
    for case in cases:
        previous = split_by_scenario.setdefault(case["scenario_id"], case["split"])
        if previous != case["split"]:
            raise ValueError("Related variants cross the split")
        req = case["request"]
        if not re.search(r"\boperating\s+margins?\b", req["question"], re.I):
            raise ValueError("Unsupported task outside operating margin workflow")
        for ticker in req["tickers"]:
            previous = split_by_company.setdefault(ticker, case["split"])
            if previous != case["split"]:
                raise ValueError("Company-disjoint split was violated")
        if not set(req["years"]) <= {2023, 2024, 2025}:
            raise ValueError("Out-of-corpus fiscal year")


def _records(facts, tags):
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for concept in tags:
        for row in gaap.get(concept, {}).get("units", {}).get("USD", []):
            yield concept, row


def _checked_metadata(row, filings):
    metadata = filings.get(row.get("accn"))
    if not metadata or row.get("filed") != metadata.get("filingDate") or row.get("form") != metadata.get("form"):
        raise ValueError("Source fact and filing history disagree")
    return metadata


def resolve_company(data_dir, ticker, years=(2023, 2024, 2025)):
    """Resolve source identities and Decimal gold, independently of agent code."""
    root = Path(data_dir)
    facts_path, filings_path = root / f"{ticker}_companyfacts.json", root / f"{ticker}_filings.json"
    facts, history = json.loads(facts_path.read_text()), json.loads(filings_path.read_text())
    if history.get("complete") is not True or int(facts["cik"]) != int(history["cik"]):
        raise ValueError("Incomplete or mismatched raw SEC source cache")
    if ticker in COMPANY_CIK and int(facts["cik"]) != COMPANY_CIK[ticker]:
        raise ValueError("Source belongs to a different company than the requested ticker")
    filings = {r["accessionNumber"]: r for r in history["annual_filings"]}
    if len(filings) != len(history["annual_filings"]):
        raise ValueError("Duplicate filing history requires review")
    for filing in filings.values():
        evidence = filing.get("fiscal_year_evidence")
        if evidence:
            if not re.fullmatch(r"[a-f0-9]{64}", evidence.get("source_sha256", "")):
                raise ValueError("Invalid original-filing fingerprint")
            p = root / "sec_fiscal_years" / (evidence["source_sha256"] + ".html")
            if sha(p) != evidence["source_sha256"] or evidence["report_date"] != filing["reportDate"]:
                raise ValueError("Unverifiable original-filing fiscal-year correction")
            text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", p.read_text())).split())
            if " ".join(evidence["quote"].split()) not in text:
                raise ValueError("Fiscal-year definition quote absent from original filing")
    snapshot = sha(facts_path)
    metadata_path = root / f"{ticker}_companyfacts_metadata.json"
    if metadata_path.exists() and json.loads(metadata_path.read_text()).get("sha256") != snapshot:
        raise ValueError("SEC download metadata does not match original source bytes")
    result = {}
    for year in years:
        periods, period_sources = set(), []
        for concept, row in _records(facts, PERIOD_TAGS):
            if row.get("form") not in ANNUAL_FORMS or row.get("fp") != "FY":
                continue
            metadata = filings.get(row.get("accn"), {})
            corrected_year = metadata.get("fiscal_year_evidence", {}).get("fiscal_year", row.get("fy"))
            if corrected_year != year or row.get("end") != metadata.get("reportDate"):
                continue
            _checked_metadata(row, filings)
            if not row.get("start"):
                continue
            start, end = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
            if not 350 <= (end-start).days+1 <= 380:
                continue
            periods.add((row["start"], row["end"]))
            period_sources.append({"concept": concept, "accession": row["accn"], "fy": row.get("fy"),
                                   "fiscal_year_evidence": metadata.get("fiscal_year_evidence")})
        if len(periods) != 1:
            raise ValueError(f"{ticker} FY{year}: expected one independently resolvable annual period, got {sorted(periods)}")
        start, end = next(iter(periods))
        target = {"ticker": ticker, "fiscal_year": year, "period_start": start, "period_end": end,
                  "period_days": (date.fromisoformat(end)-date.fromisoformat(start)).days+1,
                  "period_evidence": period_sources}
        for metric, tags in (("revenue", REVENUE_TAGS), ("operating_income", ("OperatingIncomeLoss",))):
            candidates = []
            for concept, row in _records(facts, tags):
                if row.get("form") not in ANNUAL_FORMS or (row.get("start"), row.get("end")) != (start, end):
                    continue
                metadata = _checked_metadata(row, filings)
                if type(row.get("val")) is not int or row["filed"] < end or metadata.get("reportDate", end) < end:
                    raise ValueError("Invalid annual source fact")
                candidates.append((concept, row))
            if not candidates:
                target[metric] = {"status": "not_found", "code": "metric_not_found", "forbid_substitution": True,
                                  "snapshot_sha256": snapshot, "filings_snapshot_sha256": sha(filings_path)}
                continue
            latest = max(r["filed"] for _, r in candidates)
            chosen = [(c, r) for c, r in candidates if r["filed"] == latest]
            if len({r["val"] for _, r in chosen}) != 1:
                raise ValueError("Latest source values conflict; freeze cannot invent a gold answer")
            concept = chosen[0][0]
            selected = [r for c, r in chosen if c == concept]
            accessions = sorted({r["accn"] for r in selected})
            value = str(selected[0]["val"])
            target[metric] = {"status": "ok", "value": value, "unit": "USD", "concept": concept,
                "filed": latest, "filing_identifiers": accessions,
                "expected_fact_id_prefix": f"f_{concept}_{start}_{end}_{accessions[0]}_",
                "source_fact_id": f"sec:{int(facts['cik']):010d}:{concept}:{start}:{end}:USD:{accessions[0]}:{value}",
                "snapshot_sha256": snapshot, "filings_snapshot_sha256": sha(filings_path),
                "source_url": f"https://www.sec.gov/Archives/edgar/data/{int(facts['cik'])}/{accessions[0].replace('-', '')}/{accessions[0]}-index.html"}
        if all(target[m]["status"] == "ok" for m in ("revenue", "operating_income")):
            target["margin"] = {"status": "ok", "value": decimal_margin(target["operating_income"]["value"], target["revenue"]["value"]),
                                "unit": "%", "rounding": "ROUND_HALF_EVEN", "decimal_places": 2}
        else:
            target["margin"] = {"status": "unavailable", "reason": "Required GAAP input absent; no substitute earnings measure permitted"}
        result[year] = target
    return result


def build_gold(cases, data_dir):
    companies = sorted({t for c in cases for t in c["request"]["tickers"]})
    resolved = {t: resolve_company(data_dir, t) for t in companies}
    gold = []
    for case in cases:
        req = case["request"]
        targets = [resolved[t][y] for t in req["tickers"] for y in req["years"]]
        gold.append({"schema_version": SCHEMA_VERSION, "case_id": case["case_id"],
            "scenario_id": case["scenario_id"], "split": case["split"], "request_sha256": digest(req),
            "expected_outcome_written_before_run": True,
            "numeric_targets": targets,
            "expected_behavior": ["Use only annual GAAP revenue and operating income for the same exact fiscal period",
                "Calculate operating margin in code with Decimal half-even rounding to two decimals",
                "Cite the raw source identity for both inputs and the calculated result",
                "Display requested company/fiscal-year pairs, or explicitly disclose missing supported inputs",
                "Do not substitute pretax income, adjusted earnings, segment profit, another period or another company"],
            "qualitative_criteria": (["Use evidence from both requested fiscal years for each risk comparison",
                "Cite Item 1A source passages with verifiable source fingerprints",
                "Distinguish textual evidence from claims of causal impact on margin",
                "Do not infer whole-filing risk additions/removals from partial retrieved passages",
                "A human must label the topic-specific changes against complete Item 1A sections"] if req["include_risks"] else []),
            "requires_human_adjudication": req["include_risks"],
            "human_risk_labels": None,
            "provenance": {"author_role": "separate AI-assisted case-author", "agent_outputs_seen": False,
                "agent_runtime_modified": False, "human_verified": False,
                "limitation": "AI role separation does not satisfy the brief's independent human held-out author/reviewer requirement"}})
    return gold, resolved


def _as_dict(value):
    return value.model_dump() if hasattr(value, "model_dump") else value


def evaluate_case(case_id, result, records, private_gold):
    """Code-check completed note against frozen source facts, not agent verdicts.

    task_success is deliberately null for unlabelled risk semantics. This checker
    does not claim to judge narrative causal claims or full-filing risk changes.
    """
    if isinstance(private_gold, (str, Path)):
        private_gold = [json.loads(line) for line in Path(private_gold).read_text().splitlines() if line.strip()]
    if isinstance(private_gold, list):
        matches = [g for g in private_gold if g["case_id"] == case_id]
        if len(matches) != 1:
            raise ValueError("Exactly one frozen gold record is required")
        gold = matches[0]
    else:
        gold = private_gold if private_gold.get("case_id") == case_id else private_gold[case_id]
    result, records = _as_dict(result), {k: _as_dict(v) for k, v in records.items()}
    note = _as_dict(result.get("note")) or {}
    body = note.get("body_markdown", "")
    citations = {_as_dict(c)["evidence_id"]: _as_dict(c) for c in note.get("citations", [])}
    errors, checks = [], []
    if result.get("status") != "completed":
        errors.append("run_not_completed")
    if not body:
        errors.append("final_note_missing")
    requested_pairs = {(t["ticker"], t["fiscal_year"]) for t in gold["numeric_targets"]}
    for key, record in records.items():
        if record.get("kind") in {"fact", "calculation"} and (record.get("ticker"), record.get("year")) not in requested_pairs:
            errors.append(f"{key}:out_of_scope_numeric_evidence")
    for target in gold["numeric_targets"]:
        ticker, year = target["ticker"], target["fiscal_year"]
        pair = f"{ticker}:FY{year}"
        found = {}
        for metric in ("revenue", "operating_income"):
            expected = target[metric]
            matching = [(key, record) for key, record in records.items() if record.get("kind") == "fact"
                        and record.get("ticker") == ticker and record.get("year") == year and record.get("metric") == metric]
            if expected["status"] != "ok":
                if matching:
                    errors.append(f"{pair}:{metric}:unsupported_substitution")
                continue
            if len(matching) != 1:
                errors.append(f"{pair}:{metric}:expected_one_fact_got_{len(matching)}")
                continue
            key, record = matching[0]
            fact = record.get("fact", {})
            expected_top = {"ticker": ticker, "year": year, "metric": metric, "value": expected["value"], "unit": "USD", "source_sha256": expected["snapshot_sha256"], "source_url": expected["source_url"]}
            expected_fact = {"status": "ok", "ticker": ticker, "fiscal_year": year, "metric": metric,
                "value": int(expected["value"]), "unit": "USD", "concept": expected["concept"],
                "period_start": target["period_start"], "period_end": target["period_end"],
                "filed": expected["filed"], "filing_identifiers": expected["filing_identifiers"],
                "snapshot_sha256": expected["snapshot_sha256"], "filings_snapshot_sha256": expected["filings_snapshot_sha256"]}
            mismatches = [f"record.{k}" for k, v in expected_top.items() if record.get(k) != v]
            mismatches += [f"fact.{k}" for k, v in expected_fact.items() if fact.get(k) != v]
            exact_id = expected["expected_fact_id_prefix"] + digest(fact)[:12]
            if key != exact_id:
                mismatches.append("exact_fact_id")
            if mismatches:
                errors.append(f"{pair}:{metric}:mismatch:" + ",".join(mismatches))
            found[metric] = (key, record)
        margins = [(key, record) for key, record in records.items() if record.get("kind") == "calculation"
                   and record.get("ticker") == ticker and record.get("year") == year]
        if target["margin"]["status"] != "ok":
            if margins:
                errors.append(f"{pair}:fabricated_margin")
            if not re.search(rf"\b{re.escape(ticker)}\s+FY{year}\b", body) or not re.search(r"unavailable|missing|cannot|not available", body, re.I):
                errors.append(f"{pair}:missing_limitation_not_disclosed")
            checks.append({"ticker": ticker, "year": year, "expected": "abstain_missing_gaap_input"})
            continue
        if len(margins) != 1 or set(found) != {"revenue", "operating_income"}:
            errors.append(f"{pair}:missing_or_duplicate_margin")
            continue
        key, calculation = margins[0]
        income_id, income = found["operating_income"]
        revenue_id, revenue = found["revenue"]
        wanted = target["margin"]["value"]
        if calculation.get("value") != wanted or calculation.get("unit") != "%" or calculation.get("inputs") != [income_id, revenue_id] or calculation.get("formula") != "100 * operating_income / revenue":
            errors.append(f"{pair}:wrong_calculation_or_inputs")
        if key != "margin_" + digest([revenue_id, income_id, wanted])[:32]:
            errors.append(f"{pair}:wrong_calculation_id")
        # Check the delivered table, not merely successful tool calls/evidence.
        row = (f"| {ticker} | {year} | {target['period_start']} to {target['period_end']} | "
               f"{Decimal(target['revenue']['value']):,.0f} [{revenue_id}] | "
               f"{Decimal(target['operating_income']['value']):,.0f} [{income_id}] | {wanted}% [{key}] |")
        if body.count(row) != 1:
            errors.append(f"{pair}:final_table_row_missing_or_wrong")
        for eid in (income_id, revenue_id, key):
            source = records[eid]
            citation = citations.get(eid, {})
            if citation.get("source_sha256") != source.get("source_sha256") or citation.get("source_url") != source.get("source_url"):
                errors.append(f"{pair}:{eid}:final_citation_missing_or_wrong")
        checks.append({"ticker": ticker, "year": year, "expected_margin_percent": wanted})
    numeric_success = not errors
    requires_human = gold["requires_human_adjudication"]
    # A reviewed adjudication must be merged separately, never inferred here.
    return {"case_id": case_id, "numeric_success": numeric_success,
            "task_success": None if requires_human else numeric_success,
            "risk_semantic_success": None, "risk_label_status": "pending_human_label" if requires_human else "not_applicable",
            "numeric_errors": errors, "numeric_checks": checks,
            "metric_scope": "Frozen annual SEC fact identities, exact matched periods, Decimal margin, delivered table/citations, missing-input abstention; no narrative semantic judgment"}


def _write_jsonl(path, rows):
    path.write_text("".join(canonical(r) + "\n" for r in rows))
    path.chmod(0o600)


def freeze(output_dir, data_dir):
    output = Path(output_dir).resolve()
    if (output / "freeze_manifest.json").exists():
        raise ValueError("Already frozen; create a new version rather than overwriting evaluation material")
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    cases = author_cases()
    gold, resolved = build_gold(cases, data_dir)
    _write_jsonl(output / "cases.dev.jsonl", [c for c in cases if c["split"] == "dev"])
    _write_jsonl(output / "cases.heldout.jsonl", [c for c in cases if c["split"] == "heldout"])
    _write_jsonl(output / "gold.private.jsonl", gold)
    _write_jsonl(output / "numeric_truth.private.jsonl", [v for company in resolved.values() for v in company.values()])
    # Snapshot copies are evaluation inputs, never mounted into the model/tool process.
    source_root = Path(data_dir)
    snapshots = output / "sec_source_snapshot"
    snapshots.mkdir(mode=0o700)
    source_hashes = {}
    for ticker in sorted(resolved):
        for suffix in ("companyfacts.json", "filings.json", "companyfacts_metadata.json"):
            source = source_root / f"{ticker}_{suffix}"
            if source.exists():
                destination = snapshots / source.name
                shutil.copyfile(source, destination)
                destination.chmod(0o600)
                source_hashes[str(destination.relative_to(output))] = sha(destination)
        history = json.loads((source_root / f"{ticker}_filings.json").read_text())
        for filing in history["annual_filings"]:
            evidence = filing.get("fiscal_year_evidence")
            if evidence:
                relative = Path("sec_fiscal_years") / (evidence["source_sha256"] + ".html")
                target = snapshots / relative
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(source_root / relative, target)
                target.chmod(0o600)
                source_hashes[str(target.relative_to(output))] = sha(target)
    worksheet = []
    for ticker in sorted(resolved):
        history = json.loads((source_root / f"{ticker}_filings.json").read_text())
        for year in (2024, 2025):
            sources = []
            for y in (year-1, year):
                end = resolved[ticker][y]["period_end"]
                candidates = [f for f in history["annual_filings"] if f.get("reportDate") == end and f["form"] == "10-K"]
                row = min(candidates, key=lambda f: f["filingDate"])
                cik = int(history["cik"])
                sources.append({"fiscal_year": y, "filing_identifier": row["accessionNumber"], "report_date": end,
                    "source_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{row['accessionNumber'].replace('-', '')}/{row['primaryDocument']}",
                    "required_item": "1A", "complete_section_reviewed": False, "passage_ids": [], "source_sha256": None})
            worksheet.append({"label_id": f"RISK_{ticker}_{year}", "ticker": ticker, "company_year": year, "prior_fiscal_year": year-1,
                "sources": sources, "status": "pending_independent_human_label", "reviewer_name": "", "reviewed_at": None,
                "risk_changes": [], "overall_label": None,
                "allowed_change_labels": ["added", "removed", "materially_changed", "unchanged", "insufficient_evidence"],
                "required_per_change": ["topic", "label", "prior_passage_ids", "current_passage_ids", "source_hashes", "explanation"],
                "instructions": "Read complete Item 1A for both fiscal years. Populate source hashes and passage IDs. Added/removed needs exhaustive section review. Leave pending if not reviewed. Retrieval relevance labels and AI judgments are not human risk-change labels."})
    _write_jsonl(output / "risk_label_worksheet.private.jsonl", worksheet)
    readme = """# Frozen evaluation material — evaluator only\n\n60 AI-assisted requests; 20 development, 40 held out. Fifteen company scenario groups, four distinct requests per group. All variants and all peers of a company stay within the same split. Each request uses the existing operating-margin workflow; 30 also ask for qualitative risk comparison.\n\nThe case-author agent was separately assigned, did not change agent runtime code and did not inspect agent-run outputs. This is AI-assisted role separation, NOT the independent human held-out author/reviewer required by the brief. A human should review/freeze content before claiming full rubric completion.\n\nNumeric gold was derived before any agent run from cached original SEC companyfacts and filing history. Annual periods require current-filing FY/report-date matches (or hash-verified original-filing corrections), full-year durations and equal numerator/denominator dates. Values use latest matching annual filings in the frozen source snapshot. IBM's supported GAAP operating-income concept is absent in all three years: its expected behavior is explicit abstention, never pretax substitution.\n\nNo risk changes have been hand labelled. The 30-company-year worksheet is blank by design. All risk semantic results and overall risk-case success remain null until separate human adjudication. Existing retrieval relevance labels do not substitute for these labels.\n\nFreeze manifest hashes cases, expected outcomes, source snapshots and authoring code. Do not edit after the first run; use a new version and disclose any reclassification after held-out inspection. The evaluator may pass only case.request to the adapter. Public here means no gold fields; held-out requests still remain private until the individual run.\n\nFilesystem modes are only a local hygiene measure. Running the model under the same unrestricted OS account is NOT isolation. Launch the agent in a child process/container with only a public request and public corpus roots; do not mount this folder or authoring code. Enforce tool allowlists excluding arbitrary filesystem reads. Freeze checks must reject path overlap with the agent project, data, passages, corpus and index roots.\n\nThe numerical checker verifies original fact identity, exact runtime fact-ID derivation, Decimal margin, final rendered table/citations and missing-input abstention. It does not use the agent critic's verdict as ground truth, and it does not score semantic explanations.\n"""
    (output / "README.private.md").write_text(readme)
    files = {p.name: sha(p) for p in output.iterdir() if p.is_file() and p.name != "freeze_manifest.json"}
    manifest = {"schema_version": SCHEMA_VERSION, "frozen_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(cases), "split_counts": dict(Counter(c["split"] for c in cases)),
        "company_count": len(resolved), "company_year_count": sum(len(v) for v in resolved.values()),
        "risk_case_count": sum(c["request"]["include_risks"] for c in cases), "risk_company_years_pending": len(worksheet),
        "source_hashes": source_hashes, "artifact_hashes": files,
        "authoring_code_sha256": sha(__file__), "authorship": "AI-assisted, not independently human verified",
        "before_any_agent_run": True, "split_policy": "company-disjoint; primary-company related variants and all peers stay in the same split",
        "human_risk_labels_complete": False, "agent_filesystem_isolation": "must be enforced by evaluator launch; filesystem modes alone do not suffice"}
    (output / "freeze_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "freeze_manifest.json").chmod(0o600)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(freeze(args.output_dir, args.data_dir), indent=2))


if __name__ == "__main__":
    main()
