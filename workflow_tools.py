"""Role-bound adapters to the four existing MCP servers. No arbitrary endpoint tool."""
import asyncio
from decimal import Decimal, ROUND_HALF_EVEN
import time

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from company_registry import filing_url
from research_tools_client import direct_client, local_url
from workflow_contracts import WorkflowError, ROLE_TOOLS, canonical, digest, guard_text


SPECS = {
    "search_filings": {"ticker": "one approved ticker", "year": "integer approved fiscal year", "item": "1A or 7 or 8", "query": "short search phrase; no relative dates"},
    "get_passage": {"passage_id": "previously retrieved passage ID"},
    "get_table_rows": {"passage_id": "previously retrieved table ID", "offset": "integer >=0", "limit": "integer 1..10"},
    "get_section": {"ticker": "one approved ticker", "year": "integer approved fiscal year", "item": "1A or 7 or 8", "offset": "integer >=0"},
    "collect_financials": {},
}


class MCPBackend:
    def __init__(self, keys, urls=None):
        # The graph receives only these scoped service credentials. In
        # particular it has no PORTAL_REVIEW_TOKEN or OpenRouter key.
        self.tokens = {"sandbox": keys.get("SANDBOX_API_TOKEN"), "portal_read": keys.get("PORTAL_READ_TOKEN"),
                       "portal_draft": keys.get("PORTAL_DRAFT_TOKEN"), "portal_publish": keys.get("PORTAL_PUBLISH_TOKEN")}
        self.urls = urls or {"edgar": "http://127.0.0.1:8001", "kb": "http://127.0.0.1:8002",
                            "sandbox": "http://127.0.0.1:8003", "portal": "http://127.0.0.1:8004"}
        self.urls = {key: local_url(value) for key, value in self.urls.items()}

    async def call(self, service, tool, arguments, access=None):
        token_key = "sandbox" if service == "sandbox" else "portal_"+(access or "read") if service == "portal" else None
        token = self.tokens.get(token_key)
        if token_key and not token:
            raise WorkflowError("missing_credential", "A required MCP service credential is missing.")
        headers = {"Authorization": "Bearer "+token} if token else {}
        transport = StreamableHttpTransport(self.urls[service]+"/mcp", headers=headers,
                    httpx_client_factory=direct_client, max_sse_event_size=2*1024*1024)
        try:
            async with asyncio.timeout(90):
                async with Client(transport, timeout=80, init_timeout=10) as client:
                    result = await client.call_tool(tool, arguments, raise_on_error=False)
                    data = result.structured_content
            if not isinstance(data, dict) or len(canonical(data).encode()) > 2*1024*1024:
                raise ValueError("Invalid result")
            if data.get("status") != "ok":
                return {"status": "error", "code": "evidence_unavailable", "upstream_status": data.get("status")}
            return data.get("data", data)
        except WorkflowError:
            raise
        except Exception:
            raise WorkflowError("mcp_unavailable", "An MCP service did not return a valid result. No fallback was used.") from None


