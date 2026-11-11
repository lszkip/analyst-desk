"""Actual MCP contract and HTTP tests with explicitly fictional model fixtures."""
import asyncio
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
import uvicorn

from check_edgar_mcp import direct_http_client
from edgar_mcp import create_server as edgar_server
from filings_kb_mcp import create_server, TOOL_NAMES
from filing_retrieval import SearchEngine
from test_filing_retrieval import build_fixture


class FilingsKBTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.catalogue,self.models,self.qdrant=build_fixture(self.root)
        self.addCleanup(self.qdrant.close)
        self.engine=SearchEngine(self.catalogue,self.models,self.qdrant,index_dir=self.root/"index")
        self.server=create_server(self.engine)
        self.request={"query":"cyber cloud risk", "tickers":["MSFT"], "years":[2025]}
        self.table=next(p for p in self.catalogue.passages.values() if p["kind"]=="table")

    async def call(self,name,args=None,server=None):
        async with Client(server or self.server,timeout=10) as client:
            return await client.call_tool(name,args or {},raise_on_error=False)

    async def test_every_tool_has_typed_schemas_and_read_only_annotation(self):
        async with Client(self.server) as client:tools=await client.list_tools()
        self.assertEqual({t.name for t in tools},TOOL_NAMES)
        for tool in tools:
            self.assertIsInstance(tool.input_schema,dict);self.assertIsInstance(tool.output_schema,dict)
            a=tool.annotations.model_dump(by_alias=True)
            self.assertTrue(a["readOnlyHint"]);self.assertFalse(a["destructiveHint"])
            self.assertNotIn("qdrant_url",tool.input_schema.get("properties",{}))

    async def test_plan_is_returned_without_search(self):
        with patch.object(self.engine,"search",side_effect=AssertionError):
            reply=(await self.call("plan_search",{"request":self.request})).structured_content
        self.assertEqual(reply["status"],"ok")
        self.assertEqual(reply["data"]["plan"][0]["years"],[2025])

    async def test_search_returns_source_linked_evidence(self):
        reply=(await self.call("search_filings",{"request":self.request})).structured_content
        self.assertEqual(reply["status"],"ok")
        self.assertTrue(reply["data"]["hits"][0]["untrusted_source"])
        self.assertIn(reply["data"]["hits"][0]["passage_id"],self.catalogue.passages)

    async def test_complete_table_passage_is_preserved(self):
        reply=(await self.call("get_passage",{"passage_id":self.table["passage_id"]})).structured_content
        self.assertEqual(reply["data"]["passage"],self.table)

    async def test_table_pagination_preserves_exact_cells(self):
        reply=(await self.call("get_table_rows",{"passage_id":self.table["passage_id"],"offset":1,"limit":1})).structured_content
        self.assertEqual(reply["data"]["rows"],[self.table["table"]["rows"][1]])
        self.assertIsNone(reply["data"]["next_offset"])

    async def test_coverage_reports_actual_pairs(self):
        reply=(await self.call("get_coverage")).structured_content
        self.assertEqual(reply["data"]["company_years"],4)
        self.assertEqual(reply["data"]["companies"],["GOOGL","MSFT"])

    async def test_argument_validation_rejects_wrong_types_and_operator_fields(self):
        for args in ({"request":{**self.request,"years":["2025"]}},
                     {"request":{**self.request,"limit":True}},
                     {"request":{**self.request,"qdrant_url":"http://example.invalid"}}):
            with self.subTest(args=args):
                reply=await self.call("search_filings",args)
                self.assertTrue(reply.is_error)

    async def test_path_argument_and_unknown_passage_are_safe_errors(self):
        for pid in ("../../.env","p_"+"a"*64):
            with self.subTest(pid=pid):
                reply=(await self.call("get_passage",{"passage_id":pid})).structured_content
                self.assertEqual(reply["status"],"error")
                self.assertNotIn(str(self.root),json.dumps(reply))

    async def test_table_limits_are_enforced(self):
        for limit in (0,51,True):
            reply=await self.call("get_table_rows",{"passage_id":self.table["passage_id"],"limit":limit})
            self.assertTrue(reply.is_error)

    async def test_non_table_cannot_be_read_as_table(self):
        p=next(p for p in self.catalogue.passages.values() if p["kind"]=="text")
        reply=(await self.call("get_table_rows",{"passage_id":p["passage_id"]})).structured_content
        self.assertEqual(reply["status"],"error")

    async def test_changed_source_fails_closed(self):
        path=next(iter(self.catalogue.source_files));path.write_bytes(path.read_bytes()+b" ")
        for name,args in (("get_coverage",{}),("search_filings",{"request":self.request}),
                          ("get_passage",{"passage_id":self.table["passage_id"]})):
            with self.subTest(name=name):
                reply=(await self.call(name,args)).structured_content
                self.assertEqual(reply["status"],"error")

    async def test_qdrant_outage_returns_structured_error_without_secret(self):
        with patch.object(self.qdrant,"query_points",side_effect=RuntimeError("private-api-key")):
            reply=(await self.call("search_filings",{"request":self.request})).structured_content
        self.assertEqual(reply["code"],"retrieval_unavailable")
        self.assertNotIn("private-api-key",json.dumps(reply))

    async def test_read_calls_do_not_mutate_sources_or_index(self):
        files=[p for p in self.root.rglob("*") if p.is_file()]
        before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in files}
        await self.call("search_filings",{"request":self.request})
        await self.call("get_passage",{"passage_id":self.table["passage_id"]})
        self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_mtime_ns) for p in files})

    async def test_real_http_transport_discovers_and_calls_kb(self):
        sock=socket.socket();sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
        app=self.server.http_app(path="/mcp",json_response=True,stateless_http=True)
        server=uvicorn.Server(uvicorn.Config(app,log_level="error"))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        try:
            for _ in range(100):
                if server.started:break
                await asyncio.sleep(.02)
            self.assertTrue(server.started)
            transport=StreamableHttpTransport(f"http://127.0.0.1:{port}/mcp",httpx_client_factory=direct_http_client)
            async with Client(transport,timeout=10) as client:
                self.assertEqual({t.name for t in await client.list_tools()},TOOL_NAMES)
                reply=await client.call_tool("search_filings",{"request":self.request})
                self.assertEqual(reply.structured_content["status"],"ok")
        finally:
            server.should_exit=True
            await asyncio.to_thread(thread.join,5)
            sock.close()

    async def test_edgar_get_section_returns_paginated_source_references(self):
        server=edgar_server(self.root/"raw",passages_dir=self.root/"passages",corpus_dir=self.root/"filings")
        reply=(await self.call("get_section",{"ticker":"Microsoft","year":2025,"item":"8","limit":1},server)).structured_content
        self.assertEqual(reply["status"],"ok")
        self.assertEqual(reply["total_passages"],2);self.assertEqual(reply["next_offset"],1)
        self.assertIn(reply["passages"][0]["passage_id"],self.catalogue.passages)
        self.assertIn("Amounts",reply["passages"][0]["text"])
        following=(await self.call("get_section",{"ticker":"MSFT","year":2025,"item":"8","offset":1,"limit":1},server)).structured_content
        self.assertEqual(following["passages"][0]["table"]["rows"][1][1]["text"],"100")

    async def test_edgar_section_errors_are_structured(self):
        server=edgar_server(self.root/"raw",passages_dir=self.root/"passages",corpus_dir=self.root/"filings")
        for changes in ({"ticker":"../../.env"},{"year":2023},{"item":"99"}):
            args={"ticker":"MSFT","year":2025,"item":"8",**changes}
            self.assertEqual((await self.call("get_section",args,server)).structured_content["status"],"error")

    async def test_edgar_section_rejects_missing_or_changed_sources(self):
        path=next(iter(self.catalogue.source_files));path.unlink()
        server=edgar_server(self.root/"raw",passages_dir=self.root/"passages",corpus_dir=self.root/"filings")
        reply=(await self.call("get_section",{"ticker":"MSFT","year":2025,"item":"8"},server)).structured_content
        self.assertEqual(reply["status"],"error")


if __name__=="__main__":unittest.main()
