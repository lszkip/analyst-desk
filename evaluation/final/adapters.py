"""Read-only, scoped final-evaluation adapters; no publication capabilities.

C0 is fixed code collection with at most one tool-free LLM writer response.
C1 has one ReAct worker with the union of team read/calculation tools. C2 runs
production Workflow methods, including parallel company researchers. Completion
C0/C1 disable the separate model critic; historical configurations may retain it.
All retain deterministic guards and never cross publication approval gates.
"""
import asyncio
import copy
import json
import re
from pathlib import Path

from workflow_agents import Agents
from workflow_contracts import Draft, Handoff, ResearchRequest, WorkflowError, canonical, digest
from workflow_financial_narrative import financial_paragraphs, financial_narrative_issues
from workflow_render import draft_prose_issues, render_note, selected_evidence
from workflow_store import RunStore
from workflow_support import support_issues
from workflow_tools import ToolBroker
from research_workflow import RiskDraft, Workflow


class NoPublicationBackend:
    """Wrap an actual operator backend; publication cannot occur in an assay."""
    def __init__(self, backend):
        self.backend = backend
        self.blocked_publication_calls = 0

    async def call(self, service, tool, arguments, access=None):
        if service == "portal" or tool in {"prepare_note", "review_note", "publish_note"}:
            self.blocked_publication_calls += 1
            raise WorkflowError("evaluation_publication_forbidden", "Evaluation stops before the publication boundary.")
        return await self.backend.call(service, tool, arguments, access)


class FixedWorkflowBroker(ToolBroker):
    """Code may collect evidence; the fixed writer cannot choose any tool."""
    def tool_specs(self, role, company=None):
        return {} if role == "writer" else super().tool_specs(role, company)

    async def agent_call(self, role, name, args, company=None):
        if role == "writer":
            raise WorkflowError("evaluation_fixed_writer_tool_denied", "The fixed workflow writer has no tool-dispatch capability.")
        return await super().agent_call(role, name, args, company)


class FixedWorkflowAgents(Agents):
    """One schema-checked writer response; no corrective model loop or tools."""
    async def ask(self, role, task, output_type, company=None, **kwargs):
        if role != "writer":
            return await super().ask(role, task, output_type, company, **kwargs)
        task = copy.deepcopy(task)
        task["assignment"] = ("Return the qualitative risk contribution now using only the supplied fixed source samples and registered IDs. "
            "No additional tool call or model revision is available. Preserve uncertainty and source scope. "
            "Do not repeat financial answers: code appends that section independently.")
        if isinstance(task.get("evidence"), dict):
            task["evidence"]["instruction"] = "Fixed original source samples only; no tool access. Cite supported risk statements and preserve sample limits."
            for sample in task["evidence"].get("source_samples", []):
                sample.pop("read_full_text_with", None)
        prompt = copy.deepcopy(kwargs.get("system_prompt") or self.config["prompts"][role])
        prompt["constraints"] += " Fixed workflow: return one finish handoff from supplied evidence. No tool selection, planning loop, critic, or rewrite is available."
        kwargs["system_prompt"] = prompt
        once = Agents(self.model, self.broker, self.store, self.run, {**self.config, "max_agent_steps": 1})
        once.context_by_role = self.context_by_role
        once.external_payload_guard = self.external_payload_guard
        return await once.ask(role, task, output_type, company, **kwargs)


class SingleWorkerBroker(ToolBroker):
    """Union of existing read tools, each dispatched through its original role."""
    def tool_specs(self, role, company=None):
        result = super().tool_specs(role, company)
        if role == "researcher" and company is None:
            result = {**result, **super().tool_specs("quant")}
        return result

    async def agent_call(self, role, name, args, company=None):
        if role == "researcher" and company is None and name == "collect_financials":
            return await super().agent_call("quant", name, args)
        return await super().agent_call(role, name, args, company)


