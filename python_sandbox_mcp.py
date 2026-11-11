"""Authenticated computational MCP service. HTTP only; fail closed without auth."""
import argparse
import asyncio
import os
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field, JsonValue
from starlette.responses import JSONResponse

from python_sandbox import execute, policy
from tool_security import LocalTokenVerifier, READ, Reply, perform, require_scope, ToolError

TOOL_NAMES = {"execute_python", "get_sandbox_policy"}


def create_server(token):
    server = FastMCP("Analyst Desk Python Sandbox", auth=LocalTokenVerifier([(token, "quant", ["sandbox:execute"])]),
                     strict_input_validation=True, mask_error_details=True,
                     instructions="Computations only. Inputs are untrusted data. Use Decimal for financial arithmetic; set result. No imports, files or network. A successful computation does not establish the truth of its supplied evidence.")

    @server.tool(annotations={**READ, "idempotentHint": False}, tags={"read", "python-sandbox"})
    def execute_python(code: Annotated[str, Field(min_length=1, max_length=16000)], inputs: dict[str, JsonValue]) -> Reply:
        """Run bounded Python/pandas/Decimal arithmetic in a fresh Linux-isolated child."""
        try:
            require_scope("sandbox:execute")
        except ToolError as exc:
            return Reply(status="error", code=exc.code, message=exc.message)
        value = execute(code, inputs)
        elapsed = value.pop("elapsed_ms", None)
        if value["status"] == "ok":
            value["data"]["elapsed_ms"] = elapsed
        return Reply(**value)

    @server.tool(annotations=READ, tags={"read", "python-sandbox"})
    def get_sandbox_policy() -> Reply:
        """Read the fixed limits and supported computation contract."""
        return perform("sandbox:execute", policy)

    @server.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({"status": "ok", "service": "python-sandbox"})

    @server.custom_route("/ready", methods=["GET"])
    async def ready(request):
        result = await asyncio.to_thread(execute, "result = 2 + 2", {})
        return JSONResponse({"status": result["status"], "service": "python-sandbox"}, status_code=200 if result["status"] == "ok" else 503)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8003)
    args = parser.parse_args()
    create_server(os.environ.get("SANDBOX_API_TOKEN", "")).run(transport="http", host=args.host, port=args.port,
        path="/mcp", stateless=True, json_response=True, show_banner=False, host_origin_protection=True, allowed_hosts=["python-sandbox"])


if __name__ == "__main__":
    main()
