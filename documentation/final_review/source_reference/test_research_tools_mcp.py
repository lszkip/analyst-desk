"""Real authenticated HTTP MCP calls and human-route separation."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
from argparse import Namespace
import io

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
import httpx
import uvicorn

from check_edgar_mcp import direct_http_client
from python_sandbox_mcp import create_server as sandbox_server
from research_portal import PortalStore
from research_portal import ReviewDecision
from research_portal_mcp import create_server as portal_server, TOOL_NAMES
from test_research_portal import fixture_note

READ_TOKEN, DRAFT_TOKEN, PUBLISH_TOKEN, REVIEW_TOKEN, SANDBOX_TOKEN = [c*40 for c in "rdphs"]


@asynccontextmanager
async def serve(server):
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    app = server.http_app(path="/mcp", json_response=True, stateless_http=True)
    runner = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: runner.run(sockets=[sock]), daemon=True); thread.start()
    try:
        for _ in range(200):
            if runner.started:
                break
            await asyncio.sleep(.01)
        if not runner.started:
            raise RuntimeError("Test HTTP server did not start.")
        yield f"http://127.0.0.1:{port}"
    finally:
        runner.should_exit = True
        await asyncio.to_thread(thread.join, 5)
        sock.close()


def client(url, token):
    return Client(StreamableHttpTransport(url + "/mcp", headers={"Authorization": "Bearer " + token}, httpx_client_factory=direct_http_client), timeout=15)


class ResearchToolsMCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.store = PortalStore(Path(tmp.name) / "portal.sqlite3")
        self.server = portal_server(self.store, READ_TOKEN, DRAFT_TOKEN, PUBLISH_TOKEN, REVIEW_TOKEN)

    async def test_schemas_annotations_and_no_human_approval_tool(self):
        async with serve(self.server) as url, client(url, READ_TOKEN) as c:
            tools = await c.list_tools()
            self.assertEqual({t.name for t in tools}, TOOL_NAMES)
            for t in tools:
                self.assertIsInstance(t.input_schema, dict); self.assertIsInstance(t.output_schema, dict)
                self.assertEqual(t.annotations.read_only_hint, t.name not in {"prepare_note", "publish_note"})

    async def test_invalid_tokens_and_human_token_cannot_connect_to_mcp(self):
        async with serve(self.server) as url:
            for token in ["bad", "", REVIEW_TOKEN, SANDBOX_TOKEN]:
                with self.subTest(token=token[:1]):
                    with self.assertRaises(Exception):
                        async with client(url, token) as c:
                            await c.list_tools()

    async def test_read_credential_cannot_prepare_or_publish(self):
        async with serve(self.server) as url, client(url, READ_TOKEN) as c:
            for name, args in [("prepare_note", {"note": fixture_note().model_dump(), "idempotency_key": "test"}),
                               ("publish_note", {"proposal_id": "test", "expected_hash": "a"*64, "approval_token": "x"*43})]:
                reply = (await c.call_tool(name, args)).structured_content
                self.assertEqual(reply["code"], "permission_denied")

    async def test_writer_cannot_publish_and_publisher_cannot_create_draft(self):
        async with serve(self.server) as url:
            async with client(url, DRAFT_TOKEN) as c:
                reply = await c.call_tool("publish_note", {"proposal_id": "test", "expected_hash": "a"*64, "approval_token": "x"*43})
                self.assertEqual(reply.structured_content["code"], "permission_denied")
            async with client(url, PUBLISH_TOKEN) as c:
                reply = await c.call_tool("prepare_note", {"note": fixture_note().model_dump(), "idempotency_key": "test"})
                self.assertEqual(reply.structured_content["code"], "permission_denied")

    async def test_http_prepare_human_review_publish_and_read(self):
        async with serve(self.server) as url:
            async with client(url, DRAFT_TOKEN) as c:
                proposal = (await c.call_tool("prepare_note", {"note": fixture_note().model_dump(), "idempotency_key": "test"})).structured_content["data"]
            args = {"proposal_id": proposal["proposal_id"], "expected_hash": proposal["content_hash"], "approval_token": "x"*43}
            async with client(url, PUBLISH_TOKEN) as c:
                self.assertEqual((await c.call_tool("publish_note", args)).structured_content["code"], "approval_required")
            review = {"expected_hash": proposal["content_hash"], "action": "approve", "reviewer": "Synthetic HTTP test"}
            async with httpx.AsyncClient(trust_env=False) as http:
                for token in [READ_TOKEN, DRAFT_TOKEN, PUBLISH_TOKEN, "wrong"]:
                    response = await http.post(url+"/human-review/"+proposal["proposal_id"], headers={"Authorization": "Bearer "+token}, json=review)
                    self.assertEqual(response.status_code, 403)
                response = await http.post(url+"/human-review/"+proposal["proposal_id"], headers={"Authorization": "Bearer "+REVIEW_TOKEN}, json=review)
                self.assertEqual(response.status_code, 200, response.text)
                approval = response.json()["data"]
            args["approval_token"] = approval["approval_token"]
            async with client(url, PUBLISH_TOKEN) as c:
                published = (await c.call_tool("publish_note", args)).structured_content["data"]
                self.assertEqual(published, (await c.call_tool("publish_note", args)).structured_content["data"])
            async with client(url, READ_TOKEN) as c:
                self.assertEqual((await c.call_tool("list_notes")).structured_content["data"]["total"], 1)
                self.assertEqual((await c.call_tool("get_note", {"note_id": published["note_id"]})).structured_content["data"], published)
                value = (await c.call_tool("get_proposal", {"proposal_id": proposal["proposal_id"]})).structured_content["data"]
                self.assertNotIn(approval["approval_token"], str(value))

    async def test_review_origin_and_invalid_payload_are_rejected(self):
        proposal = self.store.prepare(fixture_note(), "test")
        async with serve(self.server) as url, httpx.AsyncClient(trust_env=False) as c:
            headers = {"Authorization": "Bearer "+REVIEW_TOKEN, "Origin": "https://attacker.invalid"}
            response = await c.post(url+"/human-review/"+proposal["proposal_id"], headers=headers, json={})
            self.assertEqual(response.status_code, 403)
            headers.pop("Origin")
            response = await c.post(url+"/human-review/"+proposal["proposal_id"], headers=headers, json={"approved": True})
            self.assertEqual(response.status_code, 400)

    async def test_validation_and_missing_data_are_structured(self):
        async with serve(self.server) as url, client(url, READ_TOKEN) as c:
            missing = await c.call_tool("get_note", {"note_id": "missing"})
            self.assertEqual(missing.structured_content["code"], "not_found")
            for args in [{"limit": True}, {"limit": 51}, {"limit": "10"}]:
                reply = await c.call_tool("list_notes", args, raise_on_error=False)
                self.assertTrue(reply.is_error)

    async def test_sandbox_http_auth_execution_and_policy(self):
        async with serve(sandbox_server(SANDBOX_TOKEN)) as url, client(url, SANDBOX_TOKEN) as c:
            tools = await c.list_tools()
            self.assertEqual({t.name for t in tools}, {"execute_python", "get_sandbox_policy"})
            self.assertTrue(all(t.output_schema and t.annotations.read_only_hint for t in tools))
            policy = (await c.call_tool("get_sandbox_policy")).structured_content
            self.assertFalse(policy["data"]["network"])
            result = (await c.call_tool("execute_python", {"code": "result = inputs[\"a\"] + 1", "inputs": {"a": 3}})).structured_content
            self.assertEqual(result["data"]["result"], 4)
            attack = (await c.call_tool("execute_python", {"code": "import os", "inputs": {}})).structured_content
            self.assertEqual(attack["code"], "invalid_calculation")

    async def test_in_process_client_cannot_bypass_auth(self):
        async with Client(self.server) as c:
            result = (await c.call_tool("list_notes")).structured_content
        self.assertEqual(result["code"], "permission_denied")

    async def test_service_refuses_missing_or_shared_credentials(self):
        with self.assertRaises(ValueError):
            sandbox_server("")
        with self.assertRaises(ValueError):
            portal_server(self.store, READ_TOKEN, DRAFT_TOKEN, PUBLISH_TOKEN, PUBLISH_TOKEN)

    async def test_shipped_operator_check_through_both_http_services(self):
        from check_research_tools import check
        keys = {"SANDBOX_API_TOKEN": SANDBOX_TOKEN, "PORTAL_READ_TOKEN": READ_TOKEN,
                "PORTAL_DRAFT_TOKEN": DRAFT_TOKEN, "PORTAL_PUBLISH_TOKEN": PUBLISH_TOKEN,
                "PORTAL_REVIEW_TOKEN": REVIEW_TOKEN}
        async with serve(self.server) as pu, serve(sandbox_server(SANDBOX_TOKEN)) as su:
            with patch("check_research_tools.credentials", return_value=keys):
                result = await check(su, pu)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["portal"]["published_during_check"])
        self.assertEqual(self.store.list_notes()["total"], 0)

    async def test_human_cli_review_publishes_only_after_explicit_input(self):
        from review_portal_note import review
        keys = {"PORTAL_READ_TOKEN": READ_TOKEN, "PORTAL_PUBLISH_TOKEN": PUBLISH_TOKEN, "PORTAL_REVIEW_TOKEN": REVIEW_TOKEN}
        proposal = self.store.prepare(fixture_note(), "cli-fixture")
        async with serve(self.server) as url:
            args = Namespace(portal_url=url, proposal_id=proposal["proposal_id"], reviewer="Simulated test human", action="approve", edited_note=None)
            output = io.StringIO()
            with patch("review_portal_note.credentials", return_value=keys), patch("sys.stdin.isatty", return_value=True), patch("sys.stdout", output), patch("builtins.input", return_value=""):
                await review(args)
            self.assertEqual(self.store.list_notes()["total"], 0)
            with patch("review_portal_note.credentials", return_value=keys), patch("sys.stdin.isatty", return_value=True), patch("sys.stdout", output), patch("builtins.input", return_value="APPROVE AND PUBLISH "+proposal["content_hash"][:12]):
                await review(args)
            self.assertEqual(self.store.list_notes()["total"], 1)
            self.assertNotIn(REVIEW_TOKEN, output.getvalue())

    async def test_human_cli_refuses_noninteractive_approval(self):
        from review_portal_note import review
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(ValueError):
                await review(Namespace())

    async def test_repeated_human_cli_returns_existing_note_without_prompt_or_write(self):
        from review_portal_note import review
        import json
        proposal = self.store.prepare(fixture_note(), "retry-cli")
        approved = self.store.review(proposal["proposal_id"], ReviewDecision(
            expected_hash=proposal["content_hash"], action="approve", reviewer="Synthetic fixture"))
        published = self.store.publish(proposal["proposal_id"], proposal["content_hash"], approved["approval_token"])
        database_before = self.store.path.read_bytes()
        # Only read credentials are present. The CLI must not prompt, approve or publish again.
        async with serve(self.server) as url:
            args = Namespace(portal_url=url, proposal_id=proposal["proposal_id"], reviewer="Synthetic fixture", action="approve", edited_note=None)
            for _ in range(2):
                output = io.StringIO()
                with patch("review_portal_note.credentials", return_value={"PORTAL_READ_TOKEN": READ_TOKEN}), patch("sys.stdin.isatty", return_value=True), patch("sys.stdout", output), patch("builtins.input", side_effect=AssertionError("Unexpected approval prompt")):
                    await review(args)
                result = json.loads(output.getvalue())
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["action"], "already_published")
                self.assertEqual(result["note_id"], published["note_id"])
        self.assertEqual(self.store.list_notes()["total"], 1)
        self.assertEqual(self.store.path.read_bytes(), database_before)

    async def test_closed_proposal_actions_are_reported_before_prompting(self):
        from review_portal_note import ReviewCommandError, review
        proposal = self.store.prepare(fixture_note(), "closed-cli")
        self.store.review(proposal["proposal_id"], ReviewDecision(
            expected_hash=proposal["content_hash"], action="reject", reviewer="Synthetic fixture"))
        async with serve(self.server) as url:
            args = Namespace(portal_url=url, proposal_id=proposal["proposal_id"], reviewer="Synthetic fixture", action="approve", edited_note=None)
            with patch("review_portal_note.credentials", return_value={"PORTAL_READ_TOKEN": READ_TOKEN}), patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=AssertionError("Unexpected approval prompt")):
                with self.assertRaises(ReviewCommandError) as exc:
                    await review(args)
                self.assertEqual(exc.exception.code, "already_rejected")
                args.action = "reject"
                output = io.StringIO()
                with patch("sys.stdout", output):
                    await review(args)
                self.assertIn('"already_rejected"', output.getvalue())
        self.assertEqual(self.store.list_notes()["total"], 0)

    async def test_published_note_cannot_be_edited_or_rejected_by_cli(self):
        from review_portal_note import ReviewCommandError, review
        proposal = self.store.prepare(fixture_note(), "immutable-cli")
        approved = self.store.review(proposal["proposal_id"], ReviewDecision(
            expected_hash=proposal["content_hash"], action="approve", reviewer="Synthetic fixture"))
        self.store.publish(proposal["proposal_id"], proposal["content_hash"], approved["approval_token"])
        database_before = self.store.path.read_bytes()
        async with serve(self.server) as url:
            args = Namespace(portal_url=url, proposal_id=proposal["proposal_id"], reviewer="Synthetic fixture", edited_note=None)
            for action in ("edit", "reject"):
                args.action = action
                with patch("review_portal_note.credentials", return_value={"PORTAL_READ_TOKEN": READ_TOKEN}), patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=AssertionError("Unexpected approval prompt")):
                    with self.assertRaises(ReviewCommandError) as exc:
                        await review(args)
                    self.assertEqual(exc.exception.code, "already_published")
        self.assertEqual(self.store.path.read_bytes(), database_before)


if __name__ == "__main__":
    unittest.main()
