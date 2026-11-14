"""Interactive, isolated live demonstration of checkpoint and approval controls.

Uses the production LangGraph and portal. The planner response and note are
explicit synthetic fixtures. No provider call or production publication runs.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
RUN = "controlled_checkpoint_demo"
REQUEST = {"question": "Controlled software demo of an operating margins comparison plan for Microsoft and Alphabet in FY2025; no financial research will be performed.",
           "tickers": ["MSFT", "GOOGL"], "years": [2025], "include_risks": False}
PLAN = {"objective": "Show persistent plan review in a controlled software demonstration.",
        "outline": ["Review the saved plan", "Reject this demonstration before any research"],
        "research_query": "synthetic demonstration only",
        "limitations": ["Scripted planner response; no filing was retrieved and no financial conclusion is made."]}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_path(path):
    return not any(value.is_symlink() for value in (path, *path.parents))


def child_environment(extra=None):
    env = {key: value for key, value in os.environ.items() if key not in
           {"OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"}}
    env.update(PYTHONDONTWRITEBYTECODE="1", LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    env.update(extra or {})
    return env


def write_new(path, value):
    require(safe_path(path), "Symbolic links are not accepted for demo state.")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    path.chmod(0o600)


def validate_private(folder):
    folder = Path(folder).absolute()
    require(safe_path(folder) and folder.name == "private", "Invalid private demo folder.")
    marker = json.loads((folder / "Demo_Owner.json").read_text())
    require(marker == {"kind": "isolated_live_control_demo", "directory": str(folder)},
            "This folder is not owned by the isolated demonstration.")
    return folder


class ScriptedPlanner:
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, config):
        role = json.loads(messages[0]["content"])["role"]
        require(role == "supervisor" and self.calls == 0, "The demonstration permits one scripted planner response only.")
        self.calls += 1
        return {"content": json.dumps({"tool": "finish", "arguments": {}, "final": PLAN}),
                "usage": {"prompt_tokens": 80, "completion_tokens": 60, "total_tokens": 140, "cost": 0.0},
                "model": "scripted-demo-planner-not-a-provider"}


class NoExternalTools:
    async def call(self, *args, **kwargs):
        raise AssertionError("The checkpoint demonstration must not call any research tool.")


async def checkpoint_worker(folder, phase):
    from langgraph.types import Command
    from research_workflow import open_workflow
    from run_research_workflow import pending_gate
    folder = validate_private(folder)
    config = json.loads((folder / "Workflow_Config.json").read_text())
    state = folder / "workflow"
    if phase == "start":
        require(not state.exists(), "An earlier checkpoint cannot be replaced.")
    else:
        require((state / "checkpoints.sqlite3").is_file(), "The saved checkpoint is missing.")
    model = ScriptedPlanner()
    async with open_workflow(state, RUN, REQUEST, config, model, NoExternalTools()) as (workflow, graph, options):
        if phase == "start":
            await graph.ainvoke({"request": REQUEST, "researchers": {}, "revisions": 0, "replans": 0}, options)
        before = await graph.aget_state(options)
        gate = pending_gate(before)
        require(gate is not None and gate["gate"] == "plan" and gate["plan"] == PLAN,
                "The production graph did not retain the expected plan interrupt.")
        checkpoint_id = before.config["configurable"]["checkpoint_id"]
        prior = json.loads((folder / "Checkpoint_Start.json").read_text()) if phase == "resume" else None
        if prior:
            require(prior["checkpoint_id"] == checkpoint_id and prior["plan_hash"] == gate["expected_hash"],
                    "The reopened checkpoint differs from the stopped process.")
            # The interactive parent obtained a real REJECT decision before
            # launching this child. Reject cannot grant publication authority.
            await graph.ainvoke(Command(resume={"action": "reject", "expected_hash": gate["expected_hash"],
                                                "replacement": None}), options)
        after = await graph.aget_state(options)
        execution = workflow.broker.store.report(RUN)
        result = {"phase": phase, "process_id": os.getpid(), "checkpoint_id": checkpoint_id,
                  "plan_hash": gate["expected_hash"], "plan": gate["plan"],
                  "next_nodes_before": list(before.next), "status_after": after.values["status"],
                  "next_nodes_after": list(after.next), "scripted_responses_this_process": model.calls,
                  "external_provider_calls": 0, "tool_calls": execution["budget"]["tool_calls"],
                  "scripted_journal_requests_cumulative": execution["metrics"]["llm_requests_started"],
                  "usage_values_are_synthetic": True}
        require(result["tool_calls"] == 0, "Unexpected tool activity in checkpoint demo.")
        if phase == "resume":
            require(model.calls == 0 and after.values["status"] == "rejected" and not after.next,
                    "Resume did not finish through the production rejection branch.")
    result["checkpoint_file_sha256_after_process_close"] = hashlib.sha256((state / "checkpoints.sqlite3").read_bytes()).hexdigest()
    write_new(folder / ("Checkpoint_Start.json" if phase == "start" else "Checkpoint_Resume.json"), result)
    print(json.dumps(result))
    return 0


def checkpoint_process(folder, phase):
    result = subprocess.run([sys.executable, "-m", "deployment.live_control_demo", "--worker", phase,
                             "--private-dir", str(folder)], cwd=ROOT, capture_output=True, text=True,
                            timeout=60, env=child_environment())
    require(result.returncode == 0, "The isolated checkpoint child failed; no existing state was touched.")
    return json.loads((folder / ("Checkpoint_Start.json" if phase == "start" else "Checkpoint_Resume.json")).read_text())


def synthetic_note(edited=False):
    from research_portal import ResearchNote
    return ResearchNote(title="CONTROLLED DEMO — synthetic approval revision " + ("2" if edited else "1"),
        body_markdown="Synthetic software demonstration only. No real company financial claim. " +
                      ("This edited wording needs a new approval." if edited else "This original wording is shown for exact-content approval."),
        tickers=["FICT"], fiscal_years=[2025], citations=[{"evidence_id": "synthetic_demo_source",
        "source_sha256": hashlib.sha256(b"synthetic demonstration source, not an SEC filing").hexdigest(),
        "source_url": "https://www.sec.gov/Archives/edgar/data/0/synthetic-demo-not-a-filing.htm"}])


def ask_exact(word, text):
    require(sys.stdin.isatty(), "Live human decisions require an interactive terminal; piped input is refused.")
    print(text, flush=True)
    return input("Type " + word + " to continue, or press Enter to cancel: ").strip() == word


def start_portal(folder):
    keys = {name: secrets.token_urlsafe(36) for name in
            ("PORTAL_READ_TOKEN", "PORTAL_DRAFT_TOKEN", "PORTAL_PUBLISH_TOKEN", "PORTAL_REVIEW_TOKEN")}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    child = subprocess.Popen([sys.executable, str(ROOT / "research_portal_mcp.py"), "--host", "127.0.0.1",
                              "--port", str(port), "--db", str(folder / "portal.sqlite3")], cwd=ROOT,
                             env=child_environment(keys),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return child, "http://127.0.0.1:" + str(port), keys


async def portal_demo(folder, record):
    import httpx
    from research_portal import note_hash
    from research_tools_client import mcp_client, call
    process, url, keys = start_portal(folder)
    record["portal"] = {"scope": "new isolated loopback HTTP/MCP server and private database",
                        "process_id": process.pid, "completed": False, "phase": "startup"}
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=15) as http:
            for _ in range(100):
                require(process.poll() is None, "The isolated portal process stopped during startup.")
                try:
                    response = await http.get(url + "/health")
                    if response.status_code == 200 and response.json().get("service") == "research-portal":
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.1)
            else:
                raise ValueError("The isolated portal did not become ready.")
            note, edited = synthetic_note(), synthetic_note(True)
            record["portal"]["phase"] = "prepare_synthetic_proposal"
            async with mcp_client(url, keys["PORTAL_DRAFT_TOKEN"]) as client:
                proposal = await call(client, "prepare_note", {"note": note.model_dump(), "idempotency_key": "isolated_demo"})
            pid, old_hash = proposal["proposal_id"], proposal["content_hash"]
            require(old_hash == note_hash(note) and proposal["state"] == "pending", "Unexpected original proposal.")
            print("\nThis synthetic payload is the ONLY content being reviewed. It will not be published.")
            print(json.dumps(note.model_dump(), indent=2), flush=True)
            print("Original full SHA-256:", old_hash, flush=True)
            record["portal"]["phase"] = "await_operator_approval"
            require(sys.stdin.isatty(), "Interactive human review is required.")
            reviewer = input("Enter your own name or initials for this controlled review: ").strip()
            require(1 <= len(reviewer) <= 100 and all(ord(c) >= 32 for c in reviewer), "A real operator's name or initials are required.")
            if not ask_exact("APPROVE", "Approve only the displayed synthetic revision; this grants no authority for a later edit."):
                raise KeyboardInterrupt
            response = await http.post(url + "/human-review/" + pid,
                headers={"Authorization": "Bearer " + keys["PORTAL_REVIEW_TOKEN"]},
                json={"action": "approve", "expected_hash": old_hash, "reviewer": reviewer})
            require(response.status_code == 200, "The portal refused the operator approval.")
            approved = response.json()["data"]
            require(approved["state"] == "approved" and approved["content_hash"] == old_hash,
                    "The portal did not approve the displayed content.")
            capability = approved["approval_token"]  # Memory only; never printed or exported.
            record["portal"].update(proposal_id=pid, original_hash=old_hash, operator_approved=True,
                                    reviewer_identity="operator-entered; not exported or independently verified")
            print("Approval accepted for revision 1. The capability remains private.", flush=True)
            print("\nProposed replacement:")
            print(json.dumps(edited.model_dump(), indent=2), flush=True)
            print("Edited full SHA-256:", note_hash(edited), flush=True)
            record["portal"]["phase"] = "await_operator_edit"
            if not ask_exact("EDIT", "Apply this displayed edit and revoke the earlier approval. No new approval will be given."):
                raise KeyboardInterrupt
            require(approved["approval_expires"] > time.time() + 45,
                    "Approval is too close to expiry to attribute rejection specifically to the edit. Run a new demonstration.")
            response = await http.post(url + "/human-review/" + pid,
                headers={"Authorization": "Bearer " + keys["PORTAL_REVIEW_TOKEN"]},
                json={"action": "edit", "expected_hash": old_hash, "reviewer": reviewer, "edited_note": edited.model_dump()})
            require(response.status_code == 200, "The portal refused the operator edit.")
            changed = response.json()["data"]
            new_hash = changed["content_hash"]
            require(changed["state"] == "pending" and changed["revision"] == 2 and new_hash == note_hash(edited)
                    and new_hash != old_hash and "approval_token" not in changed, "The edit did not revoke approval as expected.")
            print("Revision 2 is pending. Testing the OLD capability with both old and new content hashes...", flush=True)
            record["portal"]["phase"] = "reject_stale_capabilities"
            attempts = []
            async with mcp_client(url, keys["PORTAL_PUBLISH_TOKEN"]) as client:
                for digest in (old_hash, new_hash):
                    require(time.time() < approved["approval_expires"], "Original approval expired before the revocation check.")
                    result = (await client.call_tool("publish_note", {"proposal_id": pid, "expected_hash": digest,
                                                                     "approval_token": capability})).structured_content
                    require(time.time() < approved["approval_expires"], "Original approval expired during the revocation check.")
                    attempts.append({"expected_hash": digest, "status": result.get("status"), "code": result.get("code"),
                                     "original_approval_unexpired": True})
                    require(result.get("status") == "error" and result.get("code") == "approval_required",
                            "A stale-approval request was not rejected; the isolated demo failed.")
            async with mcp_client(url, keys["PORTAL_READ_TOKEN"]) as client:
                final = await call(client, "get_proposal", {"proposal_id": pid})
                notes = await call(client, "list_notes")
            require(final["state"] == "pending" and notes["total"] == 0, "Unexpected publication in the isolated demo.")
            record["portal"].update(edited_hash=new_hash, revision=final["revision"], final_state=final["state"],
                                    stale_approval_attempts=attempts, published_notes=notes["total"],
                                    operator_edited=True, completed=True, phase="complete")
            exported = json.dumps(record)
            require(all(value not in exported for value in [*keys.values(), capability]), "A secret reached the report.")
            print("PASS: both stale-approval attempts were rejected; published notes = 0.", flush=True)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        record["portal"]["own_demo_server_stopped"] = process.poll() is not None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=["start", "resume"], help=argparse.SUPPRESS)
    parser.add_argument("--private-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        require(args.private_dir is not None, "An owned private folder is required.")
        return asyncio.run(checkpoint_worker(args.private_dir, args.worker))
    require(sys.stdin.isatty(), "Run this guided demo in your terminal. Piped approvals are refused.")
    from workflow_agents import load_config
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(4)
    output = ROOT / "LSOutput" / ("Live_Control_Demo_" + stamp)
    require(safe_path(output), "Unsafe output path.")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    folder = output / "private"
    folder.mkdir(mode=0o700)
    write_new(folder / "Demo_Owner.json", {"kind": "isolated_live_control_demo", "directory": str(folder)})
    write_new(folder / "Workflow_Config.json", load_config())
    record = {"schema_version": "live-control-demo-v1", "status": "started",
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "evidence_class": "interactive_controlled_software_demonstration",
              "financial_research_claim": False, "agent_performance_claim": False,
              "external_model_calls": 0, "production_state_accessed": False,
              "source_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                ("research_workflow.py", "research_portal.py", "research_portal_mcp.py", "workflow_agents.py",
                 "workflow_config.json", "deployment/live_control_demo.py")}}
    result = 1
    try:
        print("CONTROLLED LIVE DEMO: production control code, synthetic plan/note, no paid research.")
        print("All state is new and private. Existing containers, notes and workflow databases are untouched.", flush=True)
        record["phase"] = "checkpoint_start"
        first = checkpoint_process(folder, "start")
        record["checkpoint_start"] = first
        print("\nProduction LangGraph paused at plan_review. The first Python process has EXITED.")
        print("Stopped process:", first["process_id"], "Checkpoint:", first["checkpoint_id"])
        print("Plan full SHA-256:", first["plan_hash"])
        print(json.dumps(first["plan"], indent=2), flush=True)
        if not ask_exact("REJECT", "Resume this saved checkpoint in a NEW Python process and reject the synthetic plan before any research."):
            raise KeyboardInterrupt
        record["phase"] = "checkpoint_resume"
        second = checkpoint_process(folder, "resume")
        require(first["process_id"] != second["process_id"], "The demonstration did not use separate processes.")
        record["checkpoint_resume"] = second
        record["operator_rejected_plan"] = True
        print("PASS: new process", second["process_id"], "reopened the same checkpoint and resumed to", second["status_after"])
        print("No planner response was repeated during resume.", flush=True)
        record["phase"] = "portal_approval_invalidation"
        asyncio.run(portal_demo(folder, record))
        record["phase"] = "complete"
        record["status"], result = "passed", 0
    except (KeyboardInterrupt, EOFError):
        record["status"] = "cancelled_incomplete"
        print("Cancelled. No completion claim is made; the isolated evidence is preserved.")
    except Exception as exc:
        record["status"] = "failed"
        record["error_category"] = type(exc).__name__
        print("The controlled demonstration did not finish. No production state was changed.")
    finally:
        record["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report = output / "Live_Control_Demo_Report.json"
        write_new(report, record)
        archive = output / ("Live_Control_Demo_Results_" + stamp + ".zip")
        with ZipFile(archive, "x", compression=ZIP_DEFLATED) as bundle:
            bundle.write(report, report.name)
        print("\nResult:", record["status"])
        print("Upload only this results ZIP (it excludes private databases and credentials):")
        print(archive)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
