"""Matched memory ON/OFF evaluation adapter, never a publication route.

Uses the production Agents.context_by_role hook before prompt cache identity,
size checks and token reservation. The same production tools, role scopes and
critic remain active. Memory is a frozen prior-note snapshot, not current gold.
"""
import copy
from pathlib import Path
from workflow_agents import Agents
from workflow_contracts import ResearchRequest, digest, canonical
from workflow_store import RunStore
from workflow_tools import ToolBroker
from research_workflow import Workflow
from evaluation.final.adapters import (NoPublicationBackend, SingleWorkerBroker,
    ConfiguredModel, fixed_plan, _fixed_collection, _single_worker,
    _team_collection, _deterministic_validation)

async def execute_memory_variant(request, variant, workflow_config, model, backend, folder, run_id, *, lesson_context=None, memory_context=None, memory_guard=None):
    """Execute one fresh arm. No case gold or expectations are accepted here."""
    request = ResearchRequest.model_validate(request).model_dump()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    store = RunStore(folder / "journal.sqlite3")
    store.start(run_id, {**workflow_config, "request": request, "evaluation_variant": variant})
    safe_backend = NoPublicationBackend(backend)
    broker_type = SingleWorkerBroker if variant["architecture"] == "single_agent" else ToolBroker
    broker = broker_type(safe_backend, store, run_id, request)
    workflow_config = copy.deepcopy(workflow_config)
    if lesson_context is not None:
        for role, prompt in workflow_config["prompts"].items():
            if role == "publisher":
                continue
            lessons = lesson_context(role)
            if lessons:
                prompt["constraints"] += "\nFrozen reviewed development guidance (not source evidence): " + canonical(lessons)
    agents = Agents(ConfiguredModel(model), broker, store, run_id, workflow_config)
    snapshot = copy.deepcopy(memory_context or {"enabled":False,"status":"Memory is prior context, not evidence or new facts.","preferences":{},"past_notes":[],"truncated":False})
    if memory_guard is not None:
        memory_guard(snapshot)
        agents.external_payload_guard = memory_guard
        broker.external_payload_guard = memory_guard
    agents.context_by_role = {role:{"memory":snapshot} for role in ("supervisor","risk_diff","writer")}
    store.event(run_id,"background_snapshot","supervisor",{"background_sha256":digest(snapshot),"memory_enabled":snapshot.get("enabled") is True})
    workflow = Workflow(agents, broker, workflow_config)
    state = {"request": request, "researchers": {}, "revisions": 0, "replans": 0,
             "evaluation_only": True, "human_reviewed": False, "publication": None,
             "plan": fixed_plan(request), "plan_approved": True}
    # Identical shared topic retrieval from the reviewed development correction.
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
    state["memory_context_sha256"] = digest(snapshot)
    state["memory_enabled"] = snapshot.get("enabled") is True
    return state


def make_memory_executor(contexts, guards=None):
    frozen = copy.deepcopy(contexts)
    def snapshot_for(request, enabled):
        key=digest(ResearchRequest.model_validate(request).model_dump())
        if key not in frozen:
            raise ValueError("Memory request was not included in the frozen context manifest")
        return frozen[key]["on" if enabled else "off"]
    async def execute(request, variant, config, model, backend, folder, run_id, *, lesson_context=None):
        if type(variant.get("memory")) is not bool or variant.get("lessons") is not False:
            raise ValueError("Memory variants need an explicit toggle and lessons disabled")
        context=snapshot_for(request,variant["memory"])
        guard=guards.get(digest(ResearchRequest.model_validate(request).model_dump())) if guards else None
        return await execute_memory_variant(request,variant,config,model,backend,folder,run_id,
            lesson_context=lesson_context,memory_context=context,memory_guard=guard)
    execute.context_sha256=digest(frozen)
    return execute
