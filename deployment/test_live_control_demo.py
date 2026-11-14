"""Software checks only; these do not provide completed human-review evidence."""
import asyncio
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deployment import live_control_demo as demo


class BaseDemoChecks(unittest.TestCase):
    def setUp(self):
        from workflow_agents import load_config
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "private"
        self.folder.mkdir()
        demo.write_new(self.folder / "Demo_Owner.json", {"kind": "isolated_live_control_demo", "directory": str(self.folder)})
        demo.write_new(self.folder / "Workflow_Config.json", load_config())



class DemoOfflineChecks(BaseDemoChecks):
    def test_foreign_state_and_symlink_refused(self):
        other = Path(self.temp.name) / "foreign"
        other.mkdir()
        with self.assertRaises(ValueError):
            demo.validate_private(other)
        link = Path(self.temp.name) / "link"
        link.symlink_to(self.folder, target_is_directory=True)
        with self.assertRaises(ValueError):
            demo.validate_private(link)

    def test_existing_file_cannot_be_overwritten(self):
        with self.assertRaises(FileExistsError):
            demo.write_new(self.folder / "Demo_Owner.json", {})

    def test_piped_human_decisions_refused(self):
        with patch.object(demo.sys.stdin, "isatty", return_value=False), self.assertRaises(ValueError):
            demo.ask_exact("APPROVE", "Controlled test")

    def test_children_disable_external_tracing_and_drop_provider_keys(self):
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true",
                                     "OPENROUTER_API_KEY": "private-key", "LANGSMITH_API_KEY": "trace-key"}):
            env = demo.child_environment({"PORTAL_READ_TOKEN": "isolated-token"})
        self.assertEqual(env["LANGSMITH_TRACING"], "false")
        self.assertEqual(env["LANGCHAIN_TRACING_V2"], "false")
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertNotIn("LANGSMITH_API_KEY", env)
        self.assertEqual(env["PORTAL_READ_TOKEN"], "isolated-token")

    def test_scripted_planner_refuses_second_response_and_tools_refused(self):
        model = demo.ScriptedPlanner()
        async def run():
            args = ([{"content": json.dumps({"role": "supervisor"})}], {})
            await model.complete(*args)
            with self.assertRaises(ValueError):
                await model.complete(*args)
            with self.assertRaises(AssertionError):
                await demo.NoExternalTools().call("portal", "publish_note", {})
        asyncio.run(run())

    def test_synthetic_note_edit_changes_exact_hash(self):
        from research_portal import note_hash
        original, edited = demo.synthetic_note(), demo.synthetic_note(True)
        self.assertNotEqual(note_hash(original), note_hash(edited))
        self.assertIn("Synthetic software demonstration", original.body_markdown)
        self.assertEqual(original.tickers, ["FICT"])


class DemoLiveChecks(BaseDemoChecks):
    def test_actual_checkpoint_survives_two_processes_and_resume_rejects(self):
        # Production graph/checkpointer; scripted planner, no human review claim.
        first = demo.checkpoint_process(self.folder, "start")
        second = demo.checkpoint_process(self.folder, "resume")
        self.assertNotEqual(first["process_id"], second["process_id"])
        self.assertEqual(first["checkpoint_id"], second["checkpoint_id"])
        self.assertEqual(first["plan_hash"], second["plan_hash"])
        self.assertEqual(first["scripted_responses_this_process"], 1)
        self.assertEqual(second["scripted_responses_this_process"], 0)
        self.assertEqual(second["status_after"], "rejected")
        self.assertEqual(second["tool_calls"], 0)

    def test_replaced_checkpoint_identity_refused(self):
        demo.checkpoint_process(self.folder, "start")
        path = self.folder / "Checkpoint_Start.json"
        saved = json.loads(path.read_text())
        saved["plan_hash"] = "a" * 64
        path.write_text(json.dumps(saved))
        with self.assertRaises(ValueError):
            demo.checkpoint_process(self.folder, "resume")

    def test_live_portal_start_prepare_and_cancel_never_approve(self):
        import research_portal_mcp
        from research_portal import PortalStore
        source_root = Path(research_portal_mcp.__file__).parent
        record = {}
        # Real HTTP/MCP server; cancel before submitting any review. This test
        # deliberately supplies no human name or approval decision.
        with patch.object(demo, "ROOT", source_root), patch.object(demo.sys.stdin, "isatty", return_value=True), \
             patch("builtins.input", return_value=""), redirect_stdout(StringIO()):
            with self.assertRaises(ValueError):
                asyncio.run(demo.portal_demo(self.folder, record))
        self.assertTrue(record["portal"]["own_demo_server_stopped"])
        store = PortalStore(self.folder / "portal.sqlite3")
        self.assertEqual(store.list_notes()["total"], 0)
        with store.connect() as connection:
            actions = [row[0] for row in connection.execute("SELECT action FROM audit ORDER BY seq")]
        self.assertEqual(actions, ["prepared"])
        self.assertNotIn("operator_approved", record["portal"])

if __name__ == "__main__":
    unittest.main()
