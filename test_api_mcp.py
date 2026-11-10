"""Offline /ask integration: real HTTP MCP server, fictional SEC fixtures.

The model alone is mocked in integration tests. API and MCP have separate
fixture directories so mismatched snapshots cannot pass by sharing a dict.
No OpenRouter/SEC requests or real credentials are used.
"""

import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.request import ProxyHandler, build_opener

from fastapi.testclient import TestClient

import edgar_client
from financial_data import get_annual_financial_fact
from test_api import main, args, draft, fake_response, fake_tool_call, make_claim, write_fixture


class APIOverMCPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.remote = Path(cls.temp.name) / "remote"
        cls.remote.mkdir()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}/mcp"
        cls.log = tempfile.TemporaryFile(mode="w+")
        cls.addClassCleanup(cls.log.close)
        env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "SYSTEMROOT", "TEMP", "TMP"}}
        env.update(PYTHONDONTWRITEBYTECODE="1", FASTMCP_HOME=str(Path(cls.temp.name) / "fastmcp"))
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("edgar_mcp.py")),
            "--transport", "http", "--port", str(port), "--data-dir", str(cls.remote)],
            env=env, stdout=cls.log, stderr=cls.log)

        def stop():
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        cls.addClassCleanup(stop)
        opener = build_opener(ProxyHandler({}))
        for _ in range(150):
            if process.poll() is not None:
                cls.log.seek(0)
                raise AssertionError("MCP startup failed: " + cls.log.read())
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=0.2) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.1)
        raise AssertionError("MCP startup timed out")

    def setUp(self):
        for path in self.remote.iterdir():
            path.unlink()
        for ticker in ("MSFT", "GOOGL"):
            write_fixture(self.remote, ticker)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.local = Path(temporary.name)
        self.sync_cache()
        for target, value in (("main.DATA_DIR", self.local), ("main.EDGAR_MCP_URL", self.url),
                              ("main.client", Mock())):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("main.get_annual_financial_fact", side_effect=AssertionError("No local retrieval fallback"))
        self.direct = p.start()
        self.addCleanup(p.stop)
        self.ai = main.client.chat.completions.create
        self.http = TestClient(main.app)
        self.addCleanup(self.http.close)

    def sync_cache(self):
        for path in self.local.iterdir():
            path.unlink()
        for path in self.remote.iterdir():
            shutil.copyfile(path, self.local / path.name)

    def prepare(self, requests=None):
        requests = requests or [args()]
        results = [get_annual_financial_fact(**item, data_dir=self.local) for item in requests]
        self.ai.side_effect = [
            fake_response(tool_calls=[fake_tool_call(item, i) for i, item in enumerate(requests, 1)]),
            fake_response(content=draft(claims=[make_claim(result, f"e{i}")
                for i, result in enumerate(results, 1) if result["status"] == "ok"])),
        ]
        return results

    def ask(self, question="What was Microsoft's annual net income for FY2025?"):
        return self.http.post("/ask", json={"question": question})

    def mutate_remote(self, mutate):
        path = self.remote / "MSFT_companyfacts.json"
        document = json.loads(path.read_text())
        mutate(document["facts"]["us-gaap"])
        path.write_text(json.dumps(document))

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["detail"]["code"], code)
        self.assertNotIn("answer", response.json())
        self.direct.assert_not_called()

    def test_ask_retrieves_through_real_http_mcp_and_replays_local_evidence(self):
        expected = self.prepare()[0]
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["lookup_backend"], "edgar_mcp")
        self.assertEqual(body["evidence"][0]["result"], expected)
        self.assertEqual(body["guardrail"], {"status": "passed", "checked_claims": 1})
        self.assertIn("USD 250", body["answer"])  # Fictional value only.
        self.assertIn(expected["sources"][0]["url"], body["answer"])
        self.assertEqual(self.ai.call_count, 2)
        self.direct.assert_not_called()

    def test_two_companies_and_three_years_preserve_all_six_results(self):
        requests = [args(ticker=ticker, year=year) for ticker in ("MSFT", "GOOGL") for year in (2023, 2024, 2025)]
        expected = self.prepare(requests)
        response = self.ask("Compare Microsoft and Google annual net income for FY2023 through FY2025.")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["result"] for item in response.json()["evidence"]], expected)
        self.assertEqual(response.json()["guardrail"]["checked_claims"], 6)
        self.assertEqual(self.ai.call_count, 2)
        self.direct.assert_not_called()

    def test_cutoff_passes_to_mcp_and_returns_missing_without_second_model_call(self):
        expected = self.prepare([args(as_of="2025-07-31")])[0]
        response = self.ask("Microsoft FY2025 net income using filings dated on or before 2025-07-31")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["evidence"][0]["result"], expected)
        self.assertEqual(expected["status"], "not_found")
        self.assertEqual(self.ai.call_count, 1)

    def test_conflicting_remote_and_local_records_return_no_amount(self):
        def change(gaap):
            rows = gaap["NetIncomeLoss"]["units"]["USD"]
            rows.append({**rows[-1], "val": 999})
        self.mutate_remote(change)
        self.sync_cache()
        self.prepare()
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["evidence"][0]["result"]["status"], "conflict")
        self.assertNotIn("USD 250", response.json()["answer"])
        self.assertEqual(self.ai.call_count, 1)

    def test_missing_parent_suggests_consolidated_without_returning_its_amount(self):
        self.mutate_remote(lambda gaap: gaap.update(ProfitLoss=gaap.pop("NetIncomeLoss")))
        self.sync_cache()
        self.prepare()
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["evidence"][0]["result"]
        self.assertEqual(result["status"], "not_found")
        self.assertNotIn("value", result)
        self.assertEqual(result["available_alternatives"][0]["metric"], "net_income_consolidated")
        self.assertEqual(self.ai.call_count, 1)

    def test_explicit_consolidated_measure_works_over_mcp(self):
        self.mutate_remote(lambda gaap: gaap.update(ProfitLoss=gaap.pop("NetIncomeLoss")))
        self.sync_cache()
        self.prepare([args(metric="net_income_consolidated")])
        response = self.ask("What was Microsoft's consolidated net income for FY2025?")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["evidence"][0]["result"]["metric"], "net_income_consolidated")

    def test_whole_batch_is_rejected_before_mcp_for_unrequested_year(self):
        self.prepare([args(), args(year=2024)])
        with patch("main.lookup_batch") as fetch:
            response = self.ask()
            fetch.assert_not_called()
        self.assert_error(response, 502, "request_guardrail_blocked")

    def test_missing_year_needs_no_mcp_connection(self):
        self.prepare()
        with patch("main.lookup_batch") as fetch:
            response = self.ask("What was Microsoft's annual net income?")
            fetch.assert_not_called()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["evidence"], [])
        self.assertEqual(self.ai.call_count, 1)

    def test_unrequested_alternative_measure_needs_no_mcp_connection(self):
        self.prepare([args(metric="pretax_income")])
        with patch("main.lookup_batch") as fetch:
            response = self.ask()
            fetch.assert_not_called()
        self.assert_error(response, 502, "request_guardrail_blocked")

    def test_invalid_and_duplicate_tool_batches_never_reach_mcp(self):
        for requests in ([args(), args()], [args(), args(year=True)], [args(year=y) for y in range(2020, 2027)]):
            self.ai.side_effect = [fake_response(tool_calls=[fake_tool_call(item, i) for i, item in enumerate(requests, 1)])]
            with patch("main.lookup_batch") as fetch:
                response = self.ask()
                fetch.assert_not_called()
            self.assertIn(response.status_code, (422, 502))
            self.direct.assert_not_called()

    def test_missing_remote_cache_is_503_even_when_api_has_cache(self):
        self.prepare()
        (self.remote / "MSFT_companyfacts.json").unlink()
        response = self.ask()
        self.assert_error(response, 503, "financial_data_unavailable")
        self.assertEqual(self.ai.call_count, 1)

    def test_corrupt_remote_cache_is_503(self):
        self.prepare()
        (self.remote / "MSFT_filings.json").write_text("bad JSON")
        self.assert_error(self.ask(), 503, "financial_data_unavailable")
        self.assertEqual(self.ai.call_count, 1)

    def test_stopped_mcp_returns_503_without_using_api_cache(self):
        self.prepare()
        # Reserve a port without listening: connections fail, no timing race.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            with patch("main.EDGAR_MCP_URL", f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"):
                self.assert_error(self.ask(), 503, "edgar_mcp_unavailable")
        self.assertEqual(self.ai.call_count, 1)

    def test_remote_value_mismatch_is_blocked_before_second_model_call(self):
        self.prepare()
        self.mutate_remote(lambda gaap: gaap["NetIncomeLoss"]["units"]["USD"][-1].update(val=999))
        self.assert_error(self.ask(), 502, "output_guardrail_blocked")
        self.assertEqual(self.ai.call_count, 1)

    def test_same_values_but_different_snapshot_bytes_are_blocked(self):
        self.prepare()
        path = self.remote / "MSFT_companyfacts.json"
        path.write_text(path.read_text() + "\n")
        self.assert_error(self.ask(), 502, "output_guardrail_blocked")
        self.assertEqual(self.ai.call_count, 1)

    def test_missing_local_verification_cache_blocks_remote_success(self):
        self.prepare()
        (self.local / "MSFT_filings.json").unlink()
        self.assert_error(self.ask(), 502, "output_guardrail_blocked")
        self.assertEqual(self.ai.call_count, 1)

    def test_model_altered_claim_is_still_blocked_after_mcp_retrieval(self):
        result = self.prepare()[0]
        claim = make_claim(result)
        claim["value"] += 1
        self.ai.side_effect = [fake_response(tool_calls=[fake_tool_call(args())]),
                               fake_response(content=draft(claims=[claim]))]
        self.assert_error(self.ask(), 502, "output_guardrail_blocked")
        self.assertEqual(self.ai.call_count, 2)

    def test_cache_change_during_drafting_is_rechecked(self):
        result = self.prepare()[0]
        def model(**kwargs):
            if kwargs["tool_choice"] == "auto":
                return fake_response(tool_calls=[fake_tool_call(args())])
            path = self.local / "MSFT_companyfacts.json"
            path.write_text(path.read_text() + "\n")
            return fake_response(content=draft(claims=[make_claim(result)]))
        self.ai.side_effect = model
        self.assert_error(self.ask(), 502, "output_guardrail_blocked")

    def test_nonfinancial_definition_does_not_connect_to_mcp(self):
        self.ai.side_effect = [fake_response(content=draft(kind="define_net_income"))]
        with patch("main.lookup_batch") as fetch:
            response = self.ask("What does net income mean?")
            fetch.assert_not_called()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["evidence"], [])

    def test_second_tool_round_remains_blocked(self):
        self.ai.side_effect = [fake_response(tool_calls=[fake_tool_call(args())]),
                               fake_response(tool_calls=[fake_tool_call(args(year=2024))])]
        response = self.ask()
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.ai.call_count, 2)


class MCPClientBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.__aenter__ = AsyncMock(return_value=self.client)
        self.client.__aexit__ = AsyncMock(return_value=False)
        self.client.list_tools = AsyncMock(return_value=[SimpleNamespace(
            name=main.TOOL_NAME, annotations=SimpleNamespace(model_dump=lambda **kw:
                {"readOnlyHint": True, "destructiveHint": False}))])
        self.client.call_tool = AsyncMock(return_value=SimpleNamespace(is_error=False,
            structured_content={"status": "not_found"}))
        p = patch("fastmcp.Client", return_value=self.client)
        self.factory = p.start()
        self.addCleanup(p.stop)

    def call(self, requests=None):
        return edgar_client.lookup_batch("http://127.0.0.1:8001/mcp", requests or [args()])

    def test_only_configured_local_http_endpoint_is_allowed(self):
        for url in ("http://edgar:8001/mcp", "http://127.0.0.1:8001/mcp", "http://[::1]:8001/mcp"):
            self.assertEqual(edgar_client.validate_endpoint(url), url)
        for url in ("edgar_mcp.py", "", " https://example.com/mcp", "http://attacker:8001/mcp",
                    "http://127.0.0.1:8001/mcp?key=secret", "http://user:secret@edgar:8001/mcp",
                    "http://edgar/mcp", "http://edgar:8001/other", "http://edgar:8001/mcp#x",
                    "http://edgar:99999/mcp", "http://edgar:8001/mcp\n"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                edgar_client.validate_endpoint(url)
        self.factory.assert_not_called()

    def test_malformed_or_protocol_error_results_are_not_accepted(self):
        for result, is_error in ((None, False), ([], False), ({}, False),
                                 ({"status": "made_up"}, False), ({"status": "ok"}, True)):
            self.client.call_tool.return_value = SimpleNamespace(is_error=is_error, structured_content=result)
            with self.subTest(result=result), self.assertRaises(edgar_client.EdgarMCPError):
                self.call()

    def test_oversized_result_is_rejected(self):
        self.client.call_tool.return_value = SimpleNamespace(is_error=False,
            structured_content={"status": "ok", "text": "x" * edgar_client.MAX_RESULT_BYTES})
        with self.assertRaises(edgar_client.EdgarMCPError):
            self.call()

    def test_missing_or_write_tool_is_rejected_before_call(self):
        for tools in ([], [SimpleNamespace(name=main.TOOL_NAME, annotations=None)],
                      [SimpleNamespace(name=main.TOOL_NAME, annotations=SimpleNamespace(
                          model_dump=lambda **kw: {"readOnlyHint": False, "destructiveHint": True}))]):
            self.client.list_tools.return_value = tools
            with self.assertRaises(edgar_client.EdgarMCPError):
                self.call()
        self.client.call_tool.assert_not_called()

    def test_transport_exception_is_masked_and_no_partial_batch_is_returned(self):
        self.client.call_tool.side_effect = [self.client.call_tool.return_value,
                                            RuntimeError("untrusted server text secret=example")]
        with self.assertRaises(edgar_client.EdgarMCPError) as caught:
            self.call([args(), args(ticker="GOOGL")])
        self.assertEqual(str(caught.exception), "EDGAR MCP retrieval failed.")
        self.assertEqual(self.client.call_tool.call_count, 2)

    def test_batch_deadline_cancels_a_slow_server(self):
        async def stall(*a, **kw):
            await asyncio.Event().wait()
        self.client.call_tool.side_effect = stall
        with patch("edgar_client.BATCH_TIMEOUT_SECONDS", 0.05):
            started = time.monotonic()
            with self.assertRaises(edgar_client.EdgarMCPError):
                self.call()
            self.assertLess(time.monotonic() - started, 2)
        self.client.__aexit__.assert_awaited_once()

    def test_invalid_or_duplicate_batch_never_connects(self):
        for requests in ([], [args(), args()], [args(year=True)], [args(year=y) for y in range(2020, 2027)]):
            with self.assertRaises(edgar_client.EdgarMCPError):
                edgar_client.lookup_batch("http://127.0.0.1:8001/mcp", requests)
        self.factory.assert_not_called()

    def test_client_sends_only_canonical_arguments_and_fixed_tool(self):
        self.call([args(ticker="Microsoft")])
        self.client.call_tool.assert_awaited_once_with(main.TOOL_NAME, args(), raise_on_error=False)

    def test_http_client_does_not_use_proxy_environment(self):
        with patch("httpx2.AsyncClient") as factory:
            edgar_client.direct_http_client(timeout=10, trust_env=True)
        self.assertIs(factory.call_args.kwargs["trust_env"], False)
        self.assertIs(factory.call_args.kwargs["follow_redirects"], False)
        self.assertNotIn("headers", factory.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
