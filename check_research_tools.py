"""Check live MCP services without approving or publishing a note."""
import argparse
import asyncio
import json
import secrets
import httpx

from research_tools_client import call, credentials, local_url, mcp_client


async def check(sandbox_url, portal_url):
    keys = credentials()
    async with mcp_client(sandbox_url, keys.get("SANDBOX_API_TOKEN")) as c:
        assert {t.name for t in await c.list_tools()} == {"execute_python", "get_sandbox_policy"}
        policy = await call(c, "get_sandbox_policy")
        arithmetic = await call(c, "execute_python", {"code": 'result = (Decimal(inputs["income"]) / Decimal(inputs["revenue"]) * 100).quantize(Decimal("0.01"))',
                                                      "inputs": {"income": "25", "revenue": "100"}})
        assert arithmetic["result"] == "25.00" and all(arithmetic["kernel_probes"].values())
        denied = (await c.call_tool("execute_python", {"code": "import socket", "inputs": {}})).structured_content
        assert denied["code"] == "invalid_calculation"
    async with mcp_client(portal_url, keys.get("PORTAL_READ_TOKEN")) as c:
        assert {t.name for t in await c.list_tools()} == {"list_notes", "get_note", "get_proposal", "prepare_note", "publish_note"}
        before = await call(c, "list_notes")
    note = {"title": "TEST ONLY — research tools connectivity", "body_markdown": "Synthetic test data: 25 divided by 100 equals 25%. This is not a company financial claim or an investment recommendation. The citation below is deliberately a fixture, not verified financial evidence.",
            "tickers": ["FICT"], "fiscal_years": [2025], "citations": [{"evidence_id": "synthetic_smoke_cell", "source_sha256": "a"*64,
            "source_url": "https://www.sec.gov/Archives/edgar/data/0/synthetic-fixture.htm"}]}
    async with mcp_client(portal_url, keys.get("PORTAL_DRAFT_TOKEN")) as c:
        proposal = await call(c, "prepare_note", {"note": note, "idempotency_key": "tools-smoke-"+secrets.token_hex(8)})
    args = {"proposal_id": proposal["proposal_id"], "expected_hash": proposal["content_hash"], "approval_token": "invalid-approval-"+"x"*32}
    async with mcp_client(portal_url, keys.get("PORTAL_PUBLISH_TOKEN")) as c:
        denied = (await c.call_tool("publish_note", args)).structured_content
        assert denied["code"] == "approval_required"
    async with mcp_client(portal_url, keys.get("PORTAL_READ_TOKEN")) as c:
        assert (await c.call_tool("publish_note", args)).structured_content["code"] == "permission_denied"
        assert (await call(c, "get_proposal", {"proposal_id": proposal["proposal_id"]}))["state"] == "pending"
        assert (await call(c, "list_notes"))["total"] == before["total"]
    async with httpx.AsyncClient(trust_env=False, timeout=10) as c:
        # Even the publisher credential is forbidden from approving.
        denied = await c.post(local_url(portal_url)+"/human-review/"+proposal["proposal_id"],
            headers={"Authorization": "Bearer "+keys["PORTAL_PUBLISH_TOKEN"]}, json={"action": "approve"})
        assert denied.status_code == 403
    return {"status": "ok", "sandbox": {"calculation": arithmetic["result"], "kernel_probes": arithmetic["kernel_probes"],
            "policy": policy["policy"]}, "portal": {"draft_prepared": True, "unapproved_publish_blocked": True,
            "read_only_publish_blocked": True, "agent_approval_blocked": True, "published_during_check": False,
            "proposal_id": proposal["proposal_id"], "content_hash": proposal["content_hash"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox-url", default="http://127.0.0.1:8003")
    parser.add_argument("--portal-url", default="http://127.0.0.1:8004")
    args = parser.parse_args()
    try:
        result = asyncio.run(check(local_url(args.sandbox_url), local_url(args.portal_url)))
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({"status": "error", "code": type(exc).__name__, "message": "Research-tool check failed. Check Compose health and local credentials; no secret was printed."}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