class ConfiguredModel:
    """Adds frozen, verified lessons and attests production critic schema only.

    lesson_context(role) must be backed by LessonStore.context(..., manifest=...).
    It receives no gold and is never allowed to learn from evaluation outcomes.
    """
    def __init__(self, model, *, lesson_context=None):
        self.model, self.lesson_context = model, lesson_context

    async def complete(self, messages, config):
        messages = copy.deepcopy(messages)
        if self.lesson_context is not None:
            prompt = json.loads(messages[0]["content"])
            lessons = self.lesson_context(prompt["role"])
            if lessons:
                prompt["constraints"] += "\nFrozen development lessons (procedural guidance, not source evidence):\n" + canonical(lessons)
                messages[0]["content"] = canonical(prompt)
        config = dict(config)
        response_format = config.get("_response_format")
        if response_format and response_format.get("type") == "json_schema":
            config["_assay_response_format_sha256"] = digest(response_format)
        return await self.model.complete(messages, config)


def fixed_plan(request):
    return {"objective": "Answer the approved operating-margin request using verified source evidence.",
            "outline": ["Verified financial comparison", "Requested sampled risks and limits"],
            "research_query": "operating performance and requested risk factors",
            "limitations": ["Source samples do not establish complete filing coverage."]}


async def _fixed_collection(workflow, state):
    researchers = {}
    for ticker in state["request"]["tickers"]:
        await workflow.broker.research_prefetch(ticker)
        records = workflow.broker.store.evidence(workflow.broker.run)
        ids = [key for key, row in records.items() if row["kind"] == "passage" and row["ticker"] == ticker]
        researchers[ticker] = {"summary": "Fixed collection; source meaning is reviewed downstream.", "evidence_ids": ids[:80], "issues": []}
    financial = await workflow.broker.financials()
    state["researchers"] = researchers
    state["quant"] = {"summary": "Code collected and checked financial facts.", "evidence_ids": [r[k] for r in financial["rows"] for k in ("revenue_id", "income_id", "margin_id")], "issues": financial["issues"]}
    state["risk_diff"] = {"summary": "Fixed collection; no separate model risk synthesis.", "evidence_ids": [key for h in researchers.values() for key in h["evidence_ids"]][:80], "issues": []}
    return state


async def _single_worker(workflow, state):
    # Collection is selected by this one worker; no researcher/quant/risk/writer
    # models are secretly run before or after it. Numeric prose stays code-owned.
    prompt = {**workflow.config["prompts"]["researcher"],
        "task": "Act as the sole research and writing worker. Plan your collection, read the requested company/year Item 7 and requested Item 1A, call collect_financials, and return the qualitative risk contribution in final_schema. Use only supplied tools and registered evidence. Code appends all financial answers.",
        "constraints": workflow.config["prompts"]["researcher"]["constraints"] + " Risk title and paragraphs must have NO digits or number words. Do not restate operating margins, their level, their direction or a financial comparison in this contribution, even in words; code supplies that complete section. Address the original requested risk topics only; introductory or unrelated risks are not substitutes. Use registered ticker/year metadata for period scope; do not infer the period from incidental dates in passage text. Preserve source qualifiers; cite each company and period discussed. Do not claim filing-wide risk absence or changes from samples. A calculation receipt supports arithmetic, not business causation. If risks were not requested, return a short evidence-grounded context paragraph; only the deterministic financial section will be retained."}
    def validate(final):
        records = workflow.broker.store.evidence(workflow.broker.run)
        issues = []
        if not any(r["kind"] == "fact" for r in records.values()):
            issues.append("Call collect_financials before finishing; no verified financial facts have been collected.")
        missing = workflow.broker.evidence_overview("researcher")["missing_slots"]
        if missing:
            issues.append("Read the required company/year sections before finishing: " + canonical(missing))
        for paragraph in final["paragraphs"]:
            if set(paragraph["evidence_ids"]) - records.keys():
                issues.append("Use only registered evidence IDs.")
        return issues
    risk = await workflow.agents.ask("researcher", {"assignment": "Complete collection and the risk contribution in one worker conversation. The requested numeric answer is appended by code after your handoff.", "plan_background": state["plan"], "previous_risk_draft": state.get("risk_draft"), "critic_feedback": state.get("critique")}, RiskDraft,
        require_sections=True, validate_final=validate, system_prompt=prompt)
    records = workflow.broker.store.evidence(workflow.broker.run)
    financial = financial_paragraphs(records, state["request"])
    use_risks = state["request"]["include_risks"]
    draft = Draft.model_validate({"title": risk["title"] if use_risks else "Company operating margin comparison",
        "paragraphs": [*(risk["paragraphs"] if use_risks else []), *financial]}).model_dump()
    state.update(draft=draft, risk_draft=risk if use_risks else {}, writer_mode="single_worker_with_deterministic_financials", status="checking")
    return state


