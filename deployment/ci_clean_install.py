"""Fresh GitHub-hosted runner installation check; synthetic data, no paid research.

This intentionally refuses an existing installation and is not a repair tool.
Run from the checked-out repository: python -m deployment.ci_clean_install
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
REPORT_NAME = "Clean_Install_Report.json"
SERVICES = {"api", "edgar", "qdrant", "filings-kb", "python-sandbox", "research-portal",
            "retrieval-gateway", "exporter", "prometheus", "grafana", "monitoring-gateway", "research-app"}
RUN_ID = "ci_unexecuted_stream_fixture"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def project_name(environ):
    run, attempt = environ.get("GITHUB_RUN_ID", ""), environ.get("GITHUB_RUN_ATTEMPT", "")
    require(bool(re.fullmatch(r"[0-9]{1,20}", run)) and bool(re.fullmatch(r"[0-9]{1,10}", attempt)),
            "A numeric GitHub run ID and attempt are required.")
    return f"analyst-ci-{run}-{attempt}"


def fresh_runner(root, environ):
    require(environ.get("GITHUB_ACTIONS") == "true" and environ.get("CI") == "true"
            and environ.get("ANALYST_CLEAN_RUNNER") == "github-hosted",
            "This installation check only runs on a fresh GitHub-hosted CI runner.")
    require(root.resolve() == Path(environ.get("GITHUB_WORKSPACE", "/nonexistent")).resolve(),
            "The source must be the current GitHub checkout.")
    require((root / ".git").is_dir(), "A fresh Git checkout is required.")
    require(not any(p.is_symlink() for p in (root, *root.parents)), "Symlink checkout is unsupported.")
    for relative in (".env", "data", "monitoring/data", "monitoring/secrets", REPORT_NAME,
                     "deployment/compose.ci.generated.json", "deployment/.ci-owner.json"):
        require(not (root / relative).exists() and not (root / relative).is_symlink(),
                "The checkout already contains installation state: " + relative)
    return project_name(environ)


def compose_override(project):
    require(bool(re.fullmatch(r"analyst-ci-[0-9]{1,20}-[0-9]{1,10}", project)), "Unsafe CI project.")
    # The production stack deliberately shares historic monitoring volumes.
    # CI MUST override both explicit names before it can create or delete them.
    return {"volumes": {name: {"name": project + "-" + name}
                        for name in ("prometheus-data", "grafana-data")}}


def compose_base(root, project):
    compose_override(project)  # Validate before constructing any Docker command.
    return ["docker", "compose", "--project-directory", str(root), "--project-name", project,
            "--env-file", str(root / ".env"), "-f", str(root / "compose.final.yaml"),
            "-f", str(root / "deployment/compose.ci.generated.json")]


def write_new(path, value, mode=0o600):
    raw = value if isinstance(value, str) else json.dumps(value, indent=2) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(raw)


def prepare_fixture(root):
    """Use production parsing with invented bytes; no SEC download or model stub."""
    from unittest.mock import patch
    import filing_corpus as corpus
    import filing_passages as passages
    from retrieval_data import PassageCatalogue
    from test_api import write_fixture
    from test_filing_passages import BODY, report
    raw = root / "data/raw"
    raw.mkdir(parents=True, exist_ok=False)
    for ticker in ("MSFT", "GOOGL"):
        write_fixture(raw, ticker)
    plan = corpus.plan_corpus(["MSFT", "GOOGL"], [2024, 2025], data_dir=raw)
    body = BODY.replace("Supply constraints may reduce revenue.",
                        "Cyber attacks threaten cloud operations. Supply constraints may reduce revenue.")
    pages = {document["source_url"]: report(document, body) for document in plan["documents"]}
    with patch("filing_corpus.fetch_html", side_effect=lambda url, ua: pages[url]):
        corpus.prepare_corpus(["MSFT", "GOOGL"], [2024, 2025], "CI fixture tests@example.invalid",
                              data_dir=raw, corpus_dir=root / "data/filings")
    passages.prepare_passages(["MSFT", "GOOGL"], [2024, 2025], data_dir=raw,
                              corpus_dir=root / "data/filings", passages_dir=root / "data/passages")
    catalogue = PassageCatalogue(root / "data/passages", root / "data/filings")
    catalogue.replay(raw)
    # The exporter requires its existing validated saved snapshot. Replaying this
    # public test fixture is NOT a new run, publication or measurement.
    saved = root / "monitoring/tests/fixtures/google_meta_saved_snapshot.json"
    from monitoring.exporter import validate_snapshot
    validate_snapshot(json.loads(saved.read_text()))
    (root / "monitoring/data").mkdir(mode=0o755, parents=True, exist_ok=False)
    write_new(root / "monitoring/data/snapshot.json", saved.read_text(), mode=0o644)
    return {"kind": "invented_filings_and_financial_facts", "company_years": 4,
            "tickers": ["MSFT", "GOOGL"], "years": [2024, 2025],
            "corpus_replay_verified": True, "monitoring": "existing_public_saved_snapshot_fixture",
            "monitoring_fixture_sha256": hashlib.sha256(saved.read_bytes()).hexdigest()}


def seed_idle_stream(root):
    """Seed only saved request/config files, never invoke or approve a workflow."""
    from workflow_contracts import ResearchRequest
    from workflow_lessons import LESSON_TAGS, LessonStore
    from workflow_agents import load_config
    folder = root / "data/workflow_final"
    request = ResearchRequest(question="Synthetic CI fixture: compare operating margins.",
                              tickers=["MSFT", "GOOGL"], years=[2024, 2025], include_risks=False)
    store = LessonStore(folder / "lessons.sqlite3", "local-analyst")
    write_new(folder / (RUN_ID + ".request.json"), request.model_dump())
    write_new(folder / (RUN_ID + ".config.json"), load_config())
    write_new(folder / (RUN_ID + ".lessons.json"), store.freeze(sorted(LESSON_TAGS)))


def verify_container_states(raw):
    # Compose versions emit either a JSON array or newline-delimited objects.
    try:
        parsed = json.loads(raw)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    by_service = {row["Service"]: row for row in rows}
    require(set(by_service) == SERVICES and len(rows) == len(SERVICES), "Incomplete Compose service set.")
    for name, row in by_service.items():
        require(row["State"] == "running", "A service is not running: " + name)
        if name != "qdrant":
            require(row.get("Health") == "healthy", "A service is not healthy: " + name)
    return {name: {"state": row["State"], "health": row.get("Health", "")}
            for name, row in sorted(by_service.items())}


def wait_qdrant(values):
    """Gateway liveness is not upstream readiness; wait before writing the index."""
    import httpx
    deadline = time.monotonic() + 60
    with httpx.Client(trust_env=False, timeout=5) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get("http://127.0.0.1:6333/collections",
                                      headers={"api-key": values["QDRANT_API_KEY"]})
                if response.status_code == 200 and response.json().get("status") == "ok":
                    return {"ready": True, "authenticated_collections_read": True}
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(1)
    raise ValueError("Qdrant did not become ready for the initial index build.")


def verify_ui(root):
    import httpx
    token = (root / "data/research_app/access_token").read_text().strip()
    with httpx.Client(base_url="http://127.0.0.1:8080", trust_env=False, timeout=30) as client:
        require(client.get("/").status_code == 200, "UI page failed.")
        require(client.get("/api/runs").status_code == 401, "UI authentication failed.")
        headers = {"Authorization": "Bearer " + token}
        result = client.get("/api/runs", headers=headers)
        require(result.status_code == 200 and result.json()["runs"] == [{"run_id": RUN_ID, "active": False}],
                "Unexpected UI run state.")
        result = client.get(f"/api/runs/{RUN_ID}/events", headers=headers)
        require(result.status_code == 200 and result.headers.get("content-type", "").startswith("text/event-stream"),
                "SSE response failed.")
        require("event: status\n" in result.text and "event: idle\n" in result.text, "SSE events missing.")
        payload = next(line[6:] for line in result.text.splitlines() if line.startswith("data: "))
        state = json.loads(payload)
        require(state["status"] == "not_started" and not state["running"]
                and all(event["kind"] == "background_snapshot" for event in state["events"])
                and state["metrics"]["llm_requests_started"] == 0
                and state["budget"]["tool_calls"] == 0
                and not state["publication"], "The stream fixture executed unexpectedly.")
    return {"page": "passed", "unauthenticated_api": "blocked", "authenticated_api": "passed",
            "sse_status_and_idle": "passed", "workflow_executed": False,
            "recorded_workflow_llm_requests": 0, "recorded_workflow_tool_calls": 0}


def cleanup(root, project, command):
    marker = json.loads((root / "deployment/.ci-owner.json").read_text())
    require(marker == {"project": project, "root": str(root)}, "CI ownership marker mismatch.")
    override = json.loads((root / "deployment/compose.ci.generated.json").read_text())
    require(override == compose_override(project), "CI volume override changed; refusing cleanup.")
    # Never prune Docker, target production's project, or delete host directories.
    command([*compose_base(root, project), "down", "--volumes", "--remove-orphans"], timeout=120)


def main():
    from start_analyst_desk import CHECKS, command as run_command, probe, setup
    started = time.monotonic()
    record = {"schema_version": "clean-install-v1", "status": "not_started",
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "fresh GitHub-hosted runner; production stack with synthetic source fixtures",
              "performance_claim": False, "paid_research_started": False,
              "external_llm_calls_requested": 0, "authorized_publication_requested": False,
              "commit_sha": os.environ.get("GITHUB_SHA", "unknown"),
              "run_url": "https://github.com/" + os.environ.get("GITHUB_REPOSITORY", "unknown")
                         + "/actions/runs/" + os.environ.get("GITHUB_RUN_ID", "unknown"),
              "commands": []}
    values, project, armed = {}, None, False
    def command(argv, timeout=1200):
        record["phase"] = " ".join(argv)
        print("CI phase: " + " ".join(argv[:3]), flush=True)
        return run_command(argv, ROOT, values, record, timeout=timeout)
    result = 1
    try:
        project = fresh_runner(ROOT, os.environ)
        record["project"] = project
        record["phase"] = "prepare_synthetic_source_and_monitoring_fixtures"
        record["fixture"] = prepare_fixture(ROOT)
        write_new(ROOT / ".env", "OPENROUTER_API_KEY=ci-invalid-" + secrets.token_hex(24) + "\n")
        values = setup(ROOT)
        # Additional local secrets are not stored in .env; redact them too.
        values["CI_UI_TOKEN"] = (ROOT / "data/research_app/access_token").read_text().strip()
        values["CI_GRAFANA_TOKEN"] = (ROOT / "monitoring/secrets/grafana_admin_password").read_text().strip()
        for value in values.values():
            if value and len(str(value)) >= 16:
                print("::add-mask::" + str(value), flush=True)
        write_new(ROOT / "deployment/compose.ci.generated.json", compose_override(project))
        write_new(ROOT / "deployment/.ci-owner.json", {"project": project, "root": str(ROOT)})
        armed = True
        seed_idle_stream(ROOT)
        base = compose_base(ROOT, project)
        command([*base, "config", "--quiet"], timeout=30)
        command([sys.executable, "prepare_retrieval_models.py"], timeout=900)
        # Only the Qdrant access gateway and EDGAR are needed to build the index.
        command([*base, "up", "-d", "--build", "--wait", "--wait-timeout", "240",
                 "qdrant", "edgar", "retrieval-gateway"], timeout=900)
        record["phase"] = "wait_for_authenticated_qdrant_readiness"
        record["qdrant_bootstrap"] = wait_qdrant(values)
        command([sys.executable, "prepare_filing_index.py"], timeout=300)
        command([sys.executable, "prepare_filing_index.py", "--verify-only"], timeout=180)
        command([*base, "up", "-d", "--build", "--wait", "--wait-timeout", "360"], timeout=1200)
        record["containers"] = verify_container_states(command([*base, "ps", "--all", "--format", "json"], timeout=30))
        record["health"] = {name: probe(url) for name, url in CHECKS.items()}
        require(all(value["healthy"] for value in record["health"].values()), "A host health/readiness check failed.")
        command([sys.executable, "check_edgar_mcp.py", "--ticker", "MSFT", "--year", "2025"], timeout=90)
        command([sys.executable, "check_filings_kb.py", "--ticker", "MSFT", "--year", "2025"], timeout=180)
        command([sys.executable, "check_research_tools.py"], timeout=180)
        record["negative_publish_tests"] = "passed; unapproved and read-only requests rejected"
        record["protocol_checks"] = {"edgar_fact": "passed", "kb_five_tools_and_readonly_key": "passed",
                                     "sandbox_and_unapproved_portal_publish_block": "passed"}
        record["ui"] = verify_ui(ROOT)
        record["phase"] = "all_installation_and_protocol_checks_passed"
        record["status"], result = "passed", 0
    except Exception as exc:
        record["status"] = "failed"
        record["error_category"] = type(exc).__name__
        record["failed_phase"] = record.get("phase", "fresh_runner_guard")
        print("Clean installation check failed; inspect the sanitized phase log.", flush=True)
        if armed:
            try:
                command([*compose_base(ROOT, project), "logs", "--no-color", "--tail", "60"], timeout=30)
            except Exception:
                record["diagnostics_failed"] = True
    finally:
        if armed:
            try:
                cleanup(ROOT, project, command)
                record["cleanup"] = "own_ci_project_and_unique_volumes_removed"
            except Exception as exc:
                record["cleanup"] = "failed_" + type(exc).__name__
                record["status"], result = "failed", 1
        record["seconds"] = round(time.monotonic() - started, 2)
        # If freshness validation failed, never overwrite an earlier report.
        try:
            write_new(ROOT / REPORT_NAME, record, mode=0o644)
        except FileExistsError:
            print("An earlier CI report exists; it was preserved.", flush=True)
        print(json.dumps({key: value for key, value in record.items() if key != "commands"}, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
