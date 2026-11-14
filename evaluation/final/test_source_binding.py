"""Exact reviewed source binding regressions using synthetic public SEC records."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from evaluation.final import source_binding as sb
from workflow_contracts import WorkflowError, digest


def raw(value, indent=None):
    return json.dumps(value, indent=indent, ensure_ascii=False, sort_keys=True, separators=None if indent else (',',':'),allow_nan=False).encode()


def sha(value): return hashlib.sha256(value).hexdigest()


class SourceBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.repo=self.root/'repo';(self.repo/'data/raw').mkdir(parents=True)
        self.binding=self.root/'private/Source_Binding.json';self.certificate=self.root/'proof.json'
        self.cases=[{'case_id':c,'request':{'tickers':['MSFT'],'years':[2025]}} for c in ['F002','F003']]
        values={'companyfacts':{'cik':789019,'facts':{'OperatingIncomeLoss':25,'Revenues':100}},
                'filings':{'cik':789019,'complete':True,'prepared_at':'original',
                'annual_filings':[{'accessionNumber':'0000950170-25-100235','filingDate':'2025-07-30','form':'10-K','reportDate':'2025-06-30'}],
                'source_snapshots':[{'sha256':'1'*64}]}}
        self.manifest={'source_hashes':{},'authoring_code_sha256':'c'*64};self.old={};self.current={};rows=[]
        for kind,value in values.items():
            name='data/raw/MSFT_'+kind+'.json';original=raw(value)
            current_value=deepcopy(value)
            if kind=='filings':current_value['prepared_at']='reviewed current'
            current=raw(current_value,indent=2)
            self.old[name]=original;self.current[name]=current;(self.repo/name).write_bytes(current)
            self.manifest['source_hashes']['sec_source_snapshot/'+Path(name).name]=sha(original)
            projected=deepcopy(value)
            if kind=='filings':projected.pop('prepared_at')
            rows.append({'relative_path':name,'ticker':'MSFT','kind':kind,'frozen_raw_sha256':sha(original),
                'current_raw_sha256':sha(current),'canonical_sha256':sha(raw(projected)),
                'ignored_root_fields':['prepared_at'] if kind=='filings' else []})
        fact={'status':'ok','value':'100','filed':'2025-07-30','filing_identifiers':['0000950170-25-100235'],
              'source_url':'https://www.sec.gov/Archives/fixture','snapshot_sha256':sha(self.old['data/raw/MSFT_companyfacts.json']),
              'filings_snapshot_sha256':sha(self.old['data/raw/MSFT_filings.json'])}
        self.gold=[{'case_id':c['case_id'],'numeric_targets':[{'ticker':'MSFT','fiscal_year':2025,'revenue':fact,
                    'operating_income':{**fact,'value':'25'},'margin':{'value':'25.00'}}]} for c in self.cases]
        proof={'schema_version':'verified-source-equivalence-v1','frozen_bundle_sha256':'a'*64,
            'original_freeze_manifest_sha256':'b'*64,'frozen_authoring_code_sha256':'c'*64,
            'pilot_case_ids':['F002','F003'],'files':rows,'outcome_equivalence':{'company_year_pairs':1,'facts':2,'margins':1,
            'all_equal_except_snapshot_fields':True,'ignored_fact_fields':sorted(sb.HASH_FIELDS)}}
        self.certificate.write_bytes(raw(proof));self.proof_sha=sha(self.certificate.read_bytes())

    def bind(self, **kwargs):
        return sb.bind_sources(self.repo,self.manifest,self.cases,self.gold,self.certificate,self.binding,
            frozen_bundle_sha256='a'*64,original_freeze_manifest_sha256='b'*64,
            expected_certificate_sha256=self.proof_sha,**kwargs)

    def test_only_two_hash_fields_are_rebound_without_mutating_gold_or_cache(self):
        original=deepcopy(self.gold);before={name:(self.repo/name).read_bytes() for name in self.current}
        bound,report=self.bind()
        expected=deepcopy(original)
        for case in expected:
            for target in case['numeric_targets']:
                for metric in ['revenue','operating_income']:
                    target[metric]['snapshot_sha256']=sha(self.current['data/raw/MSFT_companyfacts.json'])
                    target[metric]['filings_snapshot_sha256']=sha(self.current['data/raw/MSFT_filings.json'])
        self.assertEqual(bound,expected);self.assertEqual(self.gold,original)
        self.assertEqual(before,{name:(self.repo/name).read_bytes() for name in self.current})
        self.assertTrue(report['expected_answers_unchanged']);self.assertEqual(report['original_gold_sha256'],digest(original))
        self.assertNotEqual(report['original_gold_sha256'],report['bound_gold_sha256'])

    def test_changed_fact_value_is_rejected_without_overwrite(self):
        path=self.repo/'data/raw/MSFT_companyfacts.json';value=json.loads(path.read_bytes());value['facts']['Revenues']=999;path.write_bytes(raw(value))
        changed=path.read_bytes()
        with self.assertRaises(WorkflowError) as caught:self.bind()
        self.assertEqual(caught.exception.code,'frozen_source_mismatch');self.assertEqual(path.read_bytes(),changed);self.assertFalse(self.binding.exists())

    def test_changed_filing_and_nested_snapshot_are_rejected(self):
        path=self.repo/'data/raw/MSFT_filings.json'
        for field in ['annual_filings','source_snapshots']:
            value=json.loads(self.current['data/raw/MSFT_filings.json']);value[field]=[];path.write_bytes(raw(value))
            with self.assertRaises(WorkflowError):self.bind()
        self.assertFalse(self.binding.exists())

    def test_even_equivalent_unreviewed_reformatting_is_rejected(self):
        path=self.repo/'data/raw/MSFT_companyfacts.json';path.write_bytes(raw(json.loads(path.read_bytes()),indent=4))
        with self.assertRaises(WorkflowError) as caught:self.bind()
        self.assertEqual(caught.exception.code,'frozen_source_mismatch')

    def test_certificate_tamper_cannot_expand_allowlist(self):
        proof=json.loads(self.certificate.read_bytes());proof['files'][0]['current_raw_sha256']='d'*64;self.certificate.write_bytes(raw(proof))
        with self.assertRaises(WorkflowError) as caught:self.bind()
        self.assertEqual(caught.exception.code,'source_equivalence_proof')

    def test_certificate_must_match_original_frozen_manifest(self):
        self.manifest['source_hashes']['sec_source_snapshot/MSFT_companyfacts.json']='0'*64
        with self.assertRaises(WorkflowError) as caught:self.bind()
        self.assertEqual(caught.exception.code,'source_equivalence_proof')

    def test_repeat_preserves_existing_binding_bytes_and_hash(self):
        _,first=self.bind();before=self.binding.read_bytes();stamp=self.binding.stat().st_mtime_ns
        _,second=self.bind(expected_binding_sha256=first['binding_sha256'],require_existing=True)
        self.assertEqual(first,second);self.assertEqual(self.binding.read_bytes(),before);self.assertEqual(self.binding.stat().st_mtime_ns,stamp)

    def test_other_approved_variant_cannot_replace_existing_run_binding(self):
        _,report=self.bind();before=self.binding.read_bytes()
        (self.repo/'data/raw/MSFT_filings.json').write_bytes(self.old['data/raw/MSFT_filings.json'])
        with self.assertRaises(WorkflowError) as caught:self.bind(expected_binding_sha256=report['binding_sha256'])
        self.assertEqual(caught.exception.code,'source_binding_identity');self.assertEqual(self.binding.read_bytes(),before)

    def test_missing_binding_for_initialized_pilot_is_not_recreated(self):
        with self.assertRaises(WorkflowError) as caught:self.bind(require_existing=True)
        self.assertEqual(caught.exception.code,'source_binding_identity');self.assertFalse(self.binding.exists())

    def test_missing_registered_binding_is_not_recreated(self):
        with self.assertRaises(WorkflowError):self.bind(expected_binding_sha256='f'*64)
        self.assertFalse(self.binding.exists())

    def test_tampered_saved_binding_is_not_repaired(self):
        self.bind();value=json.loads(self.binding.read_bytes());value['expected_answers_unchanged']=False;self.binding.write_bytes(raw(value));before=self.binding.read_bytes()
        with self.assertRaises(WorkflowError) as caught:self.bind()
        self.assertEqual(caught.exception.code,'source_binding_identity');self.assertEqual(self.binding.read_bytes(),before)

    def test_symlink_source_and_saved_binding_are_rejected(self):
        path=self.repo/'data/raw/MSFT_companyfacts.json';original=path.read_bytes();path.unlink();target=self.root/'outside.json';target.write_bytes(original);path.symlink_to(target)
        with self.assertRaises(WorkflowError):self.bind()
        path.unlink();path.write_bytes(original);self.binding.parent.mkdir();self.binding.symlink_to(target)
        with self.assertRaises(WorkflowError):self.bind()
        self.assertEqual(target.read_bytes(),original)


if __name__=='__main__':unittest.main()
