"""Bounded ReAct decisions over explicit role tools; provider secrets stay outside state."""
import asyncio
import json
from pathlib import Path
import time

import httpx
from pydantic import ValidationError
from workflow_contracts import AgentAction, Handoff, ROLE_TOOLS, WorkflowError, canonical, digest


MAX_RESPONSE_FAILURES = 3


def schema_feedback(error, schema, *, final=False):
    """Expose trusted field paths and size constraints, never rejected content."""
    fields = set()

    def collect(node):
        if isinstance(node, dict):
            fields.update(node.get("properties", {}))
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)
    collect(schema)
    messages = {
        "string_too_long": "Shorten the string to the maximum length.",
        "string_too_short": "Supply a string meeting the minimum length.",
        "too_long": "Reduce the number of entries to the maximum length.",
        "too_short": "Supply the required minimum number of entries.",
        "missing": "Supply this required field.",
        "extra_forbidden": "Remove the unrecognised field.",
        "json_invalid": "Return one valid JSON object, without Markdown fences.",
        "string_type": "Supply a JSON string.",
        "int_type": "Supply a JSON integer.",
        "bool_type": "Supply a JSON boolean.",
        "list_type": "Supply a JSON array.",
        "dict_type": "Supply a JSON object.",
    }
    result = []
    for item in error.errors(include_url=False, include_context=True, include_input=True)[:8]:
        path = "final" if final else "action"
        for part in item["loc"]:
            path += f"[{part}]" if type(part) is int else "."+(part if part in fields else "[unrecognised_field]")
        kind = item["type"]
        detail = {"path": path, "rule": messages.get(kind, "Match the supplied schema and action contract.")}
        for key in ("max_length", "min_length", "ge", "le"):
            bound = item.get("ctx", {}).get(key)
            if type(bound) in (int, float):
                detail[key] = bound
        rejected = item.get("input")
        if isinstance(rejected, (str, list, dict)):
            detail["observed_length"] = len(rejected)
        if item["loc"] and item["loc"][-1] == "quote":
            detail["instruction"] = "Select a shorter verbatim source excerpt; do not paraphrase, append ellipses, or change the claim. Fact/calculation references require an empty quote."
        result.append(detail)
    return result


def compact_observation(name, result):
    """Preserve source IDs and pagination before any text preview is shortened."""
    def passage(p, text_limit):
        value = {k: p[k] for k in ("passage_id", "ticker", "fiscal_year", "item", "kind", "source_sha256") if k in p}
        if isinstance(p.get("text"), str):
            value.update(text=p["text"][:text_limit], text_truncated=len(p["text"]) > text_limit)
        if isinstance(p.get("table"), dict):
            table = p["table"]
            value["table"] = {k: table[k] for k in ("table_id", "caption", "column_count") if k in table}
            value["table"].update(total_rows=len(table.get("rows", [])), rows_omitted=True,
                                  read_with="get_table_rows")
        value["untrusted_source"] = True
        return value

    value = result
    if name == "get_section":
        value = {k: result[k] for k in ("status", "code", "ticker", "fiscal_year", "item", "total_passages", "next_offset") if k in result}
        value["passages"] = [passage(p, 1200) for p in result.get("passages", [])]
    elif name == "get_passage" and isinstance(result.get("passage"), dict):
        value = {"passage": passage(result["passage"], 5000)}
    elif name == "search_filings" and "hits" in result:
        hits = []
        for hit in result["hits"]:
            row = {k: hit[k] for k in ("passage_id", "ticker", "fiscal_year", "item", "kind", "score") if k in hit}
            excerpt = hit.get("excerpt", "")
            row.update(excerpt=excerpt[:800], excerpt_truncated=len(excerpt) > 800)
            hits.append(row)
        value = {"hits": hits, "untrusted_source": True}
    # Large table pages remain explicitly incomplete previews. Full original
    # passages/receipts stay in the journal and are replay-verified separately.
    text = canonical(value)
    if len(text.encode()) > 8000:
        metadata = {k: result[k] for k in ("status", "code", "passage_id", "ticker", "fiscal_year", "item", "next_offset", "total_rows", "total_passages") if k in result}
        # Even a multibyte text or an unusually long caption must not push the
        # structured source identifiers out of the fallback preview.
        identifiers = ("passage_id", "ticker", "fiscal_year", "item", "kind", "source_sha256")
        for key in ("passages", "hits"):
            if key in value:
                metadata[key] = [{k: p[k] for k in identifiers if k in p} for p in value[key]]
        if "passage" in value:
            metadata["passage"] = {k: value["passage"][k] for k in identifiers if k in value["passage"]}
        metadata.update(result_preview=text.encode()[:4000].decode("utf-8", errors="ignore"),
                        preview_truncated=True, untrusted_source=True)
        return metadata
    return value


