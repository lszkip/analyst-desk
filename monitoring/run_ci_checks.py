"""Portable saved-run monitoring checks; no research, compose changes or AI calls."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid


PROMETHEUS_IMAGE = "prom/prometheus:v3.13.5"
PROMTOOL_TMPFS = "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777"
PUBLIC_RULE_TEST_NAMES = (
    "97 percent fires and recovery clears", "98 percent does not fire",
    "100 percent does not fire", "zero denominator is not a percentage",
    "missing evidence is unavailable",
)
MAX_OUTPUT_BYTES = 1024 * 1024
MIN_EXECUTED_TESTS = 13
MAX_ARCHIVE_SKIPS = 2
LOCAL_LABEL = "org.analyst-desk.monitoring-ci"
SAFE_ENV_KEYS = {"PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR",
                 "USERPROFILE", "XDG_CONFIG_HOME", "DOCKER_CONFIG", "DOCKER_CERT_PATH",
                 "DOCKER_TLS_VERIFY", "DOCKER_API_VERSION"}


def safe_environment(include_selection=False):
    keys = SAFE_ENV_KEYS | ({"DOCKER_HOST", "DOCKER_CONTEXT"} if include_selection else set())
    env = {key: value for key, value in os.environ.items() if key in keys}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0", HF_HUB_OFFLINE="1")
    # The CI path always uses the committed sanitized fixture, even if a local
    # operator has set the optional private-archive variable for other tests.
    return env


def terminate_process(process, force=False):
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        elif force:
            process.kill()
        else:
            process.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def capture_command(arguments, cwd, timeout=60, env=None, max_output=MAX_OUTPUT_BYTES):
    """Bound memory and elapsed time; raw output stays inside this process."""
    started = time.monotonic()
    record = {"exit_code": None, "timed_out": False, "output_limit_exceeded": False}
    output = bytearray()
    seen = 0
    output_hash = hashlib.sha256()
    overflow = threading.Event()
    try:
        process = subprocess.Popen(arguments, cwd=cwd, env=env or safe_environment(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=os.name == "posix")
    except OSError:
        record.update(status="unavailable", seconds=round(time.monotonic()-started, 3),
                      output_bytes=0, output_sha256=output_hash.hexdigest())
        return record, ""

    def read_output():
        nonlocal seen
        try:
            while True:
                chunk = process.stdout.read1(65536)
                if not chunk:
                    break
                seen += len(chunk)
                output_hash.update(chunk)
                if len(output) < max_output:
                    output.extend(chunk[:max_output-len(output)])
                if seen > max_output:
                    overflow.set()
                    terminate_process(process)
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        record["timed_out"] = True
        terminate_process(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            terminate_process(process, force=True)
            process.wait(timeout=5)
    except BaseException:
        terminate_process(process, force=True)
        process.wait(timeout=5)
        raise
    finally:
        reader.join(timeout=2)
        if reader.is_alive():
            terminate_process(process, force=True)
            process.stdout.close()
            reader.join(timeout=2)
    record.update(exit_code=process.returncode, output_limit_exceeded=overflow.is_set(),
                  seconds=round(time.monotonic()-started, 3), output_bytes=seen,
                  output_sha256=output_hash.hexdigest())
    record["status"] = ("passed" if process.returncode == 0 and not record["timed_out"]
                        and not record["output_limit_exceeded"] else "failed")
    return record, output.decode("utf-8", errors="replace")


def unittest_counts(output):
    runs = re.findall(r"^Ran (\d+) tests? in [0-9.]+s$", output, re.MULTILINE)
    skips = re.findall(r"^OK \(skipped=(\d+)\)$", output, re.MULTILINE)
    if len(runs) != 1 or len(skips) > 1:
        raise ValueError("A unique unittest result summary is required.")
    discovered = int(runs[0])
    skipped = int(skips[0]) if skips else 0
    executed = discovered - skipped
    if executed < MIN_EXECUTED_TESTS or skipped > MAX_ARCHIVE_SKIPS:
        raise ValueError("Too few executed tests or unexpected skipped tests.")
    return {"discovered": discovered, "executed": executed, "skipped": skipped,
            "maximum_documented_archive_skips": MAX_ARCHIVE_SKIPS}


def unittest_failure_ids(output):
    pattern = r"^(FAIL|ERROR): ([A-Za-z_][A-Za-z0-9_]{0,99})(?: \(([A-Za-z_][A-Za-z0-9_.]{0,199})\))?$"
    return [{"kind": kind, "test": qualified or name}
            for kind, name, qualified in re.findall(pattern, output, re.MULTILINE)[:20]]


def validate_endpoint(endpoint):
    if not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 1024 or any(ord(c) < 32 for c in endpoint):
        raise ValueError("Unsupported Docker endpoint.")
    parsed = urlsplit(endpoint)
    if parsed.query or parsed.fragment or parsed.username is not None or parsed.password is not None:
        raise ValueError("Unsupported Docker endpoint.")
    allowed = (parsed.scheme == "unix" and not parsed.netloc and parsed.path.startswith("/")
               or parsed.scheme == "npipe" and not parsed.netloc and parsed.path.startswith("//./pipe/"))
    allowed |= (parsed.scheme == "tcp" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                and parsed.path in {"", "/"})
    if not allowed:
        raise ValueError("A local Docker engine is required.")
    return parsed.scheme


def resolve_local_docker(root):
    context = os.environ.get("DOCKER_CONTEXT")
    endpoint = os.environ.get("DOCKER_HOST") if not context else None
    selection = "DOCKER_CONTEXT" if context else "DOCKER_HOST" if endpoint else "current_context"
    if context and not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", context):
        raise ValueError("Unsupported Docker context name.")
    if not endpoint:
        args = ["docker", "context", "inspect"] + ([context] if context else [])
        args += ["--format", "{{.Endpoints.docker.Host}}"]
        record, text = capture_command(args, root, timeout=15,
                                       env=safe_environment(include_selection=True), max_output=16384)
        if record["status"] != "passed":
            raise ValueError("The local Docker context could not be inspected.")
        endpoint = text.strip()
    transport = validate_endpoint(endpoint)
    # Pin the verified endpoint for every daemon operation. A later context
    # switch cannot redirect this invocation to a different engine.
    return ["docker", "--host", endpoint], {"local_verified": True, "transport": transport,
                                           "selection": selection}


def safe_report_file(path):
    if path is None:
        return None
    if not path.is_absolute() or not path.parent.is_dir():
        raise ValueError("Report requires an absolute path in an existing directory.")
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise ValueError("Report paths cannot contain symbolic links.")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8")


def cleanup_owned_container(prefix, cidfile, nonce, root):
    """A failed name collision has no cidfile and can never remove that name."""
    if not cidfile.is_file() or cidfile.is_symlink() or cidfile.stat().st_size > 100:
        return {"attempted": False, "reason": "no_owned_container_id"}
    identifier = cidfile.read_text().strip()
    if not re.fullmatch(r"[a-f0-9]{64}", identifier):
        return {"attempted": False, "reason": "invalid_container_id"}
    fmt = '{{.Id}}|{{index .Config.Labels "' + LOCAL_LABEL + '"}}'
    result, text = capture_command(prefix + ["inspect", "--format", fmt, identifier], root, timeout=15, max_output=4096)
    if result["status"] != "passed" or text.strip() != identifier + "|" + nonce:
        return {"attempted": False, "reason": "absent_or_not_owned"}
    result, _ = capture_command(prefix + ["rm", "--force", identifier], root, timeout=20, max_output=4096)
    return {"attempted": True, "status": result["status"]}


def promtool_failure_diagnostics(output, record):
    """Classify bounded output without returning paths, raw lines or secrets."""
    text = output[:MAX_OUTPUT_BYTES].lower()
    categories = []
    signatures = (
        ("read_only_filesystem", ("read-only file system", "read only file system")),
        ("temporary_storage", ("test_storage", "mkdirtemp", "temporary directory", "temp dir", "tempdir")),
        ("permission_denied", ("permission denied", "operation not permitted")),
        ("missing_file", ("no such file or directory", "file does not exist")),
        ("resource_limit", ("cannot allocate memory", "out of memory", "no space left on device",
                            "resource temporarily unavailable", "too many open files")),
    )
    for category, phrases in signatures:
        if any(phrase in text for phrase in phrases):
            categories.append(category)
    if (record.get("timed_out") or record.get("output_limit_exceeded")) and "resource_limit" not in categories:
        categories.append("resource_limit")
    if ("exp:" in text and "got:" in text) or ("expected:" in text and "actual:" in text):
        categories.append("rule_mismatch")
    return {"categories": categories or ["unknown_failure"],
            "rule_tests_mentioned": [name for name in PUBLIC_RULE_TEST_NAMES if name in text]}


def promtool_checks(root, prefix):
    folder = root / "prometheus"
    files = [folder / name for name in ("prometheus.yml", "alerts.yml", "alert_tests.yml")]
    if any(not path.is_file() or any(p.is_symlink() for p in [path, *path.parents]) or "," in str(path) for path in files):
        raise ValueError("Prometheus configuration must consist of regular local files.")
    checks = [("promtool_config", ["check", "config", "/etc/prometheus/prometheus.yml"]),
              ("promtool_rules", ["check", "rules", "/etc/prometheus/alerts.yml"]),
              ("promtool_rule_tests", ["test", "rules", "/etc/prometheus/alert_tests.yml"])]
    records = []
    with tempfile.TemporaryDirectory(prefix="analyst-desk-monitoring-ci-") as temporary:
        for index, (name, arguments) in enumerate(checks):
            nonce = uuid.uuid4().hex
            cidfile = Path(temporary) / (nonce + ".cid")
            container_name = "analyst-desk-monitoring-ci-" + nonce
            command = prefix + ["run", "--rm", "--name", container_name, "--cidfile", str(cidfile),
                "--label", LOCAL_LABEL + "=" + nonce, "--network", "none", "--read-only",
                "--tmpfs", PROMTOOL_TMPFS, "--env", "TMPDIR=/tmp",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--pids-limit", "64",
                "--memory", "256m", "--cpus", "1", "--entrypoint", "/bin/promtool"]
            for path in files:
                command += ["--mount", "type=bind,source=" + str(path) + ",target=/etc/prometheus/" + path.name + ",readonly"]
            command += [PROMETHEUS_IMAGE, *arguments]
            result = None
            try:
                result, output = capture_command(command, root, timeout=180 if index == 0 else 60)
                result.update(name=name, container_network="none", source_mounts="read_only", image=PROMETHEUS_IMAGE,
                              container_tmpfs=PROMTOOL_TMPFS, container_tmpdir="/tmp")
                if result["status"] != "passed":
                    result["failure_diagnostics"] = promtool_failure_diagnostics(output, result)
                records.append(result)
            finally:
                if result is None or result["status"] != "passed":
                    cleanup = cleanup_owned_container(prefix, cidfile, nonce, root)
                    if result is not None:
                        result["owned_container_cleanup"] = cleanup
            if result["status"] != "passed":
                break
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Run portable Python tests without Docker.")
    parser.add_argument("--report", type=Path, help="Write a new safe JSON report at this absolute path.")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    report = {"schema_version": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
              "mode": "offline" if args.offline else "python_and_promtool", "overall_passed": False,
              "scope": "Saved-run monitoring software/configuration checks; not a live alert or research evaluation.",
              "new_model_calls": 0, "running_compose_stack_changed": False, "steps": [],
              "github_actions_status": "not_determined_by_this_local_report"}
    handle = None
    exit_code = 1
    previous_sigterm = None
    termination_requested = False

    def request_termination(signum, frame):
        nonlocal termination_requested
        if not termination_requested:
            termination_requested = True
            raise KeyboardInterrupt

    if threading.current_thread() is threading.main_thread():
        previous_sigterm = signal.signal(signal.SIGTERM, request_termination)
    try:
        handle = safe_report_file(args.report)
        result, output = capture_command([sys.executable, "-m", "unittest", "discover", "-s", str(root / "tests"), "-v"], root.parent, timeout=120)
        result["name"] = "python_unittest"
        report["steps"].append(result)
        if result["status"] != "passed":
            result["failed_tests"] = unittest_failure_ids(output)
            raise RuntimeError("Python checks did not pass.")
        try:
            result["tests"] = unittest_counts(output)
        except ValueError:
            result.update(status="failed", failure_category="insufficient_test_coverage")
            raise
        if args.offline:
            report["docker_checks"] = "not_requested"
        else:
            prefix, report["docker_endpoint"] = resolve_local_docker(root)
            report["steps"].extend(promtool_checks(root, prefix))
            if len(report["steps"]) != 4 or any(step["status"] != "passed" for step in report["steps"]):
                raise RuntimeError("Prometheus checks did not pass.")
        report["overall_passed"] = True
        exit_code = 0
    except (ValueError, OSError) as exc:
        report["error"] = {"category": "validation_or_environment", "type": type(exc).__name__}
        exit_code = 2
    except KeyboardInterrupt:
        report["error"] = {"category": "interrupted", "type": "KeyboardInterrupt"}
    except Exception as exc:
        report["error"] = {"category": "checks_failed", "type": type(exc).__name__}
    finally:
        try:
            report["finished_utc"] = datetime.now(timezone.utc).isoformat()
            text = json.dumps(report, indent=2, sort_keys=True) + "\n"
            if handle:
                with handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
            print(text, end="")
        finally:
            if previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