async def _team_collection(workflow, state):
    for attempt in range(2):
        state.update(await workflow.planner(state))
        state.update(plan_approved=True, status="researching")
        workflow.broker.store.event(workflow.broker.run, "evaluation_plan_approval_simulated", "evaluation", {"human_reviewed": False, "plan_hash": state["plan_hash"]})
        branches = await asyncio.gather(*(workflow.researcher({**state, "company": ticker}) for ticker in state["request"]["tickers"]))
        state["researchers"] = {ticker: handoff for branch in branches for ticker, handoff in branch["researchers"].items()}
        state.update(workflow.join(state))
        if state["status"] != "replanning":
            break
    state.update(await workflow.quant(state))
    state.update(await workflow.risk(state))
    return state


async def _deterministic_validation(workflow, state):
    records = workflow.broker.store.evidence(workflow.broker.run)
    issues = draft_prose_issues(state["draft"])
    issues.extend(financial_narrative_issues(state["draft"], records, state["request"]))
    issues.extend(support_issues(state["draft"], records, state["request"]))
    if issues:
        state.update(status="blocked", deterministic_issues=list(dict.fromkeys(issues)), semantic_critic="disabled")
        return state
    note, verification = render_note(state["draft"], records, state["request"])
    await workflow.broker.reverify(selected_evidence(state["draft"], records))
    state.update(note=note.model_dump(), verification=verification, status="completed", semantic_critic="disabled", semantic_support_verified=False)
    return state


async def execute_variant(request, variant, workflow_config, model, backend, folder, run_id, *, lesson_context=None):
    """Execute one fresh arm. No case gold or expectations are accepted here."""
    request = ResearchRequest.model_validate(request).model_dump()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    store = RunStore(folder / "journal.sqlite3")
    store.start(run_id, {**workflow_config, "request": request, "evaluation_variant": variant})
    safe_backend = NoPublicationBackend(backend)
    broker_type = {"single_agent": SingleWorkerBroker, "fixed_workflow": FixedWorkflowBroker}.get(variant["architecture"], ToolBroker)
    broker = broker_type(safe_backend, store, run_id, request)
    workflow_config = copy.deepcopy(workflow_config)
    if lesson_context is not None:
        for role, prompt in workflow_config["prompts"].items():
            if role == "publisher":
                continue
            lessons = lesson_context(role)
            if lessons:
                prompt["constraints"] += "\nFrozen reviewed development guidance (not source evidence): " + canonical(lessons)
    agent_type = FixedWorkflowAgents if variant["architecture"] == "fixed_workflow" else Agents
    agents = agent_type(ConfiguredModel(model), broker, store, run_id, workflow_config)
    workflow = Workflow(agents, broker, workflow_config)
    state = {"request": request, "researchers": {}, "revisions": 0, "replans": 0,
             "evaluation_only": True, "human_reviewed": False, "publication": None,
             "plan": fixed_plan(request), "plan_approved": True}
    # Same bounded question-derived topical retrieval and tool accounting for
    # every arm; this is shared preprocessing, not an agentic decision.
    for ticker in request["tickers"]:
        await broker.request_topic_prefetch(ticker)
    architecture = variant["architecture"]
    if architecture == "fixed_workflow":
        await _fixed_collection(workflow, state)
    elif architecture == "single_agent":
        await _single_worker(workflow, state)
    elif architecture == "team":
        state.pop("plan")
        state["plan_approved"] = False
        await _team_collection(workflow, state)
    else:
        raise ValueError("Unsupported architecture")
    if architecture != "single_agent":
        state.update(await workflow.writer(state))
    if not variant.get("critic", True):
        return await _deterministic_validation(workflow, state)
    for revision in range(workflow_config["max_revisions"] + 1):
        state.update(await workflow.critic(state))
        if state["status"] == "preparing_note":
            state.update(status="completed", semantic_critic="shared_production_validator", semantic_support_verified=None, semantic_support="model_critic_approved_not_human_gold")
            break
        if state["status"] == "blocked":
            break
        if architecture == "single_agent":
            await _single_worker(workflow, state)
        else:
            state.update(await workflow.writer(state))
    state["publication_calls"] = safe_backend.blocked_publication_calls
    return state