def action_summary(action, result):
    summary = {"tool": action.tool, "arguments": {k: v for k,v in action.arguments.items() if k != "query"}}
    if "query" in action.arguments:
        summary["query_sha256"] = digest(action.arguments["query"])
    for key in ("next_offset", "total_passages", "total_rows"):
        if key in result:
            summary[key] = result[key]
    return summary


def load_config(path=None):
    value = json.loads(Path(path or Path(__file__).with_name("workflow_config.json")).read_text())
    if value["model"] not in value["allowed_models"] or not 1000 <= value["token_budget"] <= 2000000 or not 10 <= value["tool_budget"] <= 500:
        raise ValueError("Invalid workflow model or budget configuration.")
    if not 1 <= value["max_output_tokens"] <= 4000 or not 1 <= value["max_agent_steps"] <= 15 or value["max_revisions"] != 2:
        raise ValueError("Invalid workflow execution bounds.")
    for role in ROLE_TOOLS:
        if set(value["prompts"][role]) != {"role", "context", "task", "constraints", "format", "examples"}:
            raise ValueError("Every role requires the six prompt slots.")
    return value


class OpenRouterModel:
    def __init__(self, key):
        if not key:
            raise WorkflowError("missing_model_key", "OPENROUTER_API_KEY is missing from the existing .env.")
        self._key = key

    async def complete(self, messages, config):
        payload = {"model": config["model"], "messages": messages, "temperature": config["temperature"],
                   "max_tokens": config["max_output_tokens"], "response_format": config.get("_response_format", {"type": "json_object"}),
                   "provider": {"allow_fallbacks": False, "require_parameters": True}}
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=90) as client:
                async with client.stream("POST", "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": "Bearer "+self._key}, json=payload) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 512000:
                            raise ValueError("Oversized model response")
                    if response.status_code >= 400:
                        provider_code = None
                        try:
                            candidate = json.loads(body).get("error", {}).get("code")
                            known_codes = {"invalid_request_error", "invalid_json_schema", "invalid_schema", "unsupported_parameter",
                                           "rate_limit_exceeded", "insufficient_quota", "provider_error"}
                            if type(candidate) is int and 0 <= candidate <= 999999 or type(candidate) is str and candidate in known_codes:
                                provider_code = candidate
                        except (ValueError, AttributeError):
                            pass
                        message = f"The configured model rejected the request (HTTP {response.status_code}"
                        message += f"; provider code {provider_code}" if provider_code is not None else ""
                        error = WorkflowError("model_http_error", message+"). No retry or model fallback occurred.")
                        error.http_status = response.status_code
                        error.provider_error_code = provider_code
                        raise error
                    response.raise_for_status()
            data = json.loads(body)
            choice = data["choices"][0]
            if choice["finish_reason"] != "stop" or data.get("model") != config["model"]:
                raise ValueError("Incomplete response or different model")
            usage = data["usage"]
            if any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
                raise ValueError("Missing usage")
            return {"content": choice["message"]["content"], "usage": {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")}, "model": data["model"]}
        except WorkflowError:
            raise
        except Exception:
            raise WorkflowError("model_unavailable", "The configured model did not return a complete usable response. No automatic retry or model substitution occurred.") from None


class Agents:
    def __init__(self, model, broker, store, run, config):
        self.model, self.broker, self.store, self.run, self.config = model, broker, store, run, config
        self.context_by_role = {}
        self.external_payload_guard = None

    async def ask(self, role, task, output_type, company=None, *, require_sections=False, validate_final=None, system_prompt=None,
                  initial_section_lookups=None, response_format=None):
        if self.model is None:
            raise WorkflowError("missing_model_key", "A configured model connection is required for this agent step.")
        observations = []
        completed_calls = {}
        call_history = []
        attempted_slots = set()
        response_failures = 0
        if response_format is not None and (role != "critic" or not isinstance(response_format, dict)
                or response_format.get("type") != "json_schema"
                or not isinstance(response_format.get("json_schema"), dict)
                or response_format["json_schema"].get("strict") is not True):
            raise WorkflowError("invalid_response_format", "Only the critic can use its strict response schema.")
        format_bytes = len(canonical(response_format).encode()) if response_format is not None else 0
        format_details = ({"response_format_sha256": digest(response_format), "response_format_bytes": format_bytes}
                          if response_format is not None else {})

        if initial_section_lookups is not None:
            if role != "researcher" or company is None or not require_sections:
                raise WorkflowError("invalid_prefetch_seed", "Only a scoped researcher can receive required-section lookup seeds.")
            self.broker.validate_research_prefetch(company, initial_section_lookups)
            for lookup in initial_section_lookups:
                args, receipt = lookup["arguments"], lookup["receipt"]
                attempted_slots.add(tuple(args[k] for k in ("ticker", "year", "item")))
                if receipt["status"] != "error":
                    summary = action_summary(AgentAction(tool="get_section", arguments=args, final=None), receipt)
                    completed_calls[receipt["operation_sha256"]] = summary
                    call_history.append(summary)
            if initial_section_lookups:
                observations.append({"prefetched_sections_available": True,
                    "instruction": "Required-section lookup outcomes are in prefetched_section_lookups; bounded original-source previews and all scoped IDs are in the task. Use saved samples, investigate topical gaps or read fuller passages. Failed/empty lookups remain explicit limits, not absent disclosures."})

        def correct_response(code, details, step):
            nonlocal response_failures
            response_failures += 1
            observations.append({"error": code, "details": details[:8],
                "required": "Return a complete valid AgentAction. Correct all occurrences of each listed error against the supplied schema and source evidence, including any beyond the first eight displayed fields. Do not invent support; use supported=false with a reason when a claim lacks evidence.",
                "repairs_remaining": MAX_RESPONSE_FAILURES-response_failures})
            self.store.event(self.run, "agent_correction", role, {
                "code": code, "step": step, "company_scope": company,
                "response_failures": response_failures, "error_count": len(details),
                "errors": details[:8]})
            if response_failures >= MAX_RESPONSE_FAILURES:
                self.store.event(self.run, "agent_response_limit", role, {
                    "company_scope": company, "failures": response_failures})
                raise WorkflowError("agent_response_limit", role+" returned three invalid responses. Execution stopped without accepting a verdict or making further calls; inspect the response diagnostics before continuing.")

        tools = self.broker.tool_specs(role, company)
        prompt = system_prompt if system_prompt is not None else self.config["prompts"][role]
        if set(prompt) != set(self.config["prompts"][role]) or prompt["role"] != role:
            raise ValueError("The effective role prompt must retain its six slots and role.")
        request = self.broker.request
        branch_assignment = None
        if role == "writer":
            # The full request remains immutable in workflow state. A writer
            # contributes only risk prose; code owns every financial sentence.
            request = {**self.broker.request,
                "question": "Write only the qualitative risk contribution for this original request: "
                    + self.broker.request["question"]
                    + " Financial answers, all quantities and operating-margin statements are generated separately by code and must not be repeated in this contribution."}
        if company is not None:
            # A narrowed ticker list with the original multi-company question
            # still asks this branch to compare peers it cannot access.
            request = {**self.broker.request, "tickers": [company],
                "question": "Collect source evidence for " + company
                    + " for the explicitly requested fiscal years to support an operating-margin comparison"
                    + (" and the requested risk discussion." if self.broker.request["include_risks"] else ".")}
            branch_assignment = {
                "assigned_company": company,
                "shared_question_background_only": self.broker.request["question"],
                "responsibility": "Research only the assigned company. Other company branches work in parallel and are not visible here. Never infer another company's source availability from this branch's observations.",
                "financial_stage": "Quant separately obtains revenue and operating income and calculates operating margins after the research branches join. A margin need not be printed in these narrative passages.",
                "source_limits": "A short or truncated preview is not a missing filing. Read more pages, use get_passage, or search the requested topic when samples are insufficient. Report only limitations within your assigned source scope."}
        for step in range(self.config["max_agent_steps"]):
            final_step = step == self.config["max_agent_steps"] - 1
            step_task = task
            if initial_section_lookups is not None:
                # Refresh in place rather than appending another source index:
                # later topical receipts must survive observation rollover.
                step_task = {**task, "registered_sources": self.broker.research_source_context(company)}
            context = {"request": request, "company_scope": company,
                       "task": step_task, "tools": {} if final_step else tools, "final_schema": output_type.model_json_schema(),
                       "action_schema": AgentAction.model_json_schema(),
                       "progress": {"step": step + 1, "max_steps": self.config["max_agent_steps"],
                                    "remaining_after_this": self.config["max_agent_steps"] - step - 1,
                                    "finish_required": final_step,
                                    "instruction": "Return finish with known evidence IDs and explicit unresolved limitations when ready. The final step is reserved for that handoff; no tool calls can execute on it."},
                       "completed_tool_requests": call_history,
                       "recent_observations": observations[-3:]}
            if initial_section_lookups is not None:
                # Keep every outcome visible, including failures which would
                # otherwise disappear behind the rolling observation window.
                # Original source text occurs only in the bounded task context.
                # Whether this invocation fetched or reused the same receipt is
                # not source evidence and must not invalidate an identical
                # cached model request after reopening the workflow.
                context["prefetched_section_lookups"] = [
                    {"arguments": row["arguments"], "receipt": row["receipt"]}
                    for row in initial_section_lookups]
            if branch_assignment is not None:
                context["branch_assignment"] = branch_assignment
            overview = self.broker.evidence_overview(role, company)
            if overview is not None:
                context["saved_evidence"] = overview
            if require_sections:
                context["section_requirement"] = "Before finish, every missing saved_evidence slot must receive an actual get_section or search_filings lookup. Missing slots are outstanding work, not unavailable reports. A failed/empty lookup must be reported as a retrieval limitation."
            if role in self.context_by_role:
                context["approved_background"] = self.context_by_role[role]
            messages = [{"role": "system", "content": canonical(prompt)},
                        {"role": "user", "content": canonical(context)}]
            if self.external_payload_guard is not None:
                self.external_payload_guard(messages)
            size = len(canonical(messages).encode()) + format_bytes
            if size > self.config["max_context_bytes"]:
                raise WorkflowError("context_limit", "The bounded context is full. Narrow the plan or reduce source scope.")
            cache_identity = [role, self.config["model"], messages]
            if response_format is not None:
                cache_identity.append(response_format)
            cache_key = digest(cache_identity)
            response = self.store.cached(self.run, cache_key)
            if response is None:
                # For the configured byte-level tokenizer, UTF-8 byte length is
                # a conservative input bound. The extra allowance covers chat
                # framing. No private chain-of-thought is requested or stored.
                reservation = size + self.config["max_output_tokens"] + 2048
                self.store.reserve(self.run, tokens=reservation)
                started = time.monotonic()
                self.store.event(self.run, "llm_start", role, {"prompt_sha256": digest(messages), "step": step, "company_scope": company, "model": self.config["model"], "reserved_tokens": reservation, **format_details})
                try:
                    call_config = self.config if response_format is None else {**self.config, "_response_format": response_format}
                    response = await self.model.complete(messages, call_config)
                except BaseException as exc:
                    error_details = {"http_status": exc.http_status, "provider_error_code": exc.provider_error_code} if isinstance(exc, WorkflowError) and getattr(exc, "code", None) == "model_http_error" else {}
                    self.store.event(self.run, "llm_error", role, {"prompt_sha256": digest(messages), "company_scope": company, **format_details, **error_details})
                    raise
                usage = response["usage"]
                self.store.event(self.run, "llm_end", role, {"prompt_sha256": digest(messages), "response_sha256": digest(response["content"]),
                                 "company_scope": company, "usage": usage, "elapsed_seconds": round(time.monotonic()-started, 4), **format_details})
                if usage["total_tokens"] > reservation:
                    raise WorkflowError("usage_overrun", "Provider usage exceeded the reserved bound; further execution is blocked.")
                self.store.cached(self.run, cache_key, response)
            else:
                self.store.event(self.run, "llm_cache_hit", role, {"prompt_sha256": digest(messages), "company_scope": company, **format_details})
            validating_final = False
            try:
                action = AgentAction.model_validate_json(response["content"])
                if action.tool == "finish":
                    validating_final = True
                    final = output_type.model_validate(action.final)
                    if validate_final is not None:
                        problems = validate_final(final.model_dump())
                        if problems:
                            correct_response("invalid_evidence_receipt", problems, step)
                            continue
                    if isinstance(final, Handoff):
                        self.store.evidence(self.run, final.evidence_ids)
                        if company:
                            for e in self.store.evidence(self.run, final.evidence_ids).values():
                                self.broker.agent_scope(role, "finish", e["ticker"], e["year"], company)
                        if require_sections:
                            missing = self.broker.evidence_overview(role, company)["missing_slots"]
                            unattempted = [s for s in missing if (s["ticker"], s["year"], s["item"]) not in attempted_slots]
                            if unattempted:
                                raise WorkflowError("incomplete_research", "Finish rejected: required sections have not been checked. Call get_section with offset 0 for "
                                    +canonical(unattempted)+". Do not describe unsearched sections as unavailable.")
                            if missing:
                                gap = "Retrieval attempts yielded no registered passages for "+canonical(missing)+". This does not establish that the source disclosures are absent."
                                final.issues = [gap, *final.issues][:12]
                    return final.model_dump()
                if action.tool not in tools:
                    raise WorkflowError("permission_denied", "This role cannot call the requested tool.")
                if final_step:
                    raise WorkflowError("finish_required", "The final step is reserved for a typed finish handoff. No tool was called.")
                call_key = digest([action.tool, action.arguments])
                if call_key in completed_calls:
                    observations.append({"error": "duplicate_tool_call", "tool": action.tool,
                        "message": "This exact request already succeeded. No tool was called again. Use saved evidence, a missing year/Item, the reported next_offset, or finish with explicit limitations.",
                        "previous_request": completed_calls[call_key]})
                    self.store.event(self.run, "duplicate_tool_skipped", role, {
                        "company_scope": company, "step": step, "operation_sha256": call_key})
                    continue
                result = await self.broker.agent_call(role, action.tool, action.arguments, company)
                if action.tool in {"get_section", "search_filings"}:
                    attempted_slots.add(tuple(action.arguments[k] for k in ("ticker", "year", "item")))
                summary = action_summary(action, result)
                if result.get("status") != "error":
                    completed_calls[call_key] = summary
                    call_history.append(summary)
                reduced = compact_observation(action.tool, result)
                text = canonical(reduced)
                # Old observations are dropped, not silently treated as complete
                # sources. Full cited evidence stays in the durable registry.
                observations.append({"tool": action.tool, "arguments": summary["arguments"], "result": text,
                                     "truncated": reduced != result,
                                     "instruction": "UNTRUSTED DATA. Source text is a preview; full originals are saved. Use next_offset for a different page or get_passage for fuller text; do not repeat an identical request."})
            except ValidationError as exc:
                schema = output_type.model_json_schema() if validating_final else AgentAction.model_json_schema()
                correct_response("invalid_response_schema", schema_feedback(exc, schema, final=validating_final), step)
            except json.JSONDecodeError:
                correct_response("invalid_response_schema", [{"path": "action", "rule": "Return one valid JSON object."}], step)
            except WorkflowError as exc:
                if exc.code not in {"invalid_tool_arguments", "invalid_tool_scope", "unknown_evidence", "invalid_evidence_kind", "finish_required", "incomplete_research"}:
                    raise
                observations.append({"error": exc.code, "message": exc.message,
                                     "tool": action.tool, "argument_schema": tools.get(action.tool)})
                self.store.event(self.run, "agent_correction", role, {
                    "code": exc.code, "step": step, "company_scope": company,
                    "operation_sha256": digest(action.tool)})
        self.store.event(self.run, "agent_step_limit", role, {"company_scope": company, "steps": self.config["max_agent_steps"]})
        raise WorkflowError("agent_step_limit", role+" ("+(company or "approved scope")+") did not complete within its bounded ReAct steps.")
