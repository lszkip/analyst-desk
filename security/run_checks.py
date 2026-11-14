"""Offline security regressions in a disposable copy; never use runtime research data.

Scripted model responses exercise application enforcement, not model resistance
to prompt injection. The sandbox suite separately exercises real subprocess and
kernel controls. No dependency installation or network access is performed.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SOURCE_FILES = (
    "company_registry.py", "python_sandbox.py", "research_portal.py",
    "research_tools_client.py", "research_workflow.py", "review_portal_note.py",
    "run_research_workflow.py", "sandbox_worker.py", "test_python_sandbox.py",
    "test_research_portal.py", "test_research_workflow.py", "test_workflow_research_prefetch.py",
    "test_workflow_critic_protocol.py", "test_workflow_financial_narrative.py", "tool_security.py",
    "workflow_agents.py", "workflow_contracts.py", "workflow_critic_protocol.py",
    "workflow_financial_narrative.py", "workflow_render.py", "workflow_store.py",
    "workflow_support.py", "workflow_tools.py", "companies.json", "workflow_config.json",
)
BASE_TEST_MODULES = ("test_research_workflow", "test_research_portal", "test_python_sandbox",
    "test_workflow_research_prefetch", "test_workflow_critic_protocol", "test_workflow_financial_narrative")
DEPENDENCIES = {
    "pydantic": "pydantic", "fastmcp": "fastmcp", "httpx": "httpx", "httpx2": "httpx2",
    "langgraph": "langgraph", "langgraph-checkpoint-sqlite": "langgraph.checkpoint.sqlite",
    "aiosqlite": "aiosqlite", "python-dotenv": "dotenv", "pandas": "pandas",
}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
CHECK_TIMEOUT_SECONDS = 240


@contextmanager
def termination_as_interrupt():
    previous = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_path(path):
    path = Path(path).absolute()
    return not any(part.is_symlink() for part in (path, *path.parents))


def read_source(root, name):
    path = root / name
    if not safe_path(path) or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("missing_or_unsafe_test_source")
    return path.read_bytes()


def inventory(root):
    tests = root / "security"
    if not safe_path(tests) or not tests.is_dir():
        raise ValueError("missing_security_tests")
    additions = sorted(str(path.relative_to(root)) for path in tests.glob("test_*.py"))
    if not 1 <= len(additions) <= 20 or any(not re.fullmatch(r"security/test_[a-zA-Z0-9_]+\.py", name) for name in additions):
        raise ValueError("invalid_security_test_inventory")
    names = [*SOURCE_FILES, *additions, "security/run_checks.py"]
    return {name: read_source(root, name) for name in names}


def clean_environment(private):
    home, temporary = private / "home", private / "tmp"
    home.mkdir(mode=0o700, exist_ok=True)
    temporary.mkdir(mode=0o700, exist_ok=True)
    return {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "HOME": str(home), "TMPDIR": str(temporary), "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0", "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
            "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}


def new_json(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    path = Path(path).absolute()
    if not safe_path(path) or not path.parent.is_dir():
        raise ValueError("unsafe_report_path")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)


def install_guards(private):
    """Guard this known test process; this is not an adversarial Python sandbox."""
    private = private.resolve()
    denied = Counter()
    runtime_roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
                     Path("/usr/lib"), Path("/usr/local/lib"), Path("/lib"), Path("/usr/share/zoneinfo")]
    system_files = {Path("/etc/localtime"), Path("/etc/mime.types"), Path("/dev/null"),
                    Path("/proc/self/maps"), Path("/proc/meminfo"), Path("/proc/cpuinfo")}
    def within(path, root):
        return path == root or root in path.parents
    def deny(category):
        denied[category] += 1
        raise PermissionError("security_test_guard_" + category)
    def audit(event, args):
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname",
                     "socket.gethostbyaddr", "socket.sendto"}:
            deny("network")
        if event == "subprocess.Popen":
            executable, arguments = args[0], args[1]
            expected = [sys.executable, "-I", "-B", str(private / "sandbox_worker.py")]
            if executable != sys.executable or list(arguments) != expected:
                deny("subprocess")
        if event == "sqlite3.connect":
            raw = str(args[0])
            if raw != ":memory:" and (raw.startswith("file:") or not within(Path(raw).resolve(), private)):
                deny("database_path")
        if event != "open" or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        original = Path(os.fsdecode(args[0]))
        path = original.resolve()
        if any(item.name == ".env" or item.name.startswith(".env.") for item in (original, path)):
            deny("environment_file")
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        mode = args[1] if len(args) > 1 and isinstance(args[1], str) else ""
        writes = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)) or any(letter in mode for letter in "wax+")
        if within(path, private):
            return
        if writes:
            deny("outside_write")
        if path in system_files or any(within(path, root) for root in runtime_roots):
            return
        deny("outside_read")
    sys.addaudithook(audit)
    return denied


class SafeResults(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.rows = {}
        self.subtest_counts = Counter()
    def startTest(self, test):
        super().startTest(test)
        name = test.id()
        if not re.fullmatch(r"[A-Za-z0-9_.]{1,240}", name):
            raise ValueError("unsafe_test_identifier")
        self.rows[name] = {"id": name, "status": "running"}
    def addSuccess(self, test):
        self.rows[test.id()]["status"] = "passed"
    def fail(self, test, error, status):
        self.rows[test.id()].update(status=status, exception_type=error[0].__name__ if re.fullmatch(r"[A-Za-z0-9_]{1,80}", error[0].__name__) else "Exception")
    def addFailure(self, test, error):
        self.fail(test, error, "failed")
        self.failures.append((test, "redacted"))
    def addError(self, test, error):
        self.fail(test, error, "error")
        self.errors.append((test, "redacted"))
    def addSkip(self, test, reason):
        self.rows[test.id()]["status"] = "skipped"
        self.skipped.append((test, "redacted"))
    def addExpectedFailure(self, test, error):
        self.rows[test.id()]["status"] = "expected_failure"
        self.expectedFailures.append((test, "redacted"))
    def addUnexpectedSuccess(self, test):
        self.rows[test.id()]["status"] = "unexpected_success"
        self.unexpectedSuccesses.append(test)
    def addSubTest(self, test, subtest, error):
        self.subtest_counts["executed"] += 1
        self.subtest_counts["passed" if error is None else "failed"] += 1
        if error is not None:
            self.fail(test, error, "failed" if issubclass(error[0], test.failureException) else "error")
            self.failures.append((test, "redacted_subtest"))


def worker(root, report_path):
    root = Path(root).absolute()
    report = {"overall_passed": False, "started_utc": now(), "tests": [], "counts": {}, "dependencies": {}}
    guards = install_guards(root)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "security"))
    try:
        for distribution, module in DEPENDENCIES.items():
            try:
                importlib.import_module(module)
                version = importlib.metadata.version(distribution)
                report["dependencies"][distribution] = version if re.fullmatch(r"[A-Za-z0-9.+_-]{1,60}", version) else "installed"
            except Exception:
                report["dependencies"][distribution] = "unavailable"
        if "unavailable" in report["dependencies"].values():
            report["failure_category"] = "dependencies_unavailable"
        else:
            loader = unittest.TestLoader()
            modules = list(BASE_TEST_MODULES) + [path.stem for path in sorted((root / "security").glob("test_*.py"))]
            suite = unittest.TestSuite(loader.loadTestsFromName(name) for name in modules)
            expected = suite.countTestCases()
            results = SafeResults()
            suite.run(results)
            report["tests"] = sorted(results.rows.values(), key=lambda item: item["id"])
            counts = Counter(row["status"] for row in report["tests"])
            report["counts"] = {"discovered": expected, "executed": results.testsRun, "passed": counts["passed"],
                "failed": counts["failed"], "errors": counts["error"], "skipped": counts["skipped"],
                "expected_failures": counts["expected_failure"], "unexpected_successes": counts["unexpected_success"]}
            report["subtest_counts"] = {key: results.subtest_counts[key] for key in ("executed", "passed", "failed")}
            covered = {name: sum(row["id"].startswith(name + ".") for row in report["tests"]) for name in modules}
            report["module_test_counts"] = covered
            report["overall_passed"] = (expected > 0 and results.testsRun == expected == len(results.rows)
                and counts["passed"] == expected and all(covered.values()) and not loader.errors)
            if not report["overall_passed"]:
                report["failure_category"] = "security_regressions_failed"
    except BaseException as exc:
        report["failure_category"] = "test_process_interrupted" if isinstance(exc, KeyboardInterrupt) else "test_process_error"
        report["exception_type"] = type(exc).__name__ if re.fullmatch(r"[A-Za-z0-9_]{1,80}", type(exc).__name__) else "Exception"
    report["guard_denials"] = dict(guards)
    report["finished_utc"] = now()
    new_json(report_path, report)
    return 0 if report["overall_passed"] else 1


def run(root):
    report = {"schema_version": 1, "scope": "Deterministic security boundaries with scripted models and synthetic state; actual sandbox kernel tests.",
        "not_measured": ["real_model_prompt_injection_resistance", "deployed_http_authorization", "complete_red_team_coverage"],
        "external_model_calls": 0, "network_access_permitted": False, "original_runtime_data_read": False,
        "started_utc": now(), "overall_passed": False}
    try:
        sources = inventory(root)
        before = {name: sha(raw) for name, raw in sources.items()}
        report["source_sha256"] = before
        with tempfile.TemporaryDirectory(prefix="analyst-desk-security-") as temporary:
            private = Path(temporary)
            os.chmod(private, 0o700)
            for name, raw in sources.items():
                destination = private / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
            worker_report = private / "worker_report.json"
            command = [sys.executable, "-I", "-B", str(private / "security/run_checks.py"), "--_worker", str(private), str(worker_report)]
            with tempfile.TemporaryFile() as capture:
                child = subprocess.Popen(command, cwd=private, env=clean_environment(private), stdin=subprocess.DEVNULL,
                    stdout=capture, stderr=subprocess.STDOUT, start_new_session=True)
                started = time.monotonic()
                category = None
                try:
                    while child.poll() is None:
                        if time.monotonic() - started > CHECK_TIMEOUT_SECONDS:
                            category = "test_timeout"; break
                        if capture.tell() > MAX_OUTPUT_BYTES:
                            category = "test_output_limit"; break
                        time.sleep(.1)
                finally:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGTERM)
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL); child.wait(timeout=5)
                capture.seek(0)
                raw_output = capture.read(MAX_OUTPUT_BYTES)
                report["worker"] = {"exit_code": child.returncode, "seconds": round(time.monotonic() - started, 3),
                    "output_bytes": len(raw_output), "output_sha256": sha(raw_output)}
            if category:
                report["failure_category"] = category
            elif worker_report.is_file() and worker_report.stat().st_size <= MAX_OUTPUT_BYTES:
                inner = json.loads(worker_report.read_text())
                report.update({key: inner[key] for key in ("tests", "counts", "subtest_counts", "dependencies", "module_test_counts", "guard_denials", "failure_category", "exception_type") if key in inner})
                report["overall_passed"] = child.returncode == 0 and inner.get("overall_passed") is True
            else:
                report["failure_category"] = "test_report_unavailable"
        report["source_unchanged"] = all(sha(read_source(root, name)) == digest for name, digest in before.items())
        if not report["source_unchanged"]:
            report["overall_passed"] = False
            report["failure_category"] = "test_source_changed"
    except BaseException as exc:
        report["overall_passed"] = False
        report["failure_category"] = "test_interrupted" if isinstance(exc, KeyboardInterrupt) else "test_preflight_error"
        report["exception_type"] = type(exc).__name__ if re.fullmatch(r"[A-Za-z0-9_]{1,80}", type(exc).__name__) else "Exception"
    report["finished_utc"] = now()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--_worker", nargs=2, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._worker:
        return worker(*args._worker)
    if args.report and (not args.report.is_absolute() or not safe_path(args.report) or not args.report.parent.is_dir() or os.path.lexists(args.report)):
        parser.error("--report requires a new absolute file path in an existing safe directory")
    report = run(Path(__file__).absolute().parents[1])
    if args.report:
        new_json(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0 if report["overall_passed"] else 1


if __name__ == "__main__":
    with termination_as_interrupt():
        raise SystemExit(main())