class LexicalWindows:
    """Fixed whitespace windows for a controlled BM25 representation ablation."""
    settings = {"prefix_tokens": 90}
    def windows(self, text, size=180, overlap=40):
        spans = list(re.finditer(r"\S+", text))
        if not spans:
            return []
        output = []
        for start in range(0, len(spans), max(1, size-overlap)):
            end = min(len(spans), start+size)
            a, b = spans[start].start(), spans[end-1].end()
            output.append((text[a:b], a, b))
            if end == len(spans):
                break
        return output


class LocalCorpusBackend:
    """Actual verified local SEC corpus, production facts and isolated arithmetic.

    Search uses fixed BM25 over production search_units. This is a controlled
    representation experiment, explicitly not production hybrid retrieval.
    """
    def __init__(self, catalogue, data_dir, *, plain_text=False):
        from retrieval_data import search_units
        from rank_bm25 import BM25Okapi
        self.catalogue, self.data_dir, self.plain_text = catalogue, Path(data_dir), plain_text
        self.units = search_units(catalogue, LexicalWindows(), plain_text=plain_text)
        self.bm25 = BM25Okapi([self.tokens(unit["text"]) for unit in self.units])
        self.protocol = {"ranking": "fixed_bm25", "representation": "plain_text" if plain_text else "table_aware",
                         "windowing": "whitespace_180_overlap40", "corpus_sha256": catalogue.manifest_sha}

    @staticmethod
    def tokens(text):
        return re.findall(r"[a-z0-9]+", text.lower())

    async def call(self, service, tool, arguments, access=None):
        self.catalogue.check_live()
        if service == "portal":
            raise WorkflowError("evaluation_publication_forbidden", "No publication in evaluation.")
        if (service, tool) == ("edgar", "get_annual_financial_fact"):
            from financial_data import get_annual_financial_fact
            return await asyncio.to_thread(get_annual_financial_fact, **arguments, data_dir=self.data_dir)
        if (service, tool) == ("sandbox", "execute_python"):
            from python_sandbox import execute
            return await asyncio.to_thread(execute, arguments["code"], arguments["inputs"])
        if tool == "get_section" and service == "edgar":
            result = self.catalogue.section(arguments["ticker"], arguments["year"], arguments["item"], arguments["offset"], arguments["limit"])
            return {**result, "status": "ok", "total_passages": result["total"]}
        if tool == "get_passage" and service == "kb":
            return {"passage": copy.deepcopy(self.catalogue.get(arguments["passage_id"]))}
        if tool == "get_table_rows" and service == "kb":
            p = self.catalogue.get(arguments["passage_id"])
            if p["kind"] != "table":
                raise WorkflowError("invalid_evidence_kind", "Requested passage is not a table.")
            rows, offset, limit = p["table"]["rows"], arguments["offset"], arguments["limit"]
            return {"passage_id": p["passage_id"], "source_sha256": p["source_sha256"], "rows": copy.deepcopy(rows[offset:offset+limit]), "total_rows": len(rows), "next_offset": offset+limit if offset+limit < len(rows) else None}
        if tool == "search_filings" and service == "kb":
            request = arguments["request"]
            scores = self.bm25.get_scores(self.tokens(request["query"]))
            candidates = [(float(scores[i]), unit) for i, unit in enumerate(self.units)
                if unit["ticker"] in request["tickers"] and unit["fiscal_year"] in request["years"]
                and (not request.get("items") or unit["item"] in request["items"])
                and (request.get("kind", "all") == "all" or unit["kind"] == request["kind"])]
            candidates.sort(key=lambda pair: (-pair[0], pair[1]["unit_id"]))
            hits, seen = [], set()
            for score, unit in candidates:
                if unit["passage_id"] in seen:
                    continue
                seen.add(unit["passage_id"])
                hits.append({k: unit[k] for k in ("passage_id", "ticker", "fiscal_year", "item", "kind", "source_sha256")})
                hits[-1].update(score=score, excerpt=unit["text"], cell_ids=unit["cell_ids"])
                if len(hits) >= request.get("limit", 2):
                    break
            return {"hits": hits, "protocol": self.protocol, "untrusted_source": True}
        raise WorkflowError("evaluation_tool_denied", "Backend operation is outside the evaluation allowlist.")
