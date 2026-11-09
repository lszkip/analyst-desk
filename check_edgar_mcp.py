"""Discover the EDGAR MCP tools and make a real protocol call, without an LLM."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

import httpx2
from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

from edgar_mcp import TOOL_NAMES
from financial_metrics import METRICS


def direct_http_client(*args, **kwargs):
    """Local/Docker MCP traffic should not use a shell's external proxy."""
    kwargs["trust_env"] = False
    return httpx2.AsyncClient(*args, **kwargs)


async def check(args):
    if args.stdio:
        transport = StdioTransport(command=sys.executable,
                                   args=[str(Path(__file__).with_name("edgar_mcp.py"))],
                                   env={"PYTHONDONTWRITEBYTECODE": "1"})
    else:
        transport = StreamableHttpTransport(args.url, httpx_client_factory=direct_http_client)
    async with Client(transport, timeout=30, init_timeout=15) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        if names != TOOL_NAMES:
            raise RuntimeError("Unexpected tool catalogue from the EDGAR server.")
        print("Discovered MCP tools: " + ", ".join(sorted(names)))
        catalogue = await client.call_tool("list_companies", {})
        print(f"Configured companies: {len(catalogue.structured_content['companies'])}")
        result = await client.call_tool("get_annual_financial_fact", {
            "ticker": args.ticker, "metric": args.metric, "year": args.year, "as_of": args.as_of,
        })
        print(json.dumps(result.structured_content, indent=2))
        return 0 if result.structured_content["status"] == "ok" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--url", default="http://127.0.0.1:8001/mcp")
    target.add_argument("--stdio", action="store_true")
    parser.add_argument("--ticker", default="MSFT")
    parser.add_argument("--metric", choices=list(METRICS), default="net_income")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--as-of")
    args = parser.parse_args()
    return asyncio.run(check(args))


if __name__ == "__main__":
    raise SystemExit(main())
