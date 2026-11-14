import copy
import importlib.util
import hashlib
import os
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile, ZIP_DEFLATED

MODULE_PATH = Path(__file__).resolve().parents[1] / 'exporter.py'
spec = importlib.util.spec_from_file_location('snapshot_exporter', MODULE_PATH)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


FIXTURE_PATH = Path(__file__).resolve().parent / 'fixtures/google_meta_saved_snapshot.json'
FIXTURE_SHA256 = 'c7e9856e997c1d9b6f09329434d3571033467f7bd76343efb99fbddb45f428d5'


def fixture_zip():
    # The private full publication archive is optional and must be selected
    # explicitly. Never discover unrelated files outside the checkout.
    value = os.environ.get('ANALYST_DESK_TEST_ARCHIVE')
    if value is None:
        return None
    if not value.strip():
        raise ValueError('ANALYST_DESK_TEST_ARCHIVE must identify an existing archive.')
    path = Path(value).expanduser().absolute()
    exporter.safe_read(path, exporter.MAX_ARCHIVE_BYTES)
    return path


def fixture_snapshot():
    # CI and developer tests always use the same committed sanitized evidence.
    # Missing, altered or invalid fixtures must fail rather than skip coverage.
    raw = exporter.safe_read(FIXTURE_PATH)
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise ValueError('The committed monitoring snapshot fixture changed.')
    return exporter.validate_snapshot(exporter.parse_json(raw))