class ToolBroker:
    def __init__(self, backend, store, run, request):
        self.backend, self.store, self.run, self.request = backend, store, run, request

    def evidence_overview(self, role, company=None):
        """Small durable source index, independent of the last few observations.

        A retrieved slot only means that some passages exist; it never means an
        entire Item was compared or that a financial claim has been verified.
        """
        if role not in {"researcher", "risk_diff"}:
            return None
        tickers = [company] if company is not None else self.request["tickers"]
        items = ["1A"] if role == "risk_diff" else ["7", *(["1A"] if self.request["include_risks"] else [])]
        groups = {}
        for key, record in self.store.evidence(self.run).items():
            if (record["kind"] == "passage" and record["ticker"] in tickers
                    and record["year"] in self.request["years"] and record["item"] in items):
                slot = (record["ticker"], record["year"], record["item"])
                groups.setdefault(slot, []).append(key)
        wanted = [(t, y, i) for t in tickers for y in self.request["years"] for i in items]
        slots = [{"ticker": t, "year": y, "item": i, "passage_count": len(groups.get((t,y,i), [])),
                  "sample_ids": groups.get((t,y,i), [])[:2]} for t,y,i in wanted]
        return {"slots": slots, "missing_slots": [{k: v for k,v in s.items() if k in {"ticker","year","item"}}
                for s in slots if s["passage_count"] == 0], "complete_section_coverage": False,
                "instruction": "These are saved source samples, not complete coverage. Read known IDs with get_passage if needed. Prioritise missing company/year/Items; do not repeatedly fetch the same page. Report unresolved limits in the handoff."}

    def research_source_context(self, company):
        """Expose scoped originals once, including IDs beyond the first samples."""
        self.scope(company, self.request["years"][0], company)
        coverage = self.evidence_overview("researcher", company)
        groups = {(s["ticker"], s["year"], s["item"]): [] for s in coverage["slots"]}
        records = self.store.evidence(self.run)
        for key, record in records.items():
            slot = (record["ticker"], record["year"], record.get("item"))
            if record["kind"] == "passage" and slot in groups:
                groups[slot].append(key)
        catalogue = [{"ticker": ticker, "year": year, "item": item,
                      "passages": [{"id": key, "source_characters": len(records[key]["passage"].get("text") or "")}
                                   for key in keys]}
                     for (ticker, year, item), keys in groups.items()]
        # Every registered ID stays visible. Prefer a bounded spread of samples
        # instead of repeatedly showing only the first two IDs in a slot.
        selected = list(dict.fromkeys(key for keys in groups.values()
                                     for key in ([keys[0], keys[-1]] if keys else [])))
        allowance = min(1000, 8000 // max(1, len(selected)))
        samples = []
        for key in selected:
            record = records[key]
            raw = (record["passage"].get("text") or "").encode()
            samples.append({"id": key, "source_excerpt": raw[:allowance].decode("utf-8", errors="ignore"),
                            "excerpt_truncated": len(raw) > allowance, "untrusted_data": True})
        context = {"source_catalogue": catalogue, "source_samples": samples,
                   "complete_section_coverage": False,
                   "instruction": "These are registered original-source samples, not complete section or topic coverage. Every scoped registered passage ID is listed, including IDs outside the bounded previews. Use get_passage for fuller text and targeted search for unresolved topics. A short preview does not establish an absent disclosure."}
        if len(canonical(context).encode()) > 18000:
            raise WorkflowError("evidence_context_limit", "The scoped source index exceeds the bounded research context. Narrow the source scope.")
        return context

    async def research_prefetch(self, company):
        """Perform required missing-slot lookups without model round trips.

        Only actual calls supply operation receipts. Existing passage records
        independently supply samples, never an invented page-fetch receipt.
        Prior failed/empty prefetches remain visible and are not auto-retried.
        """
        self.scope(company, self.request["years"][0], company)
        coverage = self.evidence_overview("researcher", company)
        prior = {e["details"]["operation_sha256"]: e["details"]
                 for e in self.store.report(self.run)["events"]
                 if e["kind"] == "research_section_prefetch" and e["role"] == "researcher"}
        lookups = []
        for slot in coverage["slots"]:
            args = {k: slot[k] for k in ("ticker", "year", "item")}
            args["offset"] = 0
            operation = digest(["get_section", args])
            receipt = prior.get(operation)
            reused = receipt is not None
            if receipt is None and not slot["passage_count"]:
                result = await self.agent_call("researcher", "get_section", args, company)
                receipt = {"operation_sha256": operation, "result_sha256": digest(result),
                           "status": "error" if result.get("status") == "error" else "ok",
                           "passage_ids": [p["passage_id"] for p in result.get("passages", [])]}
                for field in ("next_offset", "total_passages"):
                    if result.get(field) is None or type(result.get(field)) is int:
                        receipt[field] = result.get(field)
                if receipt["status"] == "error":
                    receipt["code"] = "evidence_unavailable"
                self.store.event(self.run, "research_section_prefetch", "researcher", receipt)
            if receipt is not None:
                # The current registry must still contain exactly scoped source
                # records before a historical operation is offered for reuse.
                for record in self.store.evidence(self.run, receipt["passage_ids"]).values():
                    self.scope(record["ticker"], record["year"], company)
                    if record["kind"] != "passage" or record.get("item") != args["item"] or record["year"] != args["year"]:
                        raise WorkflowError("changed_evidence", "A saved prefetch receipt no longer matches its section scope.")
                lookups.append({"arguments": args, "receipt": receipt, "reused": reused})
        return {"lookups": lookups, "sources": self.research_source_context(company)}

    def validate_research_prefetch(self, company, lookups):
        """Only journaled broker operations may seed an agent's call history."""
        self.scope(company, self.request["years"][0], company)
        if not isinstance(lookups, list) or len(lookups) > len(self.request["years"]) * 2:
            raise WorkflowError("invalid_prefetch_seed", "Invalid required-section lookup seed.")
        receipts = {e["details"]["operation_sha256"]: e["details"]
                    for e in self.store.report(self.run)["events"]
                    if e["kind"] == "research_section_prefetch" and e["role"] == "researcher"}
        seen = set()
        for lookup in lookups:
            if not isinstance(lookup, dict) or set(lookup) != {"arguments", "receipt", "reused"}:
                raise WorkflowError("invalid_prefetch_seed", "Invalid required-section lookup seed.")
            args = lookup["arguments"]
            if (not isinstance(args, dict) or set(args) != {"ticker", "year", "item", "offset"}
                    or args["ticker"] != company or type(args["year"]) is not int
                    or args["year"] not in self.request["years"] or type(args["offset"]) is not int
                    or args["offset"] != 0 or args["item"] not in ["7", *(["1A"] if self.request["include_risks"] else [])]):
                raise WorkflowError("invalid_prefetch_seed", "A required-section seed is outside the company scope.")
            operation = digest(["get_section", args])
            if (operation in seen or operation not in receipts or not isinstance(lookup["receipt"], dict)
                    or receipts[operation] != lookup["receipt"] or type(lookup["reused"]) is not bool):
                raise WorkflowError("invalid_prefetch_seed", "A required-section seed lacks its exact broker receipt.")
            seen.add(operation)
            for record in self.store.evidence(self.run, lookup["receipt"]["passage_ids"]).values():
                if (record["kind"] != "passage" or record["ticker"] != company
                        or record["year"] != args["year"] or record.get("item") != args["item"]):
                    raise WorkflowError("invalid_prefetch_seed", "A required-section seed contains evidence outside its slot.")

    def tool_specs(self, role, company=None):
        """Give each model branch exact types, required fields and its own scope."""
        tickers = [company] if company is not None else list(self.request["tickers"])
        fields = {
            "ticker": {"type": "string", "enum": tickers},
            "year": {"type": "integer", "enum": list(self.request["years"])},
            "item": {"type": "string", "enum": ["1A", "7", "8"]},
            "query": {"type": "string", "minLength": 3, "maxLength": 600,
                      "description": "Short search phrase; no URLs or relative dates."},
            "passage_id": {"type": "string", "minLength": 1,
                           "description": "An ID already retrieved within this branch's scope."},
            "offset": {"type": "integer", "minimum": 0,
                       "description": "Required. Use 0 for the first page."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        }
        return {name: {"type": "object", "properties": {key: fields[key] for key in SPECS[name]},
                       "required": list(SPECS[name]), "additionalProperties": False}
                for name in ROLE_TOOLS.get(role, ()) if name in SPECS}

    def writing_evidence(self):
        """Bounded original-source excerpts and ledger facts, never agent claims."""
        records = self.store.evidence(self.run)
        coverage = self.evidence_overview("researcher")
        ids = [key for slot in coverage["slots"] for key in slot["sample_ids"]]
        allowance = min(1200, 14000 // max(1, len(ids)))
        excerpts = []
        for key in ids:
            record = records[key]
            p = record["passage"]
            raw = (p.get("text") or "").encode()
            excerpt = raw[:allowance].decode("utf-8", errors="ignore")
            excerpts.append({"id": key, "ticker": record["ticker"], "year": record["year"],
                "item": record["item"], "source_excerpt": excerpt,
                "excerpt_truncated": len(raw) > allowance, "untrusted_data": True,
                "read_full_text_with": "get_passage"})
        margins = []
        for key, r in records.items():
            if r["kind"] == "calculation":
                income, revenue = [records[k] for k in r["inputs"]]
                margins.append({"id": key, "ticker": r["ticker"], "year": r["year"],
                    "margin_percent": r["value"], "income": income["value"],
                    "revenue": revenue["value"], "unit": income["unit"],
                    "period_start": income["fact"]["period_start"],
                    "period_end": income["fact"]["period_end"], "input_ids": r["inputs"]})
        covered = {(r["ticker"], r["year"]) for r in margins}
        return {"source_samples": excerpts, "source_coverage": coverage, "verified_margins": margins,
                "missing_margin_pairs": [[t,y] for t in self.request["tickers"] for y in self.request["years"] if (t,y) not in covered],
                "instruction": "Ledger values and source samples take precedence over agent summaries. A missing source sample is a retrieval gap, not proof that a filing lacks a disclosure. Calculation receipts support arithmetic, not causal explanations. Full evidence is independently replayed before publication."}

    def agent_scope(self, role, tool, ticker, year, company=None):
        # Only rejected model INPUT can be corrected. Source results continue to
        # use scope() directly and fail closed if their provenance is wrong.
        try:
            self.scope(ticker, year, company)
        except WorkflowError:
            self.store.event(self.run, "tool_denied", role, {
                "operation_sha256": digest(tool), "reason": "request_scope",
                "requested_scope_sha256": digest([ticker, year]), "company_scope": company})
            allowed = [company] if company is not None else self.request["tickers"]
            raise WorkflowError("invalid_tool_scope", "No tool was called. Use a ticker from "
                +canonical(allowed)+" and an integer year from "+canonical(self.request["years"])+".") from None

    async def invoke(self, role, service, tool, args, access=None):
        # Internal mappings are fixed in code, never supplied by a model.
        allowed = {
            "researcher": {("kb","search_filings"),("kb","get_passage"),("kb","get_table_rows"),("edgar","get_section")},
            "risk_diff": {("kb","search_filings"),("kb","get_passage"),("edgar","get_section")},
            "quant": {("edgar","get_annual_financial_fact"),("sandbox","execute_python")},
            "writer": {("kb","get_passage")},
            "critic": {("kb","get_passage"),("edgar","get_annual_financial_fact")},
            "publisher": {("portal","prepare_note"),("portal","get_proposal"),("portal","publish_note")},
        }
        if (service,tool) not in allowed.get(role,set()):
            self.store.event(self.run, "tool_denied", role, {"operation_sha256": digest([service,tool]), "reason": "role_permission"})
            raise WorkflowError("permission_denied", "This role has no permission for that MCP operation.")
        if service == "portal" and access != {"prepare_note":"draft","get_proposal":"read","publish_note":"publish"}[tool]:
            raise WorkflowError("permission_denied", "Wrong credential scope for the portal operation.")
        self.store.reserve(self.run, tools=1)
        started = time.monotonic()
        self.store.event(self.run, "tool_start", role, {"service": service, "tool": tool, "arguments_sha256": digest(args)})
        try:
            value = await self.backend.call(service, tool, args, access)
        except BaseException:
            self.store.event(self.run, "tool_error", role, {"service": service, "tool": tool})
            raise
        self.store.event(self.run, "tool_end", role, {"service": service, "tool": tool, "result_sha256": digest(value),
                         "elapsed_seconds": round(time.monotonic()-started, 4), "status": value.get("status", "ok")})
        return value

    def scope(self, ticker, year, company=None):
        if ticker not in self.request["tickers"] or (company is not None and ticker != company) or type(year) is not int or year not in self.request["years"]:
            raise WorkflowError("scope_denied", "The tool request is outside the human-reviewed company/year scope.")

    def register_passage(self, p, company=None):
        self.scope(p["ticker"], p["fiscal_year"], company)
        record = {"kind": "passage", "ticker": p["ticker"], "year": p["fiscal_year"], "item": p["item"],
                  "source_url": p["source_url"], "source_sha256": p["source_sha256"], "passage": p}
        self.store.save_evidence(self.run, p["passage_id"], record)
        return p["passage_id"]

    async def agent_call(self, role, name, args, company=None):
        if name not in ROLE_TOOLS.get(role, ()) or name not in SPECS:
            raise WorkflowError("permission_denied", "This role cannot call the requested tool.")
        if not isinstance(args, dict) or set(args) != set(SPECS[name]):
            raise WorkflowError("invalid_tool_arguments", "Use exactly the fields in the tool description.")
        if name == "collect_financials":
            return await self.financials()
        if name in {"get_passage", "get_table_rows"}:
            pid = args["passage_id"]
            if not isinstance(pid, str) or not pid:
                raise WorkflowError("invalid_tool_arguments", "passage_id must be a nonempty string.")
            previous = self.store.evidence(self.run, [pid])[pid]
            if previous["kind"] != "passage":
                raise WorkflowError("invalid_evidence_kind", "This tool needs a passage ID.")
            self.agent_scope(role, name, previous["ticker"], previous["year"], company)
            if name == "get_table_rows":
                if type(args["offset"]) is not int or args["offset"] < 0 or type(args["limit"]) is not int or not 1 <= args["limit"] <= 10:
                    raise WorkflowError("invalid_tool_arguments", "Invalid table page.")
            result = await self.invoke(role, "kb", name, args)
            if result.get("status") == "error":
                return result
            p = result.get("passage")
            if p:
                self.register_passage(p, company)
            elif result.get("source_sha256") != previous["source_sha256"]:
                raise WorkflowError("changed_evidence", "Table source changed.")
            return result
        self.agent_scope(role, name, args["ticker"], args["year"], company)
        if not isinstance(args["item"], str) or args["item"] not in {"1A", "7", "8"}:
            raise WorkflowError("invalid_tool_arguments", "item must be one of the strings 1A, 7 or 8. No tool was called.")
        if name == "get_section":
            if type(args["offset"]) is not int or args["offset"] < 0:
                raise WorkflowError("invalid_tool_arguments", "Invalid section page.")
            result = await self.invoke(role, "edgar", name, {**args, "limit": 2})
            for p in result.get("passages", []):
                self.register_passage(p, company)
            return result
        query = args["query"]
        if not isinstance(query, str) or not 3 <= len(query) <= 600 or "http" in query.lower():
            raise WorkflowError("invalid_tool_arguments", "Use a bounded text search, without URLs.")
        guard_text(query)
        result = await self.invoke(role, "kb", name, {"request": {"query": query,
            "tickers": [args["ticker"]], "years": [args["year"]], "items": [args["item"]],
            "kind": "all", "limit": 2, "decompose": False}})
        if result.get("status") == "error":
            return result
        # Read full source records before making IDs available to the writer.
        for hit in result.get("hits", []):
            full = await self.invoke(role, "kb", "get_passage", {"passage_id": hit["passage_id"]})
            if "passage" not in full:
                raise WorkflowError("missing_passage", "Search result cannot be resolved to its source passage.")
            self.register_passage(full["passage"], company)
        return result

    async def financials(self):
        rows, issues = [], []
        for ticker in self.request["tickers"]:
            for year in self.request["years"]:
                facts = {}
                for metric in ("revenue", "operating_income"):
                    args = {"ticker": ticker, "metric": metric, "year": year, "as_of": None}
                    fact = await self.invoke("quant", "edgar", "get_annual_financial_fact", args)
                    if fact.get("status") != "ok":
                        issues.append(f"{ticker} FY{year}: {metric} unavailable; no substitution.")
                        continue
                    if (fact.get("ticker") != ticker or fact.get("fiscal_year") != year or fact.get("metric") != metric
                            or fact.get("unit") != "USD" or type(fact.get("value")) is not int):
                        raise WorkflowError("invalid_fact", "A fact does not match the requested company/year/measure/unit.")
                    accession = fact["filing_identifiers"][0]
                    fid = f"f_{fact['concept']}_{fact['period_start']}_{fact['period_end']}_{accession}_{digest(fact)[:12]}"
                    record = {"kind": "fact", "ticker": ticker, "year": year, "metric": metric, "value": str(fact["value"]), "unit": "USD",
                        "source_sha256": fact["snapshot_sha256"], "source_url": filing_url(ticker, accession), "arguments": args, "fact": fact}
                    self.store.save_evidence(self.run, fid, record)
                    facts[metric] = (fid, record)
                if len(facts) != 2:
                    continue
                revenue_id, revenue = facts["revenue"]
                income_id, income = facts["operating_income"]
                if any(income["fact"][k] != revenue["fact"][k] for k in ("period_start", "period_end", "unit")) or Decimal(revenue["value"]) <= 0:
                    issues.append(f"{ticker} FY{year}: incompatible periods or nonpositive revenue; margin not calculated.")
                    continue
                code = 'result = (Decimal(inputs["income"]) / Decimal(inputs["revenue"]) * 100).quantize(Decimal("0.01"))'
                calculated = await self.invoke("quant", "sandbox", "execute_python", {"code": code, "inputs": {"income": income["value"], "revenue": revenue["value"]}})
                expected = str((Decimal(income["value"])/Decimal(revenue["value"])*100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                if calculated.get("result") != expected or not all(calculated.get("kernel_probes", {}).get(k) is True for k in ("network_denied", "file_open_denied", "process_creation_denied")):
                    raise WorkflowError("invalid_calculation", "Sandbox output or isolation probes did not verify.")
                mid = "margin_"+digest([revenue_id, income_id, expected])[:32]
                record = {"kind": "calculation", "ticker": ticker, "year": year, "value": expected, "unit": "%", "inputs": [income_id, revenue_id],
                          "source_url": income["source_url"], "source_sha256": income["source_sha256"], "formula": "100 * operating_income / revenue"}
                self.store.save_evidence(self.run, mid, record)
                rows.append({"ticker": ticker, "year": year, "revenue_id": revenue_id, "income_id": income_id, "margin_id": mid, "margin_percent": expected})
        return {"rows": rows, "issues": issues, "status": "ok" if rows else "error"}

    async def reverify(self, keys):
        records = self.store.evidence(self.run, keys)
        all_keys = set(keys)
        for record in records.values():
            all_keys.update(record.get("inputs", []))
        records = self.store.evidence(self.run, sorted(all_keys))
        for key, record in records.items():
            if record["kind"] == "fact":
                current = await self.invoke("critic", "edgar", "get_annual_financial_fact", record["arguments"])
                if canonical(current) != canonical(record["fact"]):
                    raise WorkflowError("changed_evidence", "The SEC fact changed; this draft cannot be published.")
                if (record["value"] != str(current["value"]) or record["unit"] != current["unit"]
                        or record["ticker"] != current["ticker"] or record["year"] != current["fiscal_year"]
                        or record["metric"] != current["metric"] or record["source_sha256"] != current["snapshot_sha256"]
                        or record["source_url"] != filing_url(current["ticker"],current["filing_identifiers"][0])):
                    raise WorkflowError("invalid_fact_receipt", "The stored figure or citation does not match the verified SEC fact.")
            elif record["kind"] == "passage":
                current = await self.invoke("critic", "kb", "get_passage", {"passage_id": key})
                if canonical(current.get("passage")) != canonical(record["passage"]):
                    raise WorkflowError("changed_evidence", "A cited passage changed; this draft cannot be published.")
                if any(record[k] != current["passage"][k] for k in ("ticker","item","source_sha256","source_url")) or record["year"] != current["passage"]["fiscal_year"]:
                    raise WorkflowError("invalid_passage_receipt", "The stored citation does not match the verified passage.")
            elif record["kind"] == "calculation":
                income, revenue = [records[k] for k in record["inputs"]]
                if (income["kind"] != "fact" or revenue["kind"] != "fact" or income["metric"] != "operating_income" or revenue["metric"] != "revenue"
                    or any(income[k] != revenue[k] for k in ("ticker","year","unit"))
                    or any(income["fact"][k] != revenue["fact"][k] for k in ("period_start","period_end")) or Decimal(revenue["value"]) <= 0):
                    raise WorkflowError("invalid_calculation_receipt", "Calculation inputs have incompatible provenance.")
                expected = str((Decimal(income["value"])/Decimal(revenue["value"])*100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))
                if (record["value"] != expected or record["unit"] != "%" or record["ticker"] != income["ticker"] or record["year"] != income["year"]
                    or record["source_url"] != income["source_url"] or record["source_sha256"] != income["source_sha256"]):
                    raise WorkflowError("invalid_calculation_receipt", "The stored margin or citation does not match its checked inputs.")
            else:
                raise WorkflowError("invalid_evidence_kind", "The note contains an unsupported evidence type.")
        return records
