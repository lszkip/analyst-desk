"""Real subprocess and kernel policy checks; no mocked successful isolation."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from python_sandbox import SLOTS, execute, policy


class SandboxTests(unittest.TestCase):
    def test_decimal_financial_calculation_and_kernel_probes(self):
        value = execute('result = (Decimal(inputs["income"]) / Decimal(inputs["revenue"]) * 100).quantize(Decimal("0.01"))',
                        {"income": "25", "revenue": "100"})
        self.assertEqual(value["status"], "ok", value)
        self.assertEqual(value["data"]["result"], "25.00")
        self.assertTrue(all(value["data"]["kernel_probes"].values()))

    def test_pandas_grouped_calculation(self):
        result = execute('df = pd.DataFrame(inputs["rows"])\nresult = df.groupby("company")["value"].sum().to_dict()',
                         {"rows": [{"company": "FICT", "value": 3}, {"company": "FICT", "value": 7}]})
        self.assertEqual(result["data"]["result"], {"FICT": 10}, result)

    def test_inputs_cannot_execute_instructions(self):
        result = execute('result = inputs["text"]', {"text": "__import__('os').system('false')"})
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["result"], "__import__('os').system('false')")

    def test_escape_network_and_file_syntax_is_rejected(self):
        for code in ['import socket', 'result = open("/etc/passwd").read()', 'result = (1).__class__',
                     'result = pd.read_csv("http://example.invalid")', 'result = pd.DataFrame().to_pickle("/tmp/x")',
                     'result = getattr(pd, "read_csv")', 'result = eval(inputs["code"])',
                     'pd = 3', 'result = "{0.__class__}".format(1)', 'result = pd.DataFrame().query("x")']:
            with self.subTest(code=code):
                self.assertEqual(execute(code, {})["code"], "invalid_calculation")

    def test_private_environment_is_not_inherited(self):
        # Inspect the actual Popen invocation while still running the real child.
        import subprocess
        real = subprocess.Popen
        environments = []
        def launch(*args, **kwargs):
            environments.append(kwargs["env"])
            return real(*args, **kwargs)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "private-fixture", "HTTP_PROXY": "private-fixture"}), patch("python_sandbox.subprocess.Popen", side_effect=launch):
            self.assertEqual(execute("result = 1", {})["status"], "ok")
        self.assertNotIn("OPENROUTER_API_KEY", environments[0]); self.assertNotIn("HTTP_PROXY", environments[0])

    def test_cpu_limit_terminates_infinite_loop(self):
        start = time.monotonic()
        value = execute("while True:\n    pass", {})
        self.assertEqual(value["status"], "error")
        self.assertIn(value["code"], {"resource_limit", "time_limit"})
        self.assertLess(time.monotonic() - start, 12)

    def test_wall_clock_limit_kills_child(self):
        with patch("python_sandbox.WALL_SECONDS", .05):
            value = execute("while True:\n    pass", {})
        self.assertEqual(value["code"], "time_limit")

    def test_memory_limit_is_enforced(self):
        value = execute('result = "x" * 2000000000', {})
        self.assertEqual(value["status"], "error")
        self.assertIn(value["code"], {"memory_limit", "resource_limit"}, value)

    def test_print_and_result_output_limits(self):
        for code in ['print("x" * 40000)\nresult = 1', 'result = "x" * 70000']:
            self.assertEqual(execute(code, {})["code"], "output_limit")

    def test_invalid_result_and_calculation_errors_are_structured(self):
        for code in ['result = 1 / 0', 'result = float("nan")', 'result = Decimal("Infinity")', 'x = 1', 'result = pd.DataFrame({"a": [1]})']:
            with self.subTest(code=code):
                self.assertEqual(execute(code, {})["code"], "calculation_error")

    def test_input_and_code_limits(self):
        self.assertEqual(execute("result = 1", {"x": "x" * 65536})["code"], "input_limit")
        self.assertEqual(execute(" " * 16001, {})["code"], "invalid_calculation")
        self.assertEqual(execute("result = inputs", {"x": float("nan")})["code"], "invalid_calculation")

    def test_busy_service_rejects_without_spawning(self):
        SLOTS.acquire(); SLOTS.acquire()
        try:
            self.assertEqual(execute("result = 1", {})["code"], "sandbox_busy")
        finally:
            SLOTS.release(); SLOTS.release()

    def test_unavailable_runner_has_no_fallback(self):
        with patch("python_sandbox.subprocess.Popen", side_effect=OSError("secret-path")):
            result = execute("result = 123", {})
        self.assertEqual(result["code"], "sandbox_unavailable")
        self.assertNotIn("secret-path", str(result))

    def test_each_call_is_fresh(self):
        self.assertEqual(execute("private_value = 12\nresult = private_value", {})["status"], "ok")
        self.assertEqual(execute("result = private_value", {})["code"], "calculation_error")


if __name__ == "__main__":
    unittest.main()
