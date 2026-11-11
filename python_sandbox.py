"""Bounded subprocess runner; no unsafe in-process fallback."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time

from sandbox_worker import CPU_SECONDS, MAX_CODE, MAX_INPUT, MAX_OUTPUT, MEMORY_BYTES, POLICY_VERSION, WALL_SECONDS, validate

SLOTS = threading.BoundedSemaphore(2)


def policy():
    return {"policy": POLICY_VERSION, "platform": "Linux x86_64", "network": False, "file_access": False,
            "subprocesses": False, "cpu_seconds": CPU_SECONDS, "wall_seconds": WALL_SECONDS,
            "memory_bytes": MEMORY_BYTES, "output_bytes": MAX_OUTPUT, "input_bytes": MAX_INPUT,
            "code_bytes": MAX_CODE, "concurrent_jobs": 2,
            "libraries": ["pandas", "math", "Decimal"], "result_contract": "Set result to a finite JSON value; Decimal outputs are strings."}


def error(code, message):
    return {"status": "error", "code": code, "message": message}


def execute(code, inputs):
    try:
        validate(code)
        encoded_input = json.dumps(inputs, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded_input) > MAX_INPUT:
            return error("input_limit", "Calculation inputs exceed 65536 bytes.")
        payload = json.dumps({"code": code, "inputs": inputs}, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return error("invalid_calculation", "Code or inputs are outside the supported computation policy.")
    if not SLOTS.acquire(blocking=False):
        return error("sandbox_busy", "Both computation slots are busy; retry later.")
    process = None
    start = time.monotonic()
    try:
        # Explicit environment allowlist. Never inherit API keys, proxy settings,
        # Python import paths, credentials, or the parent's open descriptors.
        env = {"LANG": "C.UTF-8", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
               "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}
        process = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).with_name("sandbox_worker.py"))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd="/tmp",
            close_fds=True, start_new_session=True)
        output, diagnostics = bytearray(), bytearray()
        sent = 0
        with selectors.DefaultSelector() as selector:
            for stream, event in [(process.stdin, selectors.EVENT_WRITE), (process.stdout, selectors.EVENT_READ), (process.stderr, selectors.EVENT_READ)]:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, event)
            while selector.get_map():
                remaining = WALL_SECONDS - (time.monotonic() - start)
                if remaining <= 0:
                    return error("time_limit", "Calculation exceeded its wall-clock limit.")
                for key, _ in selector.select(min(remaining, .1)):
                    stream = key.fileobj
                    if stream is process.stdin:
                        try:
                            sent += os.write(stream.fileno(), payload[sent:sent+4096])
                        except BrokenPipeError:
                            sent = len(payload)
                        if sent == len(payload):
                            selector.unregister(stream); stream.close()
                    else:
                        data = os.read(stream.fileno(), 4096)
                        if not data:
                            selector.unregister(stream); stream.close()
                        else:
                            (output if stream is process.stdout else diagnostics).extend(data)
                            if len(output) + len(diagnostics) > MAX_OUTPUT:
                                return error("output_limit", "Calculation exceeded its output limit.")
        process.wait(timeout=max(.01, WALL_SECONDS - (time.monotonic() - start)))
        if process.returncode != 0:
            return error("resource_limit", "Calculation was terminated or could not start safely.")
        reply = json.loads(output)
        reply["elapsed_ms"] = round((time.monotonic() - start) * 1000)
        return reply
    except Exception:
        return error("sandbox_unavailable", "The isolated computation process is unavailable.")
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
        SLOTS.release()
