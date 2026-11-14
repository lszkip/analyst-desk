"""Static regressions for the small committed monitoring stack.

These checks enforce isolation and measurement contracts in the existing config
shape. Promtool validates Prometheus configuration and rule behavior; these static
checks do not replace a live Compose/dashboard run. These stdlib tests do not
claim to be general YAML or PromQL parsers.
"""
import json
from pathlib import Path
import re
import unittest

from test_exporter import exporter, fixture_snapshot

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text(encoding='utf-8')


def service_blocks(compose):
    match = re.search(r'^services:\s*\n(.*?)^networks:\s*\n', compose, re.M | re.S)
    if not match:
        raise ValueError('Expected explicit services and networks sections.')
    body = match.group(1)
    headings = list(re.finditer(r'^  ([a-z][a-z0-9-]*):\s*\n', body, re.M))
    if not headings:
        raise ValueError('No service definitions found.')
    return {heading.group(1): body[heading.end():headings[i+1].start() if i+1 < len(headings) else len(body)]
            for i, heading in enumerate(headings)}


class StackContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(read('STACK_CONTRACT.json'))
        cls.compose = read('compose.yaml')
        cls.services = service_blocks(cls.compose)
        cls.dashboard = json.loads(read('grafana/dashboards/analyst-desk-saved-runs.json'))

    def test_dashboard_queries_keep_real_and_synthetic_evidence_separate(self):
        required = {'dataset': exporter.DATASET, 'evidence_class': exporter.CLASSIFICATION,
                    'telemetry_kind': exporter.TELEMETRY_KIND}
        observed = set()
        for panel in self.dashboard['panels']:
            for target in panel.get('targets', []):
                expression = target.get('expr', '')
                self.assertIsInstance(expression, str)
                self.assertTrue(expression.strip(), 'Empty metric query can silently remove a panel.')
                self.assertNotIn('synthetic_alert_probe', expression)
                self.assertNotRegex(expression, r'\bvector\s*\(\s*0\s*\)', 'Missing data must not become a healthy zero.')
                references = list(re.finditer(r'\b(analyst_desk_[a-z_]+)(\{[^}]*\})?', expression))
                self.assertTrue(references, 'Expected an Analyst Desk evidence metric.')
                for reference in references:
                    observed.add(reference.group(1))
                    labels = reference.group(2) or ''
                    for key, value in required.items():
                        self.assertRegex(labels, rf'(?:\{{|,)\s*{key}\s*=\s*"{re.escape(value)}"\s*(?:,|\}})',
                                         'Every metric selector needs the saved assisted dataset scope.')
        self.assertTrue({'analyst_desk_reported_cost_usd', 'analyst_desk_reported_tokens',
                         'analyst_desk_verified_financial_share', 'analyst_desk_event_errors',
                         'analyst_desk_snapshot_age_seconds', 'analyst_desk_metric_available'} <= observed)
        self.assertFalse(self.dashboard['editable'])
        self.assertEqual(self.dashboard['uid'], self.contract['grafana']['dashboard_uid'])

    def test_gateway_is_the_only_loopback_port_publisher(self):
        self.assertEqual(set(self.services), set(self.contract['services']))
        for service, body in self.services.items():
            publishes = re.search(r'^    ports:\s*$', body, re.M)
            self.assertEqual(bool(publishes), service == 'monitoring-gateway')
        published = re.findall(r'^      - "(127\.0\.0\.1:\d+:\d+)"\s*$', self.services['monitoring-gateway'], re.M)
        expected = {f'127.0.0.1:{port}:{port}' for port in self.contract['ports'].values()}
        self.assertEqual(set(published), expected)
        # There must be no other short-form host binding, including 0.0.0.0.
        candidates = re.findall(r'^      - [\'"]?([^\n]+?:\d+:\d+)[\'"]?\s*$', self.compose, re.M)
        self.assertEqual(len(candidates), len(expected))
        self.assertNotRegex(self.compose, r'(?m)^\s*(?:network_mode|pid|ipc):\s*host\s*$')
        self.assertNotRegex(self.compose, r'(?m)^\s*privileged:\s*true\s*$')
        for name, body in self.services.items():
            networks = re.search(r'^    networks:\s*\[([^]]+)\]\s*$', body, re.M)
            self.assertIsNotNone(networks)
            actual = {x.strip() for x in networks.group(1).split(',')}
            self.assertEqual(actual, {'monitoring-internal', 'monitoring-access'} if name == 'monitoring-gateway' else {'monitoring-internal'})
        self.assertRegex(self.compose, r'(?m)^  monitoring-internal:\s*\n    internal: true\s*$')

    def test_mounts_do_not_expand_to_private_project_data_or_credentials(self):
        sources = re.findall(r'^        source:\s*(\S+)\s*$', self.compose, re.M)
        self.assertCountEqual(sources, ['./data', './prometheus', './grafana/provisioning',
                                        './grafana/dashboards', './secrets/grafana_admin_password', './gateway.conf'])
        self.assertEqual(len(re.findall(r'^        read_only:\s*true\s*$', self.compose, re.M)), len(sources))
        self.assertEqual(len(re.findall(r'^          create_host_path:\s*false\s*$', self.compose, re.M)), len(sources))
        for name, body in self.services.items():
            self.assertRegex(body, r'(?m)^    <<:\s*\*restrictions\s*$')
            self.assertNotRegex(body, r'(?m)^    (?:cap_add|env_file|devices):')
            self.assertNotRegex(body, r'(?m)^    read_only:\s*false\s*$')
        self.assertRegex(self.compose, r'(?m)^  read_only:\s*true\s*$')
        self.assertRegex(self.compose, r'(?m)^  cap_drop:\s*\[ALL\]\s*$')
        self.assertNotRegex(self.compose, r'(?i)(docker\.sock|OPENROUTER|PORTAL_(?:READ|WRITE|REVIEW|PUBLISH)_TOKEN|private_state|checkpoints\.sqlite|journal\.sqlite|env_file)')
        self.assertFalse(self.contract['data']['original_private_state_mounted'])
        self.assertFalse(self.contract['secret']['include_in_results'])

    def test_telemetry_destinations_are_internal_and_no_external_notifications_exist(self):
        prometheus = read('prometheus/prometheus.yml')
        datasource = read('grafana/provisioning/datasources/prometheus.yaml')
        targets = re.findall(r'^\s*- targets:\s*\[(.*?)\]\s*$', prometheus, re.M)
        self.assertEqual(targets, ['"exporter:8085"'])
        self.assertNotRegex(prometheus, r'(?m)^\s*(?:remote_write|remote_read|alerting|(?:http|file|dns|consul|kubernetes)_sd_configs|proxy_url):')
        self.assertRegex(datasource, r'(?m)^    url:\s*http://prometheus:9090\s*$')
        self.assertRegex(datasource, r'(?m)^    access:\s*proxy\s*$')
        self.assertRegex(datasource, r'(?m)^    editable:\s*false\s*$')
        self.assertFalse(self.contract['alert']['external_notifications'])
        self.assertNotRegex(datasource, r'(?im)^\s*(?:basicAuth|basicAuthUser|secureJsonData|password|oauthPassThru):')
        self.assertRegex(prometheus, r'(?m)^    sample_limit:\s*2000\s*$')

    def test_grafana_viewer_and_hidden_version_do_not_require_health_body_version(self):
        grafana = self.services['grafana']
        required = {'GF_AUTH_ANONYMOUS_ENABLED': 'true', 'GF_AUTH_ANONYMOUS_ORG_ROLE': 'Viewer',
                    'GF_AUTH_ANONYMOUS_HIDE_VERSION': 'true', 'GF_AUTH_BASIC_ENABLED': 'false',
                    'GF_USERS_ALLOW_SIGN_UP': 'false', 'GF_AUTH_DISABLE_LOGIN_FORM': 'true',
                    'GF_ANALYTICS_REPORTING_ENABLED': 'false', 'GF_SNAPSHOTS_EXTERNAL_ENABLED': 'false'}
        for key, value in required.items():
            self.assertRegex(grafana, rf'(?m)^      {key}:\s*[\'"]?{re.escape(value)}[\'"]?\s*$')
        version = self.contract['grafana']['version']
        self.assertEqual(self.contract['images']['grafana'], 'grafana/grafana:' + version)
        self.assertRegex(grafana, rf'(?m)^    image:\s*grafana/grafana:{re.escape(version)}\s*$')
        test_line = re.search(r'^      test:\s*(\[[^\n]+\])\s*$', grafana, re.M)
        self.assertIsNotNone(test_line)
        command = json.loads(test_line.group(1))
        self.assertIn('http://127.0.0.1:3000/api/health', command)
        self.assertNotIn('version', ' '.join(command).lower())
        self.assertIn('/dev/null', command, 'Container health checks should use HTTP status, not an intentionally hidden version.')

    def test_gateway_denies_management_writes_and_strips_authorization(self):
        gateway = read('gateway.conf')
        self.assertRegex(gateway, r'proxy_set_header\s+Authorization\s+"";')
        self.assertRegex(gateway, r'access_log\s+off;')
        clauses = re.findall(r'limit_except\s+([^{};]+)\s*\{\s*deny all;\s*\}', gateway)
        self.assertEqual(sum('POST' in clause.split() for clause in clauses), 1)
        self.assertTrue(all(set(clause.split()) <= {'GET', 'POST'} for clause in clauses))
        query_block = re.search(r'location\s*=\s*/api/ds/query\s*\{(.*?)\n        \}', gateway, re.S)
        self.assertIsNotNone(query_block)
        self.assertRegex(query_block.group(1), r'limit_except\s+GET POST\s*\{\s*deny all;\s*\}')
        self.assertEqual(len(re.findall(r'location / \{\s*limit_except GET \{ deny all; \}', gateway)), 3)

    def test_alert_metric_names_exist_in_exported_snapshot(self):
        # promtool exercises the threshold, empty-denominator and resolution
        # semantics. This separate integration check catches metric renames.
        emitted = exporter.metrics_text(fixture_snapshot())
        metric_names = {line.split('{', 1)[0] for line in emitted.splitlines() if line and not line.startswith('#')}
        referenced = set(re.findall(r'\banalyst_desk_[a-z_]+\b', read('prometheus/alerts.yml')))
        self.assertTrue(referenced <= metric_names, referenced - metric_names)
        self.assertIn(self.contract['alert']['share_metric'], metric_names)
        self.assertIn(self.contract['alert']['denominator_metric'], metric_names)
        self.assertEqual(self.contract['alert']['real_dataset'], exporter.DATASET)
        self.assertEqual(self.contract['alert']['probe_dataset'], 'synthetic_alert_probe')


if __name__ == '__main__':
    unittest.main()
