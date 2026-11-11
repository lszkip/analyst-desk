"""Loopback-only operator clients. Never place human credentials in agent inputs."""
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
import httpx2


def credentials():
    return dotenv_values(Path(__file__).resolve().with_name(".env"))


def local_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Operator commands accept only a plain localhost HTTP base URL.")
    return url.rstrip("/")


def direct_client(*args, **kwargs):
    # FastMCP 4 / MCP 2 use httpx2 timeout/auth objects. Preserve that API.
    kwargs["trust_env"] = False
    return httpx2.AsyncClient(*args, **kwargs)


def mcp_client(url, token):
    if not token:
        raise ValueError("A required service credential is missing. Run setup_research_tools.py.")
    return Client(StreamableHttpTransport(local_url(url) + "/mcp", headers={"Authorization": "Bearer "+token},
                                         httpx_client_factory=direct_client), timeout=20)


async def call(client, name, arguments=None):
    result = (await client.call_tool(name, arguments or {})).structured_content
    if result["status"] != "ok":
        raise ValueError(f"{name} rejected the request ({result.get('code', 'unknown')}).")
    return result["data"]
