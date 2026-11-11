"""Isolated child bootstrap. Never import this file to run untrusted code in-process.

Linux x86_64 only. AST restrictions are usability/defence-in-depth controls;
kernel syscall filtering, bounded subprocess I/O and resource limits are mandatory.
"""
import ast
import json
import sys

POLICY_VERSION = "linux_x86_64_compute_v1"
MAX_CODE = 16000
MAX_INPUT = 65536
MAX_OUTPUT = 65536
MEMORY_BYTES = 768 * 1024 * 1024
CPU_SECONDS = 3
WALL_SECONDS = 10
SAFE_NAMES = {"abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "len", "list",
              "max", "min", "print", "range", "round", "sorted", "str", "sum", "tuple", "zip", "Decimal"}
SAFE_ATTRIBUTES = {"DataFrame", "Series", "concat", "to_numeric", "sum", "mean", "min", "max", "count",
                   "round", "abs", "clip", "fillna", "isna", "notna", "dropna", "sort_values", "reset_index",
                   "groupby", "to_dict", "tolist", "to_list", "copy", "head", "tail", "shape", "columns",
                   "iloc", "loc", "items", "keys", "values", "get", "append", "extend", "quantize",
                   "sqrt", "isfinite", "floor", "ceil", "log", "exp"}
SAFE_NODES = {ast.Module, ast.Assign, ast.AugAssign, ast.Expr, ast.If, ast.For, ast.While, ast.Break, ast.Continue,
              ast.Pass, ast.Name, ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set,
              ast.Subscript, ast.Slice, ast.Attribute, ast.Call, ast.keyword, ast.BinOp, ast.UnaryOp, ast.BoolOp,
              ast.Compare, ast.IfExp, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp, ast.comprehension,
              ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.Not,
              ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot}


def validate(code):
    if not isinstance(code, str) or not code.strip() or len(code.encode()) > MAX_CODE:
        raise ValueError("Code must contain 1–16000 UTF-8 bytes.")
    tree = ast.parse(code, mode="exec")
    nodes = list(ast.walk(tree))
    if len(nodes) > 3000:
        raise ValueError("Code exceeds the syntax-size limit.")
    for node in nodes:
        if type(node) not in SAFE_NODES:
            raise ValueError("Only computational expressions, assignments and bounded-resource loops are supported.")
        if isinstance(node, ast.Name):
            if node.id.startswith("_") or node.id in {"eval", "exec", "open", "globals", "locals", "getattr", "setattr", "vars", "type", "help", "input", "compile", "breakpoint"}:
                raise ValueError("Name is outside the computation policy.")
            if isinstance(node.ctx, ast.Store) and node.id in SAFE_NAMES | {"pd", "math", "inputs"}:
                raise ValueError("Reserved names cannot be rebound.")
        if isinstance(node, ast.Attribute):
            if node.attr not in SAFE_ATTRIBUTES or not isinstance(node.ctx, ast.Load):
                raise ValueError("Attribute is outside the computation policy.")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in SAFE_NAMES:
                    raise ValueError("Callable is outside the computation policy.")
            elif not isinstance(node.func, ast.Attribute):
                raise ValueError("Indirect calls are not supported.")
            if any(k.arg is None for k in node.keywords):
                raise ValueError("Expanded keyword arguments are not supported.")
    return compile(tree, "<calculation>", "exec")


def install_filter():
    import ctypes
    import errno
    import platform
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("Unsupported sandbox platform.")
    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]
    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]
    # Linux uapi asm/unistd_64.h. No open, network, exec, clone, ptrace,
    # io_uring, process_vm*, filesystem mutation or namespace operations.
    allowed = [0, 1, 3, 5, 8, 9, 10, 11, 12, 13, 14, 15, 24, 25, 28,
               39, 60, 98, 131, 186, 202, 228, 229, 231, 318]
    instructions = [(0x20, 0, 0, 4), (0x15, 1, 0, 0xC000003E), (0x06, 0, 0, 0x80000000), (0x20, 0, 0, 0)]
    for number in allowed:
        instructions += [(0x15, 0, 1, number), (0x06, 0, 0, 0x7FFF0000)]
    instructions += [(0x06, 0, 0, 0x00050000 | errno.EPERM)]
    array = (Filter * len(instructions))(*(Filter(*i) for i in instructions))
    program = Program(len(array), array)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:
        raise RuntimeError("Kernel isolation unavailable.")
    # Trusted probes bypass the AST. All must be blocked by the kernel.
    for number, args in [(41, (2, 1, 0)), (257, (-100, ctypes.c_char_p(b"/etc/passwd"), 0)),
                         (56, (0, 0, 0, 0, 0))]:
        ctypes.set_errno(0)
        if libc.syscall(number, *args) != -1 or ctypes.get_errno() != errno.EPERM:
            raise RuntimeError("Kernel isolation probe failed.")


def main():
    import builtins
    import io
    import math
    import os
    import resource
    from contextlib import redirect_stdout
    from decimal import Decimal
    class OutputLimit(Exception):
        pass
    class BoundedOutput(io.StringIO):
        total = 0
        def write(self, s):
            self.total += len(s.encode())
            if self.total > MAX_OUTPUT // 2:
                raise OutputLimit()
            return super().write(s)
    def default(value):
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError("Nonfinite result.")
            return str(value)
        raise TypeError("Result must be JSON; convert pandas objects with to_dict or tolist.")
    reply = {"status": "error", "code": "isolation_unavailable", "message": "Sandbox isolation could not be established."}
    try:
        # Limits apply during pandas bootstrap as well as execution.
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        raw = sys.stdin.buffer.read(MAX_INPUT + MAX_CODE + 4097)
        if len(raw) > MAX_INPUT + MAX_CODE + 4096:
            raise ValueError("Request too large.")
        payload = json.loads(raw)
        compiled = validate(payload["code"])
        import pandas as pd
        # Resolve the supported common lazy paths before denying all file opens.
        frame = pd.DataFrame({"a": [1, 2], "g": ["x", "x"]})
        frame.to_dict(orient="records"); frame.groupby("g")["a"].sum().to_dict()
        pd.to_numeric(pd.Series(["1", "2"]))
        # No inherited sockets/files, including ones opened by bootstrap imports.
        os.closerange(3, 32)
        install_filter()
        env = {"__builtins__": {n: getattr(builtins, n) for n in SAFE_NAMES - {"Decimal"}},
               "Decimal": Decimal, "pd": pd, "math": math, "inputs": payload["inputs"]}
        output = BoundedOutput()
        try:
            with redirect_stdout(output):
                exec(compiled, env, env)
            if "result" not in env:
                raise ValueError("Assign the calculation to result.")
            # Validate finite and JSON-serializable before sending any result.
            result = json.loads(json.dumps(env["result"], default=default, allow_nan=False))
            reply = {"status": "ok", "data": {"result": result, "stdout": output.getvalue(),
                     "policy": POLICY_VERSION, "kernel_probes": {"network_denied": True, "file_open_denied": True, "process_creation_denied": True}}}
        except OutputLimit:
            reply = {"status": "error", "code": "output_limit", "message": "Calculation output exceeded its limit."}
        except MemoryError:
            reply = {"status": "error", "code": "memory_limit", "message": "Calculation exceeded its memory limit."}
        except Exception as exc:
            # Do not echo untrusted exception text or paths.
            reply = {"status": "error", "code": "calculation_error", "message": "Calculation failed: " + type(exc).__name__}
    except Exception:
        pass
    encoded = json.dumps(reply, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > MAX_OUTPUT:
        encoded = b'{"status":"error","code":"output_limit","message":"Result exceeded its size limit."}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
