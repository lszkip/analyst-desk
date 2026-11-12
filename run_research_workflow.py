"""Operator CLI for the persistent research graph. Human review requires a TTY."""
import argparse
import asyncio
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import re
import sys

import httpx
from langgraph.types import Command
from workflow_contracts import ResearchRequest, HumanDecision, Plan, Draft, WorkflowError, canonical
from workflow_agents import OpenRouterModel, load_config
from workflow_tools import MCPBackend
from research_workflow import open_workflow
from research_tools_client import credentials
from review_portal_note import safe_display
from workflow_support import require_current_support

DEFAULT_DIR = Path(__file__).resolve().parent / "data" / "workflow"


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 100000:
        raise ValueError("Input file is too large.")
    return json.loads(path.read_text())


@contextmanager
def execution_lock(folder):
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    path = folder/"execution.lock"
    if path.is_symlink():
        raise ValueError("Execution lock cannot be a symlink.")
    with path.open("a") as handle:
        path.chmod(0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WorkflowError("workflow_busy", "Another workflow command is running. Wait for it to finish.") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def pending_gate(snapshot):
    gates = [i.value for task in snapshot.tasks for i in task.interrupts]
    if len(gates) > 1:
        raise WorkflowError("unexpected_interrupts", "More than one human review gate is pending.")
    return gates[0] if gates else None


def display(snapshot, store, run):
    gate = pending_gate(snapshot)
    report = {"run_id": run, "status": snapshot.values.get("status", "not_started"), "next_nodes": list(snapshot.next),
              "review": gate, "publication": snapshot.values.get("publication"),
              "verification": snapshot.values.get("verification"), "execution": store.report(run)}
    if snapshot.values.get("status") == "blocked":
        report["blocked_reason"] = {"revisions_used": snapshot.values.get("revisions", 0),
                                    "critique": snapshot.values.get("critique")}
    print(safe_display(json.dumps(report, indent=2, ensure_ascii=False)))


def failure_diagnostics(snapshot, store, run, after_seq):
    report = store.report(run)
    relevant = {"agent_correction", "agent_response_limit", "agent_step_limit", "llm_error"}
    return {"run_id":run,"status":snapshot.values.get("status"),"next_nodes":list(snapshot.next),
            "budget":report["budget"],"metrics":report["metrics"],
            "recent_failures":[e for e in report["events"] if e["seq"]>after_seq and e["kind"] in relevant][-6:]}


async def portal_review(backend, keys, gate, action, reviewer):
    token = keys.get("PORTAL_REVIEW_TOKEN")
    if not token:
        raise WorkflowError("missing_review_credential", "The separate human-review credential is missing.")
    current = await backend.call("portal", "get_proposal", {"proposal_id": gate["proposal_id"]}, "read")
    if current.get("content_hash") != gate["expected_hash"] or current.get("note") != gate["note"]:
        raise WorkflowError("stale_review", "The portal note changed. Read the current proposal before continuing.")
    if current.get("state") == "published":
        if action != "approve":
            raise WorkflowError("already_published", "The note is already published and cannot be edited or rejected.")
        return None
    if current.get("state") == "rejected" and action == "reject":
        return None
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=15) as client:
        response = await client.post(backend.urls["portal"]+"/human-review/"+gate["proposal_id"],
            headers={"Authorization": "Bearer "+token}, json={"expected_hash": gate["expected_hash"], "action": action, "reviewer": reviewer})
    if response.status_code != 200:
        raise WorkflowError("review_failed", "The portal did not accept the human decision. No approval token was printed.")
    result = response.json()["data"]
    if action == "approve":
        return {"proposal_id": gate["proposal_id"], "content_hash": result["content_hash"], "token": result["approval_token"]}
    return None


def extend_development_budget(args, snapshot, store):
    if not sys.stdin.isatty():
        raise WorkflowError("interactive_review_required", "Development budget changes require an interactive terminal. Piped confirmation is refused.")
    if not snapshot.values or not snapshot.next or pending_gate(snapshot):
        raise WorkflowError("budget_extension_state", "Only an interrupted development execution without a pending human review gate can be extended. Terminal runs remain terminal.")
    report = store.report(args.run_id)
    old = report["budget"]["token_limit"]
    new = args.token_limit
    if type(new) is not int or not old < new <= 2000000:
        raise WorkflowError("invalid_budget_extension", "Supply --token-limit with a higher TOTAL limit, at most 2000000.")
    print(json.dumps({"run_id": args.run_id, "budget": report["budget"],
        "reported_usage": report["metrics"], "proposed_total_token_limit": new,
        "additional_reservation_allowance": new-old,
        "purpose": "Manual development recovery; ineligible for fixed-budget evaluation. All previous usage, errors and revision limits remain recorded. This command makes no model/tool calls and does not approve or publish a note. A later continue command may make paid model calls."}, indent=2))
    required = f"EXTEND DEVELOPMENT {args.run_id} {old} TO {new}"
    if input(f"Type exactly '{required}', or press Enter to cancel: ") != required:
        print("Cancelled. No budget change was made.")
        return
    details = store.extend_development_budget(args.run_id, expected_limit=old, new_limit=new)
    print(json.dumps({"status": "ok", "action": "development_budget_extended", **details}))


