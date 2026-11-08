import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient


# Use a dummy key and prevent real AI client setup during testing.
# This does not change your .env or the running Uvicorn server.
with patch.dict(
    os.environ, {"OPENROUTER_API_KEY": "test-placeholder"}
), patch("openai.OpenAI"):
    import main


def fake_response(content=None, tool_calls=None):
    # Build the parts of an AI response that main.py reads.
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)]
    )


def fake_tool_call(year):
    # Simulate the AI requesting our financial function.
    payload = {
        "id": "call_test_1",
        "type": "function",
        "function": {
            "name": "get_msft_annual_net_income",
            "arguments": json.dumps({"year": year}),
        },
    }

    call = Mock()
    call.id = payload["id"]
    call.function = SimpleNamespace(**payload["function"])
    call.model_dump.return_value = payload

    return call


class AskEndpointTests(unittest.TestCase):
    def setUp(self):
        # Replace the AI client so nothing goes to OpenRouter.
        client_patch = patch("main.client")
        self.ai = client_patch.start().chat.completions.create
        self.addCleanup(client_patch.stop)

        # Control the lookup result without reading the SEC file.
        tool_patch = patch("main.get_msft_annual_net_income")
        self.lookup = tool_patch.start()
        self.addCleanup(tool_patch.stop)

        # Send test requests directly to our FastAPI application.
        self.http = TestClient(main.app)
        self.addCleanup(self.http.close)

    def prepare_lookup(self, year, result, answer):
        # First simulated AI response: request the tool.
        # Second simulated AI response: provide an explanation.
        self.ai.side_effect = [
            fake_response(tool_calls=[fake_tool_call(year)]),
            fake_response(content=answer),
        ]
        self.lookup.return_value = result

    def test_successful_lookup_preserves_evidence(self):
        # Fixed test data, not a fresh SEC lookup.
        result = {
            "status": "ok",
            "value": 101832000000,
            "unit": "USD",
        }

        self.prepare_lookup(
            2025,
            result,
            "Net income was USD 101.832 billion.",
        )

        response = self.http.post(
            "/ask",
            json={"question": "Microsoft net income for FY2025?"},
        )

        self.assertEqual(response.status_code, 200)
        self.lookup.assert_called_once_with(year=2025)
        self.assertEqual(
            response.json()["evidence"][0]["result"],
            result,
        )
        self.assertEqual(self.ai.call_count, 2)

    def test_missing_data_is_sent_back_to_ai(self):
        result = {
            "status": "not_found",
            "message": "No matching record.",
        }
        answer = "The downloaded data has no matching record."

        self.prepare_lookup(2099, result, answer)

        response = self.http.post(
            "/ask",
            json={"question": "Microsoft net income for FY2099?"},
        )

        self.assertEqual(response.status_code, 200)
        self.lookup.assert_called_once_with(year=2099)
        self.assertEqual(self.ai.call_count, 2)

        # Inspect what our code sent to the second AI request.
        second_request = self.ai.call_args_list[1].kwargs
        tool_message = second_request["messages"][-1]

        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(
            tool_message["tool_call_id"],
            "call_test_1",
        )
        self.assertEqual(
            json.loads(tool_message["content"]),
            result,
        )

        # Check that the API preserves the evidence and explanation.
        self.assertEqual(
            response.json()["evidence"][0]["result"],
            result,
        )
        self.assertEqual(response.json()["answer"], answer)

    def test_clarification_skips_lookup(self):
        self.ai.return_value = fake_response(
            content="Which fiscal year?"
        )

        response = self.http.post(
            "/ask",
            json={"question": "What was Microsoft's net income?"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["evidence"], [])
        self.assertEqual(
            response.json()["answer"],
            "Which fiscal year?",
        )
        self.lookup.assert_not_called()
        self.assertEqual(self.ai.call_count, 1)

    def test_invalid_year_is_rejected_before_lookup(self):
        # Simulate the AI supplying an invalid argument.
        self.ai.return_value = fake_response(
            tool_calls=[fake_tool_call("not a year")]
        )

        response = self.http.post(
            "/ask",
            json={"question": "Microsoft net income for FY2025?"},
        )

        self.assertEqual(response.status_code, 502)
        self.lookup.assert_not_called()
        self.assertEqual(self.ai.call_count, 1)

    def test_unreadable_file_is_reported_as_error(self):
        self.ai.return_value = fake_response(
            tool_calls=[fake_tool_call(2025)]
        )

        # Simulate a missing file without deleting anything.
        self.lookup.side_effect = FileNotFoundError(
            "Simulated missing file"
        )

        response = self.http.post(
            "/ask",
            json={"question": "Microsoft net income for FY2025?"},
        )

        self.assertEqual(response.status_code, 500)
        self.assertIn(
            "Could not read",
            response.json()["detail"],
        )
        self.assertEqual(self.ai.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)