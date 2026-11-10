"""Bounded HTTP MCP retrieval for the synchronous FastAPI request worker.

The endpoint is operator configuration, never model/user input. The API keeps
its reviewed tool schema and checks the returned evidence against its own cache.
No credentials, prompts or arbitrary file paths are sent to this server.
"""

import asyncio
import json
from urllib.parse import urlsplit

from financial_contracts import FinancialFactArguments, MAX_LOOKUPS, TOOL_NAME

BATCH_TIMEOUT_SECONDS = 30
MAX_RESULT_BYTES = 1024 * 1024
RESULT_STATUSES = {"ok", "not_found", "conflict", "unsupported_period", "error"}


class EdgarMCPError(RuntimeError):
    """The configured MCP service did not supply a usable complete batch."""


def validate_endpoint(url: str) -> str:
    """This local deployment permits only its Compose service or loopback."""
    try:
        parts = urlsplit(url)
        if (not isinstance(url, str) or url != url.strip()
                or any(character.isspace() or ord(character) < 32 for character in url)
                or parts.scheme != "http"
                or parts.hostname not in {"edgar", "localhost", "127.0.0.1", "::1"}
                or parts.username is not None or parts.password is not None
                or parts.port is None or not 1 <= parts.port <= 65535
                or parts.path != "/mcp" or parts.query or parts.fragment):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("EDGAR_MCP_URL must be a local HTTP endpoint such as http://edgar:8001/mcp.") from None
    return url


def direct_http_client(*args, **kwargs):
    import httpx2

    # No proxy environment, API key, cookies or inherited authentication.
    kwargs["trust_env"] = False
    kwargs["follow_redirects"] = False
    return httpx2.AsyncClient(*args, **kwargs)


async def _fetch_batch(url, requests):
    # Lazy imports keep the existing direct/local test environment available.
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(url, httpx_client_factory=direct_http_client,
                                        max_sse_event_size=MAX_RESULT_BYTES)
    async with asyncio.timeout(BATCH_TIMEOUT_SECONDS):
        async with Client(transport, timeout=10, init_timeout=5) as client:
            tools = await client.list_tools()
            matching = [tool for tool in tools if tool.name == TOOL_NAME]
            if len(matching) != 1 or matching[0].annotations is None:
                raise EdgarMCPError("Required read-only tool is unavailable.")
            annotations = matching[0].annotations.model_dump(by_alias=True)
            if annotations.get("readOnlyHint") is not True or annotations.get("destructiveHint") is not False:
                raise EdgarMCPError("Required read-only tool is unavailable.")
            results = []
            for arguments in requests:
                reply = await client.call_tool(TOOL_NAME, arguments, raise_on_error=False)
                result = reply.structured_content
                if (reply.is_error or not isinstance(result, dict)
                        or result.get("status") not in RESULT_STATUSES
                        or len(json.dumps(result, allow_nan=False).encode("utf-8")) > MAX_RESULT_BYTES):
                    raise EdgarMCPError("Invalid structured financial result.")
                results.append(result)
            return results


def lookup_batch(url: str, requests: list[dict]) -> list[dict]:
    """Retrieve a complete batch or fail; never fall back to local retrieval.

Called from FastAPI's synchronous worker thread. Each request owns and closes
its MCP connection and event loop; no cross-request client state is retained.
"""
    try:
        validate_endpoint(url)
        if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_LOOKUPS:
            raise ValueError("Invalid batch size")
        canonical = [FinancialFactArguments.model_validate(item).model_dump() for item in requests]
        if len({json.dumps(item, sort_keys=True) for item in canonical}) != len(canonical):
            raise ValueError("Duplicate arguments")
        return asyncio.run(_fetch_batch(url, canonical))
    except Exception:
        # SDK errors may contain server text, URLs or nested exception groups.
        # Only this fixed error crosses the boundary into the API response.
        raise EdgarMCPError("EDGAR MCP retrieval failed.") from None
