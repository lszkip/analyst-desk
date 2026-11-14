"""Offline safeguards; actual Docker installation proof comes from GitHub CI."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from deployment import ci_clean_install as ci


class CleanInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".git").mkdir()
        (self.root / "deployment").mkdir()
        self.env = {"CI": "true", "GITHUB_ACTIONS": "true", "ANALYST_CLEAN_RUNNER": "github-hosted",
                    "GITHUB_WORKSPACE": str(self.root), "GITHUB_RUN_ID": "123456", "GITHUB_RUN_ATTEMPT": "2"}

    def test_fresh_checkout_accepted_and_local_or_self_hosted_refused(self):
        self.assertEqual(ci.fresh_runner(self.root, self.env), "analyst-ci-123456-2")
        for key, value in (("CI", "false"), ("GITHUB_ACTIONS", "false"),
                           ("ANALYST_CLEAN_RUNNER", "self-hosted"), ("GITHUB_WORKSPACE", "/tmp/wrong")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                ci.fresh_runner(self.root, {**self.env, key: value})

    def test_existing_secrets_or_data_are_never_reused(self):
        for relative in (".env", "data", "monitoring/data", "monitoring/secrets", ci.REPORT_NAME,
                         "deployment/compose.ci.generated.json", "deployment/.ci-owner.json"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("sentinel")
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                ci.fresh_runner(self.root, self.env)
            self.assertEqual(path.read_text(), "sentinel")
            path.unlink()

    def test_dangling_state_symlink_and_command_injection_refused(self):
        (self.root / "data").symlink_to(self.root / "absent")
        with self.assertRaises(ValueError):
            ci.fresh_runner(self.root, self.env)
        for value in ("../main", "1 --all", "1;echo x", "", "x", "1\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ci.project_name({**self.env, "GITHUB_RUN_ID": value})

    def test_cleanup_targets_only_owned_project_and_unique_volumes(self):
        project = ci.project_name(self.env)
        marker = {"project": project, "root": str(self.root)}
        ci.write_new(self.root / "deployment/.ci-owner.json", marker)
        ci.write_new(self.root / "deployment/compose.ci.generated.json", ci.compose_override(project))
        call = Mock()
        ci.cleanup(self.root, project, call)
        argv = call.call_args.args[0]
        self.assertEqual(argv[-3:], ["down", "--volumes", "--remove-orphans"])
        self.assertEqual(argv[argv.index("--project-name") + 1], project)
        override_path = self.root / "deployment/compose.ci.generated.json"
        override = json.loads(override_path.read_text())
        self.assertTrue(all(row["name"].startswith(project + "-") for row in override["volumes"].values()))
        override["volumes"]["grafana-data"]["name"] = "analyst-desk-monitoring_grafana-data"
        override_path.write_text(json.dumps(override))
        call.reset_mock()
        with self.assertRaises(ValueError):
            ci.cleanup(self.root, project, call)
        call.assert_not_called()

    def test_changed_ownership_marker_prevents_cleanup(self):
        project = ci.project_name(self.env)
        ci.write_new(self.root / "deployment/.ci-owner.json", {"project": "analyst-desk", "root": str(self.root)})
        call = Mock()
        with self.assertRaises(ValueError):
            ci.cleanup(self.root, project, call)
        call.assert_not_called()

    def test_every_service_must_be_running_and_healthchecked_where_available(self):
        rows = [{"Service": name, "State": "running", "Health": "" if name == "qdrant" else "healthy"}
                for name in sorted(ci.SERVICES)]
        self.assertEqual(set(ci.verify_container_states(json.dumps(rows))), ci.SERVICES)
        self.assertEqual(set(ci.verify_container_states("\n".join(json.dumps(row) for row in rows))), ci.SERVICES)
        for bad in (rows[:-1], rows + [rows[0]], [{**row, "State": "exited"} for row in rows],
                    [{**row, "Health": "starting"} for row in rows]):
            with self.assertRaises(ValueError):
                ci.verify_container_states(json.dumps(bad))

    def test_exclusive_write_preserves_prior_file(self):
        path = self.root / "old.json"
        ci.write_new(path, {"old": True})
        with self.assertRaises(FileExistsError):
            ci.write_new(path, {"new": True})
        self.assertEqual(json.loads(path.read_text()), {"old": True})

    def test_gateway_liveness_does_not_skip_qdrant_readiness(self):
        import httpx
        client = Mock()
        client.get.side_effect = [httpx.ConnectError("starting"),
                                  Mock(status_code=200, json=lambda: {"status": "ok"})]
        context = Mock()
        context.__enter__ = Mock(return_value=client)
        context.__exit__ = Mock(return_value=False)
        with patch("httpx.Client", return_value=context) as factory, patch.object(ci.time, "sleep"):
            result = ci.wait_qdrant({"QDRANT_API_KEY": "ephemeral-test-key"})
        self.assertEqual(client.get.call_count, 2)
        factory.assert_called_once_with(trust_env=False, timeout=5)
        self.assertTrue(result["ready"])
        self.assertEqual(client.get.call_args.args[0], "http://127.0.0.1:6333/collections")
        self.assertNotIn("ephemeral-test-key", json.dumps(result))

    def test_synthetic_fixture_replays_using_production_parser(self):
        import monitoring.exporter
        source = Path(monitoring.exporter.__file__).parent / "tests/fixtures/google_meta_saved_snapshot.json"
        destination = self.root / "monitoring/tests/fixtures/google_meta_saved_snapshot.json"
        destination.parent.mkdir(parents=True)
        shutil.copyfile(source, destination)
        with patch("urllib.request.urlopen", side_effect=AssertionError("No SEC network allowed")):
            report = ci.prepare_fixture(self.root)
        self.assertEqual(report["company_years"], 4)
        self.assertTrue(report["corpus_replay_verified"])
        self.assertEqual((self.root / "monitoring/data").stat().st_mode & 0o777, 0o755)
        from financial_data import get_annual_financial_fact
        result = get_annual_financial_fact("MSFT", "net_income", 2025, data_dir=self.root / "data/raw")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(str(result["value"]), "250")

    def test_idle_stream_seed_does_not_execute_models_or_tools(self):
        from research_app import ResearchController, create_app
        from fastapi.testclient import TestClient
        folder = self.root / "data/workflow_final"
        folder.mkdir(parents=True)
        ci.seed_idle_stream(self.root)
        model = Mock(side_effect=AssertionError("No model permitted"))
        backend = Mock()
        control = ResearchController(folder, model_factory=model, backend_factory=backend, key_loader=lambda: {})
        with TestClient(create_app(control, token_loader=lambda: "t" * 40, allowed_hosts=["testserver"])) as client:
            response = client.get(f"/api/runs/{ci.RUN_ID}/events", headers={"Authorization": "Bearer " + "t" * 40})
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: status\n", response.text)
        self.assertIn("event: idle\n", response.text)
        state = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ")))
        self.assertEqual(state["status"], "not_started")
        self.assertTrue(all(event["kind"] == "background_snapshot" for event in state["events"]))
        self.assertEqual(state["metrics"]["llm_requests_started"], 0)
        self.assertEqual(state["budget"]["tool_calls"], 0)
        self.assertIsNone(state["publication"])
        model.assert_not_called()
        # Constructing the broker does not call its backend.
        backend.return_value.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
