"""Deterministic credential and transport boundaries; no network requests.

All credentials are synthetic canaries. Mocked transport failures test error
handling, not an actual HTTP deployment or real-model injection resistance.
"""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from research_tools_client import local_url
from tool_security import LocalTokenVerifier, perform
from workflow_contracts import WorkflowError
from workflow_tools import MCPBackend


CANARY = "SYNTHETIC_SECRET_CANARY_SECURITY_TEST_ONLY_29"
READ_TOKEN = "read_" + "r" * 40
WRITE_TOKEN = "write_" + "w" * 40


class CredentialBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unregistered_token_cannot_claim_a_privileged_scope(self):
        verifier = LocalTokenVerifier([(READ_TOKEN, "reader", ["portal:read"]),
                                       (WRITE_TOKEN, "publisher", ["portal:publish"])])
        for token in ("portal:publish", READ_TOKEN + " portal:publish", CANARY):
            with self.subTest(case="unregistered credential"):
                self.assertIsNone(await verifier.verify_token(token))

    async def test_registered_read_credential_receives_only_server_assigned_scope(self):
        verifier = LocalTokenVerifier([(READ_TOKEN, "reader", ["portal:read"])])
        token = await verifier.verify_token(READ_TOKEN)
        self.assertIsNotNone(token)
        self.assertEqual(set(token.scopes), {"mcp", "portal:read"})
        self.assertNotIn(READ_TOKEN, repr(verifier.grants))

    def test_absent_or_readonly_authorization_cannot_execute_a_write(self):
        for token in (None, SimpleNamespace(scopes=["mcp", "portal:read"])):
            with self.subTest(case="no write grant"):
                operation = Mock(return_value={"published": True})
                with patch("tool_security.get_access_token", return_value=token):
                    result = perform("portal:publish", operation)
                operation.assert_not_called()
                self.assertEqual(result.status, "error")
                self.assertEqual(result.code, "permission_denied")

    def test_correct_authorization_executes_the_intended_operation_once(self):
        operation = Mock(return_value={"synthetic_result": "ok"})
        with patch("tool_security.get_access_token", return_value=SimpleNamespace(scopes=["mcp", "portal:read"])):
            result = perform("portal:read", operation)
        operation.assert_called_once_with()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.data, {"synthetic_result": "ok"})

    def test_authorized_operation_failure_does_not_echo_private_diagnostics(self):
        operation = Mock(side_effect=RuntimeError(CANARY))
        with patch("tool_security.get_access_token", return_value=SimpleNamespace(scopes=["portal:read"])):
            result = perform("portal:read", operation)
        self.assertEqual(result.code, "operation_failed")
        self.assertNotIn(CANARY, result.model_dump_json())


class EndpointBoundaryTests(unittest.TestCase):
    def test_deceptive_external_and_credential_bearing_endpoints_are_rejected(self):
        endpoints = ["http://localhost.attacker.invalid", "http://127.0.0.1.attacker.invalid",
                     "http://127.0.0.1@attacker.invalid", "http://attacker.invalid@127.0.0.1",
                     "http://localhost/?token=" + CANARY, "http://localhost/#" + CANARY,
                     "http://localhost/private", "https://localhost", "file:///etc/passwd"]
        for endpoint in endpoints:
            with self.subTest(case="invalid endpoint"):
                with self.assertRaises(ValueError) as caught:
                    local_url(endpoint)
                self.assertNotIn(CANARY, str(caught.exception))

    def test_plain_loopback_endpoint_is_accepted_without_network_access(self):
        self.assertEqual(local_url("http://127.0.0.1:8004/"), "http://127.0.0.1:8004")
        self.assertEqual(local_url("http://localhost:8003"), "http://localhost:8003")

    def test_broker_does_not_receive_review_or_model_credentials(self):
        backend = MCPBackend({"PORTAL_READ_TOKEN": READ_TOKEN,
                              "PORTAL_PUBLISH_TOKEN": WRITE_TOKEN,
                              "PORTAL_REVIEW_TOKEN": CANARY, "OPENROUTER_API_KEY": CANARY})
        self.assertNotIn(CANARY, json.dumps(backend.tokens))
        self.assertEqual(backend.tokens["portal_read"], READ_TOKEN)
        self.assertEqual(backend.tokens["portal_publish"], WRITE_TOKEN)


class TransportFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_publish_credential_stops_before_transport_creation(self):
        backend = MCPBackend({"PORTAL_READ_TOKEN": READ_TOKEN})
        with patch("workflow_tools.StreamableHttpTransport") as transport:
            with self.assertRaises(WorkflowError) as caught:
                await backend.call("portal", "publish_note", {}, "publish")
        transport.assert_not_called()
        self.assertEqual(caught.exception.code, "missing_credential")

    async def test_client_failure_redacts_synthetic_secret_and_private_path(self):
        backend = MCPBackend({})
        client = AsyncMock()
        client.__aenter__.side_effect = RuntimeError("/private/" + CANARY)
        with patch("workflow_tools.StreamableHttpTransport", return_value=object()), \
             patch("workflow_tools.Client", return_value=client):
            with self.assertRaises(WorkflowError) as caught:
                await backend.call("edgar", "get_section", {})
        self.assertEqual(caught.exception.code, "mcp_unavailable")
        self.assertNotIn(CANARY, str(caught.exception))

    async def test_nonstructured_tool_reply_fails_without_echoing_its_content(self):
        backend = MCPBackend({})
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.call_tool.return_value = SimpleNamespace(structured_content=[CANARY])
        with patch("workflow_tools.StreamableHttpTransport", return_value=object()), \
             patch("workflow_tools.Client", return_value=client):
            with self.assertRaises(WorkflowError) as caught:
                await backend.call("edgar", "get_section", {})
        self.assertEqual(caught.exception.code, "mcp_unavailable")
        self.assertNotIn(CANARY, str(caught.exception))

    async def test_transport_construction_failure_is_sanitized_before_return(self):
        backend = MCPBackend({})
        with patch("workflow_tools.StreamableHttpTransport", side_effect=ValueError(CANARY)):
            with self.assertRaises(WorkflowError) as caught:
                await backend.call("edgar", "get_section", {})
        self.assertEqual(caught.exception.code, "mcp_unavailable")
        self.assertNotIn(CANARY, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
