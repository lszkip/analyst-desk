"""Exercise all five KB tools over real HTTP MCP and check the read-only DB key."""
import argparse
import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from check_edgar_mcp import direct_http_client
from filings_kb_mcp import TOOL_NAMES
from filing_index import connect
from qdrant_client.http.exceptions import UnexpectedResponse


async def check(args):
    transport=StreamableHttpTransport(args.url,httpx_client_factory=direct_http_client)
    async with Client(transport,timeout=300,init_timeout=30) as client:
        tools=await client.list_tools()
        assert {t.name for t in tools}==TOOL_NAMES,"Unexpected tool catalogue"
        assert all(t.annotations.model_dump(by_alias=True)["readOnlyHint"] for t in tools)
        coverage=(await client.call_tool("get_coverage",{})).structured_content
        assert coverage["status"]=="ok",coverage
        request={"query":"Consolidated statement of income revenue net income", "tickers":[args.ticker],
                 "years":[args.year],"kind":"table","limit":2}
        plan=(await client.call_tool("plan_search",{"request":request})).structured_content
        assert plan["status"]=="ok",plan
        found=(await client.call_tool("search_filings",{"request":request})).structured_content
        assert found["status"]=="ok" and found["data"]["hits"],found
        hit=found["data"]["hits"][0]
        passage=(await client.call_tool("get_passage",{"passage_id":hit["passage_id"]})).structured_content
        assert passage["status"]=="ok",passage
        rows=(await client.call_tool("get_table_rows",{"passage_id":hit["passage_id"],"limit":3})).structured_content
        assert rows["status"]=="ok" and rows["data"]["rows"],rows
        readonly=connect(args.qdrant_url,read_only=True)
        try:
            # An empty upsert changes no points even if the key was misconfigured.
            try:
                readonly.upsert("ad_filings_"+coverage["data"]["generation"][:32],points=[],wait=True)
            except UnexpectedResponse as exc:
                assert exc.status_code==403,"Expected read-only write rejection"
            else:
                raise AssertionError("Qdrant read-only key accepted a write endpoint")
        finally:
            readonly.close()
        print(json.dumps({"status":"ok","tools":sorted(TOOL_NAMES),"read_only_write_rejected":True,"coverage":coverage["data"],
                          "sample_hit":hit,"sample_rows":rows["data"]},indent=2))
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url",default="http://127.0.0.1:8002/mcp")
    parser.add_argument("--qdrant-url",default="http://127.0.0.1:6333")
    parser.add_argument("--ticker",default="IBM")
    parser.add_argument("--year",type=int,default=2025)
    load_dotenv(Path(__file__).resolve().parent/".env")
    return asyncio.run(check(parser.parse_args()))


if __name__=="__main__":raise SystemExit(main())
