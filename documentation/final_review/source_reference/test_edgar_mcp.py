"""Offline MCP contract and transport tests using invented SEC data only."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import ProxyHandler, Request, build_opener
from urllib.error import HTTPError

from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

from check_edgar_mcp import direct_http_client
from edgar_mcp import create_server, TOOL_NAMES
from financial_data import get_annual_financial_fact
from financial_contracts import TOOL_NAME
from output_guardrails import check_and_render
from test_api import write_fixture, make_claim, draft, args as tool_args


class EdgarMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        write_fixture(self.folder, "MSFT")
        for path in self.folder.iterdir():
            path.chmod(0o600)
        self.server = create_server(self.folder)

    async def call(self, name="get_annual_financial_fact", arguments=None):
        if arguments is None:
            arguments = {"ticker": "MSFT", "metric": "net_income", "year": 2025, "as_of": None}
        async with Client(self.server, timeout=5) as client:
            return await client.call_tool(name, arguments, raise_on_error=False)

    def facts(self):
        path = self.folder / "MSFT_companyfacts.json"
        return path, json.loads(path.read_text())

    async def test_catalogue_has_schemas_and_only_read_tools(self):
        async with Client(self.server) as client:
            tools = await client.list_tools()
        self.assertEqual({t.name for t in tools}, TOOL_NAMES)
        for tool in tools:
            self.assertIsInstance(tool.input_schema, dict)
            self.assertIsInstance(tool.output_schema, dict)
            annotations = tool.annotations.model_dump(by_alias=True)
            self.assertTrue(annotations["readOnlyHint"])
            self.assertFalse(annotations["destructiveHint"])
            self.assertFalse(annotations["openWorldHint"])
            self.assertNotIn("data_dir", tool.input_schema.get("properties", {}))

    async def test_company_catalogue_describes_coverage_and_metric_definitions(self):
        reply = (await self.call("list_companies", {})).structured_content
        self.assertEqual(reply["status"], "ok")
        self.assertEqual(len(reply["companies"]), 15)
        self.assertIn("Microsoft", next(c for c in reply["companies"] if c["ticker"] == "MSFT")["aliases"])
        self.assertEqual(len(reply["metrics"]), 5)
        self.assertIn("not a guarantee", reply["coverage_note"])

    async def test_fact_preserves_all_existing_evidence(self):
        expected = get_annual_financial_fact("MSFT", "net_income", 2025, data_dir=self.folder)
        reply = (await self.call()).structured_content
        self.assertEqual(reply, expected)
        self.assertEqual(reply["value"], 250)  # Invented fixture value.
        self.assertEqual(reply["period_start"], "2024-07-01")
        self.assertEqual(reply["period_end"], "2025-06-30")
        evidence = [{"id": "e1", "tool": TOOL_NAME, "arguments": tool_args(), "result": reply}]
        _, guardrail = check_and_render(draft(claims=[make_claim(reply)]), evidence, data_dir=self.folder)
        self.assertEqual(guardrail, {"status": "passed", "checked_claims": 1})

    async def test_alias_is_normalised_without_guessing_a_company(self):
        reply = (await self.call(arguments={"ticker": "Microsoft", "metric": "revenue", "year": 2025})).structured_content
        self.assertEqual((reply["status"], reply["ticker"], reply["value"]), ("ok", "MSFT", 1000))

    async def test_filings_are_bounded_sorted_and_cutoff_filtered(self):
        reply = (await self.call("list_annual_filings", {"ticker": "Microsoft", "limit": 1})).structured_content
        self.assertEqual(reply["total_matching"], 3)
        self.assertTrue(reply["truncated"])
        self.assertEqual(reply["filings"][0]["report_date"], "2025-06-30")
        self.assertNotIn("fiscal_year", reply["filings"][0])
        raw = (self.folder / "MSFT_filings.json").read_bytes()
        self.assertEqual(reply["filings_snapshot_sha256"], hashlib.sha256(raw).hexdigest())
        cutoff = (await self.call("list_annual_filings", {"ticker": "MSFT", "as_of": "2024-12-31"})).structured_content
        self.assertEqual([r["filed"] for r in cutoff["filings"]], ["2024-08-01", "2023-08-01"])

    async def test_fact_cutoff_is_not_replaced_with_latest(self):
        reply = (await self.call(arguments={"ticker": "MSFT", "metric": "net_income", "year": 2025,
                                          "as_of": "2025-07-31"})).structured_content
        self.assertEqual(reply["status"], "not_found")
        self.assertNotIn("value", reply)

    async def test_missing_required_year_and_wrong_types_do_not_run_lookup(self):
        base = {"ticker": "MSFT", "metric": "net_income", "year": 2025}
        bad = [{k: v for k, v in base.items() if k != "year"},
               *[{**base, "year": year} for year in [True, "2025", 2025.5, 1800, 2200]],
               {**base, "metric": "adjusted_profit"}, {**base, "data_dir": "/etc"}]
        with patch("edgar_mcp.lookup_fact") as lookup:
            for arguments in bad:
                with self.subTest(arguments=arguments):
                    reply = await self.call(arguments=arguments)
                    self.assertTrue(reply.is_error)
            lookup.assert_not_called()

    async def test_bad_ticker_and_cutoff_return_structured_errors_before_reading(self):
        with patch("edgar_mcp.lookup_fact") as lookup:
            for ticker, cutoff in [("../../.env", None), ("UNKNOWN", None), ("MSFT", "2025-02-30")]:
                reply = (await self.call(arguments={"ticker": ticker, "metric": "revenue",
                                                   "year": 2025, "as_of": cutoff})).structured_content
                self.assertEqual((reply["status"], reply["code"]), ("error", "invalid_arguments"))
            lookup.assert_not_called()
        reply = (await self.call("list_annual_filings", {"ticker": "../../.env"})).structured_content
        self.assertEqual(reply["code"], "invalid_arguments")

    async def test_filing_limit_rejects_out_of_range_and_boolean(self):
        for limit in [0, 101, True]:
            self.assertTrue((await self.call("list_annual_filings", {"ticker": "MSFT", "limit": limit})).is_error)

    async def test_missing_cache_is_an_error_not_missing_financial_data(self):
        (self.folder / "MSFT_filings.json").unlink()
        for name, args in [("get_annual_financial_fact", None), ("list_annual_filings", {"ticker": "MSFT"})]:
            reply = (await self.call(name, args)).structured_content
            self.assertEqual((reply["status"], reply["code"]), ("error", "data_file_missing"))

    async def test_corrupt_or_wrong_company_history_does_not_escape_as_exception(self):
        path = self.folder / "MSFT_filings.json"
        original = json.loads(path.read_text())
        for text in ["broken json", json.dumps({**original, "cik": "0000000001"})]:
            path.write_text(text)
            for name, args in [("get_annual_financial_fact", None), ("list_annual_filings", {"ticker": "MSFT"})]:
                reply = await self.call(name, args)
                self.assertFalse(reply.is_error)
                self.assertEqual(reply.structured_content["status"], "error")
                self.assertEqual(reply.structured_content["code"], "invalid_data_file")

    async def test_permission_failure_is_structured(self):
        with patch("pathlib.Path.read_bytes", side_effect=PermissionError("private path")):
            for name, args in [("get_annual_financial_fact", None), ("list_annual_filings", {"ticker": "MSFT"})]:
                reply = (await self.call(name, args)).structured_content
                self.assertEqual(reply["code"], "data_file_unreadable")
                self.assertNotIn("private path", json.dumps(reply))

    async def test_conflicting_values_do_not_return_an_amount(self):
        path, facts = self.facts()
        rows = facts["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]
        rows.append({**rows[-1], "val": 999})
        path.write_text(json.dumps(facts))
        reply = (await self.call()).structured_content
        self.assertEqual(reply["status"], "conflict")
        self.assertNotIn("value", reply)

    async def test_parent_and_consolidated_profit_remain_separate(self):
        path, facts = self.facts()
        gaap = facts["facts"]["us-gaap"]
        gaap["ProfitLoss"] = gaap.pop("NetIncomeLoss")
        path.write_text(json.dumps(facts))
        parent = (await self.call()).structured_content
        self.assertEqual(parent["status"], "not_found")
        self.assertNotIn("value", parent)
        self.assertEqual(parent["available_alternatives"][0]["metric"], "net_income_consolidated")
        consolidated = (await self.call(arguments={"ticker": "MSFT", "metric": "net_income_consolidated", "year": 2025})).structured_content
        self.assertEqual((consolidated["status"], consolidated["value"]), ("ok", 250))

    async def test_tools_do_not_change_cache_or_need_external_connections(self):
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        with patch("socket.create_connection", side_effect=AssertionError("No network needed")):
            await self.call("list_companies", {})
            await self.call("list_annual_filings", {"ticker": "MSFT"})
            await self.call()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.folder.iterdir()})
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.folder.iterdir()))

    async def test_real_stdio_transport_works_without_api_key(self):
        transport = StdioTransport(command=sys.executable,
            args=[str(Path(__file__).with_name("edgar_mcp.py")), "--data-dir", str(self.folder)],
            env={"PYTHONDONTWRITEBYTECODE": "1"})
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            async with Client(transport, timeout=10, init_timeout=10) as client:
                self.assertEqual({t.name for t in await client.list_tools()}, TOOL_NAMES)
                reply = await client.call_tool("get_annual_financial_fact", {"ticker": "MSFT", "metric": "net_income", "year": 2025})
                self.assertEqual(reply.structured_content["value"], 250)

    async def test_real_http_transport_health_discovery_and_errors(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "SYSTEMROOT", "TEMP", "TMP"}}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        with tempfile.TemporaryFile(mode="w+") as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("edgar_mcp.py")),
                "--transport", "http", "--port", str(port), "--data-dir", str(self.folder)],
                env=env, stdout=log, stderr=log)
            try:
                opener = build_opener(ProxyHandler({}))
                for _ in range(150):
                    if process.poll() is not None:
                        log.seek(0)
                        self.fail(log.read())
                    try:
                        with opener.open(f"http://127.0.0.1:{port}/health", timeout=0.2) as response:
                            self.assertEqual(json.load(response), {"status": "ok", "service": "edgar-mcp"})
                        break
                    except OSError:
                        await asyncio.sleep(0.1)
                else:
                    log.seek(0)
                    self.fail("MCP HTTP startup timed out: " + log.read())
                # Exercise the Compose service hostname even without Docker DNS.
                transport = StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp",
                    headers={"Host": f"edgar:{port}"}, httpx_client_factory=direct_http_client)
                async with Client(transport, timeout=5) as client:
                    self.assertEqual({t.name for t in await client.list_tools()}, TOOL_NAMES)
                    reply = await client.call_tool("get_annual_financial_fact", {"ticker": "MSFT", "metric": "net_income", "year": 2025})
                    self.assertEqual(reply.structured_content["value"], 250)
                    reply = await client.call_tool("list_annual_filings", {"ticker": "GOOGL"})
                    self.assertEqual(reply.structured_content["code"], "data_file_missing")
                for headers, status in [({"Host": "attacker.example"}, 421),
                                        ({"Origin": "https://attacker.example"}, 403)]:
                    with self.assertRaises(HTTPError) as caught:
                        opener.open(Request(f"http://127.0.0.1:{port}/mcp", headers=headers), timeout=2)
                    self.assertEqual(caught.exception.code, status)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
