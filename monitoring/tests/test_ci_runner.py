"""Mocked regressions for promtool's isolated temporary storage and safe failures.

These tests inspect the actual helper's Docker invocation and reporting. They do
not start Docker or establish that the container fix passed on a live engine.
"""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('monitoring_ci_under_test', ROOT / 'run_ci_checks.py')
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)
CANARY = 'PRIVATE_CANARY_DO_NOT_EXPORT_92475'


def result(status='passed', text='', **changes):
    record = {'status': status, 'exit_code': 0 if status == 'passed' else 1,
              'timed_out': False, 'output_limit_exceeded': False, 'seconds': 0.01,
              'output_bytes': len(text.encode()), 'output_sha256': hashlib.sha256(text.encode()).hexdigest()}
    record.update(changes)
    return record, text


def option_values(arguments, option):
    return [arguments[index + 1] for index, value in enumerate(arguments[:-1]) if value == option]


class CIRunnerTests(unittest.TestCase):
    def setUp(self):
        self.prefix = ['docker', '--host', 'unix:///var/run/docker.sock']

    def test_each_promtool_command_has_only_bounded_writable_tmp_and_readonly_configs(self):
        calls = []
        def capture(arguments, cwd, **kwargs):
            calls.append(list(arguments))
            return result()
        with patch.object(ci, 'capture_command', side_effect=capture), patch.object(ci, 'cleanup_owned_container') as cleanup:
            records = ci.promtool_checks(ROOT, self.prefix)
        self.assertEqual(len(calls), 3)
        self.assertEqual([row['name'] for row in records], ['promtool_config', 'promtool_rules', 'promtool_rule_tests'])
        cleanup.assert_not_called()
        expected_files = {'prometheus.yml', 'alerts.yml', 'alert_tests.yml'}
        for command, record in zip(calls, records):
            with self.subTest(check=record['name']):
                self.assertEqual(command[:len(self.prefix)], self.prefix)
                self.assertIn('--read-only', command)
                self.assertEqual(option_values(command, '--network'), ['none'])
                self.assertEqual(option_values(command, '--env'), ['TMPDIR=/tmp'])
                tmpfs = option_values(command, '--tmpfs')
                self.assertEqual(len(tmpfs), 1)
                target, options = tmpfs[0].split(':', 1)
                self.assertEqual(target, '/tmp')
                parts = set(options.split(','))
                self.assertTrue({'rw', 'noexec', 'nosuid', 'nodev', 'mode=1777'} <= parts)
                sizes = [part for part in parts if part.startswith('size=')]
                self.assertEqual(len(sizes), 1)
                size = re.fullmatch(r'size=(\d+)m', sizes[0])
                self.assertIsNotNone(size)
                self.assertGreater(int(size.group(1)), 0)
                self.assertLessEqual(int(size.group(1)), 64, 'Temporary test storage must remain bounded.')
                mounts = option_values(command, '--mount')
                self.assertEqual(len(mounts), 3)
                self.assertEqual({mount.rsplit('/etc/prometheus/', 1)[1].split(',', 1)[0] for mount in mounts}, expected_files)
                for mount in mounts:
                    parts = mount.split(',')
                    self.assertIn('type=bind', parts)
                    self.assertIn('readonly', parts)
                    source = next(part.removeprefix('source=') for part in parts if part.startswith('source='))
                    self.assertEqual(Path(source).parent, ROOT / 'prometheus')
                    self.assertIn(Path(source).name, expected_files)
                self.assertNotIn('--volume', command)
                self.assertNotIn('-v', command)
                self.assertEqual(record['container_tmpfs'], tmpfs[0])
                self.assertEqual(record['container_tmpdir'], '/tmp')
                self.assertEqual(record['status'], 'passed')

    def test_failure_diagnostics_classify_storage_error_without_echoing_raw_text(self):
        text = (f'error creating temporary directory /tmp/{CANARY}: read-only file system\n'
                f'Authorization: Bearer {CANARY}\n/home/private/{CANARY}\n')
        record, _ = result('failed', text)
        diagnostic = ci.promtool_failure_diagnostics(text, record)
        self.assertTrue({'read_only_filesystem', 'temporary_storage'} <= set(diagnostic['categories']))
        self.assertEqual(set(diagnostic), {'categories', 'rule_tests_mentioned'})
        self.assertEqual(diagnostic['rule_tests_mentioned'], [])
        self.assertNotIn(CANARY, json.dumps(diagnostic))
        unknown = ci.promtool_failure_diagnostics(CANARY, record)
        self.assertEqual(unknown['categories'], ['unknown_failure'])
        self.assertNotIn(CANARY, json.dumps(unknown))

    def test_failed_promtool_check_stops_remaining_commands_and_retains_failure(self):
        text = f'permission denied while creating temporary storage /tmp/{CANARY}'
        failed, _ = result('failed', text)
        with patch.object(ci, 'capture_command', return_value=(copy.deepcopy(failed), text)) as capture, \
             patch.object(ci, 'cleanup_owned_container', return_value={'attempted': False, 'reason': 'no_owned_container_id'}) as cleanup:
            records = ci.promtool_checks(ROOT, self.prefix)
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(cleanup.call_count, 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['status'], 'failed')
        self.assertEqual(records[0]['exit_code'], 1)
        self.assertNotIn(CANARY, json.dumps(records))
        self.assertEqual(records[0]['output_sha256'], failed['output_sha256'])

    def test_main_reports_rule_test_failure_and_nonzero_exit_without_raw_output(self):
        unittest_output = 'Ran 22 tests in 0.1s\n\nOK (skipped=2)\n'
        failure = f'FAILED: rule test; unexpected error {CANARY}'
        responses = [result(text=unittest_output), result(), result(), result('failed', failure)]
        output = io.StringIO()
        with patch.object(ci, 'capture_command', side_effect=responses), \
             patch.object(ci, 'resolve_local_docker', return_value=(self.prefix, {'local_verified': True})), \
             patch.object(ci, 'cleanup_owned_container', return_value={'attempted': False, 'reason': 'no_owned_container_id'}), \
             contextlib.redirect_stdout(output):
            exit_code = ci.main([])
        report = json.loads(output.getvalue())
        self.assertNotEqual(exit_code, 0)
        self.assertFalse(report['overall_passed'])
        self.assertEqual([row['status'] for row in report['steps']], ['passed', 'passed', 'passed', 'failed'])
        self.assertEqual(report['steps'][-1]['name'], 'promtool_rule_tests')
        self.assertEqual(report['steps'][-1]['exit_code'], 1)
        self.assertNotIn(CANARY, output.getvalue())
        self.assertFalse(report['running_compose_stack_changed'])
        self.assertEqual(report['new_model_calls'], 0)


if __name__ == '__main__':
    unittest.main()