def rehash(value):
    value['integrity_sha256'] = exporter.digest({k:v for k,v in value.items() if k != 'integrity_sha256'})
    return value


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = fixture_snapshot()

    def test_actual_totals_and_single_lineage(self):
        s = self.snapshot
        self.assertEqual((s['metrics']['llm_requests'], s['metrics']['reported_tokens'], s['metrics']['reserved_tokens']), (31,133710,638420))
        self.assertEqual(s['metrics']['reported_cost_usd'], '0.0634176')
        self.assertEqual(s['metrics']['published_notes'], 1)
        self.assertEqual(s['lineage'], {'prior_events':220,'total_events':252,'new_native_tools':16,'new_llm_requests':0,'preserved_prior_records':60})
        self.assertEqual(s['metrics']['event_counts']['agent_correction'],9)
        self.assertEqual(s['metrics']['event_errors']['agent_response_limit'],1)
        self.assertEqual(s['metrics']['critic_rejections'],0)

    def test_unknown_or_changed_snapshot_fields_rejected(self):
        for change in ('private', 'classification', 'hash'):
            s=copy.deepcopy(self.snapshot)
            if change=='private': s['raw_prompt']='secret'
            if change=='classification': s['evidence_class']='held_out'
            if change=='hash': s['note_content_sha256']='0'*64
            with self.subTest(change=change), self.assertRaises(ValueError): exporter.validate_snapshot(rehash(s))

    def test_rehashed_aggregate_tampering_rejected(self):
        edits=[('reported_cost_usd','9'),('critic_rejections',99),('model_response_cache_hits',5),('unpaired_llm_requests',1),('unpaired_tool_requests',1),('reported_tokens',0)]
        for key,value in edits:
            s=copy.deepcopy(self.snapshot);s['metrics'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): exporter.validate_snapshot(rehash(s))
        s=copy.deepcopy(self.snapshot);s['metrics']['tool_stats'][0]['p95_seconds']=999
        with self.assertRaises(ValueError):exporter.validate_snapshot(rehash(s))

    def test_incomplete_cost_is_unavailable_and_not_zero(self):
        s=copy.deepcopy(self.snapshot)
        next(e for e in s['events'] if e['kind']=='llm_end').pop('reported_cost_usd')
        s['metrics']['cost_complete']=False;s['metrics']['reported_cost_usd']=None
        text=exporter.metrics_text(rehash(s),now=s['snapshot_timestamp']+1)
        self.assertNotIn('analyst_desk_reported_cost_usd{',text)
        self.assertIn('analyst_desk_cost_complete{dataset="google_meta_sequence25",evidence_class="assisted_development",telemetry_kind="saved_snapshot"} 0',text)
        s['metrics']['reported_cost_usd']='0'
        with self.assertRaises(ValueError):exporter.validate_snapshot(rehash(s))

    def test_zero_denominator_has_no_share(self):
        s=copy.deepcopy(self.snapshot)
        s['metrics'].update(financial_figures=0,verified_financial_figures=0,verified_financial_share=None)
        text=exporter.metrics_text(rehash(s),now=s['snapshot_timestamp'])
        self.assertNotIn('analyst_desk_verified_financial_share{',text)
        s['metrics']['verified_financial_share']=1.0
        with self.assertRaises(ValueError):exporter.validate_snapshot(rehash(s))

    def test_only_safe_timeline_fields_exported(self):
        text=exporter.canonical(self.snapshot)
        for forbidden in ('reviewer','body_markdown','question','prompt_sha256','response_sha256','approval_token','Authorization','source_folder','responses','evidence_ids'):
            self.assertNotIn('"'+forbidden+'"',text)
        self.assertNotIn('Customers may',text)
        self.assertEqual(self.snapshot['limitations'],exporter.LIMITATIONS)

    def test_cache_availability_and_successful_service_latency(self):
        text=exporter.metrics_text(self.snapshot,now=self.snapshot['snapshot_timestamp'])
        self.assertIn('metric="edgar_cache_hits"',text)
        self.assertIn('service="edgar",stat="p95",telemetry_kind="saved_snapshot",tool="__all__"',text)
        self.assertNotIn('analyst_desk_edgar_cache_hits{',text)

    def test_probe_separate_expiring_and_default_off(self):
        now=self.snapshot['snapshot_timestamp']+100
        off=exporter.metrics_text(self.snapshot,now=now)
        self.assertNotIn('synthetic_alert_probe',off)
        probe={'enabled':True,'verified':97,'total':100,'expires_at':now+60}
        text=exporter.metrics_text(self.snapshot,probe,now=now)
        probe_lines=[x for x in text.splitlines() if 'synthetic_alert_probe' in x]
        self.assertEqual(len(probe_lines),3)
        self.assertTrue(any(x.endswith(' 0.97') for x in probe_lines))
        self.assertFalse(any('llm_' in x or 'cost_' in x for x in probe_lines))
        self.assertNotIn('synthetic_alert_probe',exporter.metrics_text(self.snapshot,probe,now=now+61))
        self.assertEqual(off,exporter.metrics_text(self.snapshot,now=now))

    def test_probe_strict_invalid_values_rejected(self):
        now=self.snapshot['snapshot_timestamp']
        base={'enabled':True,'verified':97,'total':100,'expires_at':now+10}
        bad=[{**base,'expires_at':now+601},{**base,'expires_at':float('nan')},{**base,'expires_at':None},{**base,'verified':101},{**base,'total':0},{**base,'enabled':1},{**base,'comment':'secret'}]
        for probe in bad:
            with self.subTest(probe=probe),self.assertRaises(ValueError):exporter.metrics_text(self.snapshot,probe,now=now)

    def test_json_duplicates_nonfinite_and_symlinks_rejected(self):
        for text in (b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}'):
            with self.assertRaises(ValueError):exporter.parse_json(text)
        with tempfile.TemporaryDirectory() as folder:
            real=Path(folder)/'real.json';real.write_text('{}')
            link=Path(folder)/'link.json';link.symlink_to(real)
            with self.assertRaises(ValueError):exporter.safe_read(link)

    def test_missing_usage_or_forged_cost_rejected_by_import_derivation(self):
        path=fixture_zip()
        if path is None:self.skipTest('Optional private archive test; set ANALYST_DESK_TEST_ARCHIVE explicitly')
        with ZipFile(path) as z:case=json.loads(z.read(exporter.RUN_ID+'/Case_State_And_Evidence.json'))
        e=copy.deepcopy(case['execution'])
        next(x for x in e['events'] if x['kind']=='llm_end')['details']['usage'].pop('total_tokens')
        with self.assertRaises(ValueError):exporter.derive_metrics(e,case['state']['verification'])
        e=copy.deepcopy(case['execution']);e['metrics']['reported_cost_usd']='0'
        with self.assertRaises(ValueError):exporter.derive_metrics(e,case['state']['verification'])
        e=copy.deepcopy(case['execution'])
        next(x for x in e['events'] if x['kind']=='llm_end')['details']['usage']['cost']=None
        e['metrics']['reported_cost_usd']=None;e['metrics']['cost_complete']=False
        metrics,_=exporter.derive_metrics(e,case['state']['verification'])
        self.assertIsNone(metrics['reported_cost_usd'])

    def test_zip_tampering_duplicate_and_unsafe_members_rejected(self):
        source=fixture_zip()
        if source is None:self.skipTest('Optional private archive test; set ANALYST_DESK_TEST_ARCHIVE explicitly')
        with ZipFile(source) as z:raw={n:z.read(n) for n in z.namelist()}
        with tempfile.TemporaryDirectory() as folder:
            for mutation in ('note','decision','baseline','traversal','duplicate'):
                data=copy.deepcopy(raw)
                if mutation=='note':
                    key=exporter.RUN_ID+'/Published_Note.json';obj=json.loads(data[key]);obj['title']='changed';data[key]=json.dumps(obj).encode()
                if mutation=='decision':
                    key='Operator_Publication_Decision.json';obj=json.loads(data[key]);obj['operator_approval']['reviewer']='other';data[key]=json.dumps(obj).encode()
                if mutation=='baseline':data['Baseline_Case_State_And_Evidence.json']=b'{}'
                if mutation=='traversal':data['../escape.txt']=b'x'
                target=Path(folder)/(mutation+'.zip')
                with ZipFile(target,'w',ZIP_DEFLATED) as z:
                    for name,value in data.items():z.writestr(name,value)
                    if mutation=='duplicate':
                        import warnings
                        with warnings.catch_warnings():
                            warnings.simplefilter('ignore');z.writestr('Operator_Publication_Decision.json',data['Operator_Publication_Decision.json'])
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):exporter.build_snapshot(target)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.snapshot=fixture_snapshot();self.temp=tempfile.TemporaryDirectory();self.data=Path(self.temp.name)
        (self.data/'snapshot.json').write_text(exporter.canonical(self.snapshot))
        self.server=exporter.make_server(self.data,port=0)
        self.thread=threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.01),daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.temp.cleanup()
    def request(self,path,method='GET'):
        request=Request(self.url+path,method=method)
        try:
            with urlopen(request,timeout=2) as response:return response.status,response.read(),response.headers
        except HTTPError as exc:return exc.code,exc.read(),exc.headers
    def test_routes_read_only_and_exact_snapshot(self):
        status,body,headers=self.request('/snapshot');self.assertEqual(status,200);self.assertEqual(json.loads(body),self.snapshot)
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(json.loads(self.request('/events')[1]),self.snapshot['events'])
        self.assertEqual(json.loads(self.request('/health')[1])['probe_active'],False)
        self.assertIn(b'not live agent telemetry',self.request('/')[1])
        self.assertEqual(self.request('/snapshot?file=secrets')[0],404)
        self.assertEqual(self.request('/../snapshot.json')[0],404)
        for method in ('POST','PUT','PATCH','DELETE','OPTIONS','TRACE','HEAD'):
            self.assertEqual(self.request('/snapshot',method)[0],405)
        self.assertEqual(exporter.read_snapshot(self.data),self.snapshot)
    def test_missing_or_changed_snapshot_returns_unavailable(self):
        (self.data/'snapshot.json').write_text('{}')
        for path in ('/health','/metrics','/snapshot','/events','/'):
            status,body,_=self.request(path);self.assertEqual(status,503);self.assertEqual(json.loads(body),{'status':'unavailable'})
    def test_expired_probe_inactive_and_missing_cost_omitted(self):
        probe={'enabled':True,'verified':97,'total':100,'expires_at':time.time()-1}
        (self.data/'alert_probe.json').write_text(json.dumps(probe))
        self.assertFalse(json.loads(self.request('/health')[1])['probe_active'])
        self.assertNotIn(b'synthetic_alert_probe',self.request('/metrics')[1])
        probe['expires_at']=time.time()+30;(self.data/'alert_probe.json').write_text(json.dumps(probe))
        self.assertTrue(json.loads(self.request('/health')[1])['probe_active'])
        self.assertIn(b'synthetic_alert_probe',self.request('/metrics')[1])


if __name__=='__main__':unittest.main()