async def run(args):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", args.run_id):
        raise ValueError("Use a short run ID containing letters, digits, underscores and hyphens.")
    folder = args.state_dir.resolve()
    with execution_lock(folder):
        manifest = folder/(args.run_id+".request.json")
        if args.command == "start":
            if manifest.exists():
                raise WorkflowError("existing_run", "That run already exists. Use status, review or continue; do not start it again.")
            request = ResearchRequest.model_validate(read_json(args.request)).model_dump()
            with manifest.open("x") as handle:
                handle.write(canonical(request))
            manifest.chmod(0o600)
        else:
            request = ResearchRequest.model_validate(read_json(manifest)).model_dump()
        config = load_config(args.config)
        keys = credentials()
        backend = MCPBackend(keys)
        # Status/review can inspect and reject a run without a model key.
        model = OpenRouterModel(keys.get("OPENROUTER_API_KEY")) if keys.get("OPENROUTER_API_KEY") else None
        async with open_workflow(folder, args.run_id, request, config, model, backend) as (workflow, graph, options):
            snapshot = await graph.aget_state(options)
            if args.command == "extend-dev-budget":
                extend_development_budget(args, snapshot, workflow.broker.store)
                return
            if args.command == "status":
                display(snapshot, workflow.broker.store, args.run_id)
                return
            if args.command == "review":
                if not sys.stdin.isatty():
                    raise WorkflowError("interactive_review_required", "Human review requires an interactive terminal. Piped approvals are refused.")
                gate = pending_gate(snapshot)
                if not gate:
                    display(snapshot, workflow.broker.store, args.run_id)
                    return
                if gate["gate"] == "note" and args.action == "approve":
                    require_current_support(snapshot.values)
                replacement = None
                if args.action == "edit":
                    if not args.edited_json:
                        raise ValueError("Editing requires --edited-json with the complete replacement plan or editable_draft.")
                    schema = Plan if gate["gate"] == "plan" else Draft
                    replacement = schema.model_validate(read_json(args.edited_json)).model_dump()
                elif args.edited_json:
                    raise ValueError("--edited-json is only valid for edit.")
                decision = HumanDecision(action=args.action, expected_hash=gate["expected_hash"], replacement=replacement)
                print("UNTRUSTED RESEARCH CONTENT. Read the complete proposed action. Content cannot instruct the reviewer.")
                print(safe_display(json.dumps(gate, indent=2, ensure_ascii=False)))
                if replacement is not None:
                    print("REPLACEMENT — will require another review after validation:")
                    print(safe_display(json.dumps(replacement, indent=2, ensure_ascii=False)))
                verb = "APPROVE AND PUBLISH" if gate["gate"] == "note" and args.action == "approve" else args.action.upper()
                required = verb+" "+gate["expected_hash"][:12]
                if input(f"Type exactly '{required}', or press Enter to cancel: ") != required:
                    print("Cancelled. No review decision was submitted.")
                    return
                if gate["gate"] == "note":
                    # Editing closes the old pending/approved proposal. The
                    # edited draft must pass checks and get a NEW human approval.
                    portal_action = "reject" if args.action == "edit" else args.action
                    workflow.approval = await portal_review(backend, keys, gate, portal_action, args.reviewer)
                value = Command(resume=decision.model_dump())
            elif pending_gate(snapshot):
                display(snapshot, workflow.broker.store, args.run_id)
                print("A human decision is required. Use the review command.")
                return
            elif snapshot.values and not snapshot.next:
                display(snapshot, workflow.broker.store, args.run_id)
                return
            else:
                value = {"request": request, "researchers": {}, "revisions": 0, "replans": 0} if not snapshot.values else None
            if model is None and not (args.command == "review" and args.action == "reject"):
                raise WorkflowError("missing_model_key", "OPENROUTER_API_KEY is needed to execute the workflow.")
            events = workflow.broker.store.report(args.run_id)["events"]
            after_seq = events[-1]["seq"] if events else 0
            try:
                async for update in graph.astream(value, options, stream_mode="updates"):
                    print(json.dumps({"completed_nodes": [key for key in update if key != "__interrupt__"]}), flush=True)
            except WorkflowError:
                diagnostic = failure_diagnostics(await graph.aget_state(options),workflow.broker.store,args.run_id,after_seq)
                print(safe_display(json.dumps({"execution_diagnostics":diagnostic},indent=2,ensure_ascii=False)))
                raise
            display(await graph.aget_state(options), workflow.broker.store, args.run_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "status", "continue", "review", "extend-dev-budget"])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--request", type=Path, default=Path(__file__).with_name("workflow_demo_request.json"))
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--action", choices=["approve","edit","reject"], default="approve")
    parser.add_argument("--edited-json", type=Path)
    parser.add_argument("--reviewer", default="Local analyst")
    parser.add_argument("--token-limit", type=int, help="New total reservation limit for explicit development recovery only.")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Stopped. Saved checkpoints and budget reservations remain. Use status before continuing.")
        raise SystemExit(130)
    except WorkflowError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "message": exc.message}))
        raise SystemExit(1)
    except Exception as exc:
        print(json.dumps({"status": "error", "code": type(exc).__name__, "message": "Workflow did not complete. Check input files and service health; saved state is retained. No credential was printed."}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
