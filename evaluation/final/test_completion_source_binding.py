"""Offline source-equivalence regressions; synthetic data, no external calls."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from workflow_contracts import WorkflowError, digest
from evaluation.final import completion_source_binding as b


def raw_sha(value):
    return hashlib.sha256(value).hexdigest()


class BindingFixture:
    def __init__(self, root):
        self.root=Path(root);(self.root/'data/raw').mkdir(parents=True)
        self.manifest={'source_hashes':{},'authoring_code_sha256':'c'*64}
        self.cases=[{'case_id':'D001','request':{'tickers':['AAPL','MSFT']}}]
        self.original={};rows=[]
        for ticker in ['AAPL','MSFT']:
            for kind in ['companyfacts','filings']:
                name=f'data/raw/{ticker}_{kind}.json'
                value={'cik':1,'nested':{'prepared_at':'immutable'},'rows':[1,2,3]}
                if kind=='filings':value.update(prepared_at='2026-11-09T00:00:00+00:00',annual_filings=[{'accession':'original','year':2025}])
                else:value['facts']={'revenue':100,'income':20}
                raw=json.dumps(value).encode();(self.root/name).write_bytes(raw);self.original[name]=raw
                self.manifest['source_hashes']['sec_source_snapshot/'+Path(name).name]=raw_sha(raw)
                rows.append({'relative_path':name,'ticker':ticker,'kind':kind,'cik':'0000000001',
                    'frozen_raw_sha256':raw_sha(raw),'canonical_sha256':b.canonical_sha256(b.strict_json(raw),kind),
                    'ignored_root_fields':['prepared_at'] if kind=='filings' else [],
                    'prepared_at_policy':{'required':True,'type':'string','format':'timezone_aware_iso8601'} if kind=='filings' else None})
        self.html=b'original fiscal-year filing';sha=raw_sha(self.html)
        self.year_name='data/raw/sec_fiscal_years/'+sha+'.html'
        (self.root/self.year_name).parent.mkdir();(self.root/self.year_name).write_bytes(self.html)
        self.manifest['source_hashes']['sec_source_snapshot/sec_fiscal_years/'+sha+'.html']=sha
        self.proof={'schema_version':'completion-source-equivalence-v1','frozen_bundle_sha256':'a'*64,
            'original_freeze_manifest_sha256':'b'*64,'frozen_authoring_code_sha256':'c'*64,
            'canonicalization':{'name':b.CANONICALIZATION},'case_ids':['D001'],
            'allowed_bound_gold_changes':list(b.HASH_FIELDS),'files':rows,'year_evidence_sha256':{self.year_name:sha}}
        self.certificate=self.root/'Certificate.json';self.save_certificate()
        self.pilot={'binding_sha256':'d'*64,'files':[{'relative_path':row['relative_path'],
            'frozen_sha256':row['frozen_raw_sha256'],'actual_sha256':row['frozen_raw_sha256']}
            for row in rows if row['ticker']=='MSFT']}
        targets=[]
        for ticker in ['AAPL','MSFT']:
            fact={'value':'100','filed':'2026-01-01','source_url':'https://example.test/filing',
                  'filing_identifiers':{'accession':'original'},'fiscal_year':2025,
                  'snapshot_sha256':self.manifest['source_hashes']['sec_source_snapshot/'+ticker+'_companyfacts.json'],
                  'filings_snapshot_sha256':self.manifest['source_hashes']['sec_source_snapshot/'+ticker+'_filings.json']}
            targets.append({'ticker':ticker,'fiscal_year':2025,'revenue':deepcopy(fact),'operating_income':deepcopy(fact)})
        self.gold=[{'case_id':'D001','numeric_targets':targets,'request':'unchanged','unavailable':['unchanged']}]
    def save_certificate(self):
        self.certificate.write_text(json.dumps(self.proof));self.certificate_hash=raw_sha(self.certificate.read_bytes())
    def edit(self,name,fn):
        value=json.loads((self.root/name).read_text());fn(value);(self.root/name).write_text(json.dumps(value))
    def bind(self,**kwargs):
        options=dict(certificate_path=self.certificate,binding_path=self.root/'Completion_Source_Binding.json',
            diagnostics_path=self.root/'Source_Cache_Diagnostics.json',frozen_bundle_sha256='a'*64,
            original_freeze_manifest_sha256='b'*64,expected_certificate_sha256=self.certificate_hash)
        options.update(kwargs)
        return b.bind_completion_sources(self.root,self.manifest,self.cases,self.gold,self.pilot,**options)
    def diagnostics(self):return json.loads((self.root/'Source_Cache_Diagnostics.json').read_text())


class CanonicalTests(unittest.TestCase):
    def test_exact_decimal_perturbations_do_not_round_together(self):
        a=b.strict_json('{"v":1.0000000000000000000000000000000000001}')
        z=b.strict_json('{"v":1.0000000000000000000000000000000000002}')
        self.assertNotEqual(b.canonical_sha256(a,'companyfacts'),b.canonical_sha256(z,'companyfacts'))
        self.assertEqual(b.canonical_sha256(b.strict_json('{"v":1}'),'companyfacts'),
                         b.canonical_sha256(b.strict_json('{"v":1.000e0}'),'companyfacts'))
    def test_duplicate_nonfinite_and_overflow_json_rejected(self):
        for value in ['{"x":1,"x":2}','{"x":NaN}','{"x":Infinity}','{"x":-Infinity}','{"x":1e9999}']:
            with self.subTest(value=value),self.assertRaises((ValueError,OverflowError)):b.strict_json(value)
    def test_types_and_array_order_are_preserved(self):
        for left,right in [('{"v":1}','{"v":true}'),('{"v":1}','{"v":"1"}'),('{"v":[1,2]}','{"v":[2,1]}')]:
            self.assertNotEqual(b.canonical_sha256(b.strict_json(left),'companyfacts'),b.canonical_sha256(b.strict_json(right),'companyfacts'))


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.f=BindingFixture(self.tmp.name)
    def test_format_timestamp_variants_only_rebind_two_fields(self):
        f=self.f;gold=deepcopy(f.gold)
        f.edit('data/raw/AAPL_filings.json',lambda v:v.update(prepared_at='2026-11-14T12:00:00Z'))
        name='data/raw/AAPL_companyfacts.json';(f.root/name).write_text(json.dumps(json.loads(f.original[name]),indent=4))
        before={path:(f.root/path).read_bytes() for path in f.original}
        bound,report=f.bind()
        self.assertEqual(f.gold,gold);self.assertEqual(b._answer_projection(bound),b._answer_projection(gold))
        self.assertNotEqual(bound[0]['numeric_targets'][0]['revenue']['snapshot_sha256'],gold[0]['numeric_targets'][0]['revenue']['snapshot_sha256'])
        self.assertEqual(report['original_gold_sha256'],digest(gold));self.assertTrue(report['expected_answers_unchanged'])
        self.assertEqual(f.diagnostics()['sources_checked'],4)
        self.assertEqual(f.diagnostics()['year_html_checked'],1)
        self.assertEqual(before,{path:(f.root/path).read_bytes() for path in f.original})
    def test_all_mismatches_are_reported_at_once_without_binding(self):
        f=self.f
        for name in f.original:f.edit(name,lambda value:value.update(cik=999))
        with self.assertRaisesRegex(WorkflowError,'4 source cache'):f.bind()
        report=f.diagnostics();self.assertEqual(report['sources_checked'],4);self.assertEqual(report['mismatch_count'],4)
        self.assertTrue(all(r['status']=='canonical_mismatch' for r in report['files']))
        self.assertFalse((f.root/'Completion_Source_Binding.json').exists())
    def test_missing_null_invalid_and_timezone_free_timestamp_rejected(self):
        f=self.f;name='data/raw/AAPL_filings.json'
        for invalid in [None,15,'bad','2026-11-14T12:00:00','MISSING']:
            (f.root/name).write_bytes(f.original[name])
            f.edit(name,lambda value:value.pop('prepared_at') if invalid=='MISSING' else value.update(prepared_at=invalid))
            with self.subTest(invalid=invalid),self.assertRaises(WorkflowError):f.bind()
            self.assertEqual(f.diagnostics()['files'][1]['status'],'prepared_at_invalid')
    def test_all_nested_fields_accessions_facts_and_array_order_frozen(self):
        f=self.f
        mutations=[('filings',lambda v:v['nested'].update(prepared_at='changed')),
            ('filings',lambda v:v['annual_filings'][0].update(accession='changed')),
            ('filings',lambda v:v['annual_filings'][0].update(year=2024)),
            ('companyfacts',lambda v:v['facts'].update(revenue=101)),
            ('companyfacts',lambda v:v.update(rows=[3,2,1]))]
        for kind,change in mutations:
            for name,raw in f.original.items():(f.root/name).write_bytes(raw)
            f.edit('data/raw/AAPL_'+kind+'.json',change)
            with self.subTest(kind=kind),self.assertRaises(WorkflowError):f.bind()
            self.assertEqual(f.diagnostics()['mismatch_count'],1)
    def test_source_duplicate_json_is_rejected_even_if_last_value_matches(self):
        f=self.f;name='data/raw/AAPL_companyfacts.json';raw=f.original[name].decode();(f.root/name).write_text(raw.replace('"cik": 1','"cik": 2, "cik": 1'))
        with self.assertRaises(WorkflowError):f.bind()
        self.assertEqual(f.diagnostics()['files'][0]['status'],'invalid_json')
    def test_original_certificate_hash_and_manifest_must_match(self):
        f=self.f;f.certificate.write_text(f.certificate.read_text()+' ')
        with self.assertRaisesRegex(WorkflowError,'exact hash'):f.bind()
        self.assertEqual(f.diagnostics()['status'],'certificate_rejected')
        f.save_certificate();f.manifest['source_hashes']['sec_source_snapshot/AAPL_filings.json']='e'*64
        with self.assertRaises(WorkflowError):f.bind()
    def test_certificate_omission_and_extra_ignored_fields_are_rejected(self):
        f=self.f;proof=deepcopy(f.proof)
        f.proof['files'].pop();f.save_certificate()
        with self.assertRaises(WorkflowError):f.bind()
        f.proof=proof;f.proof['files'][0]['ignored_root_fields']=['facts'];f.save_certificate()
        with self.assertRaises(WorkflowError):f.bind()
    def test_wrong_frozen_gold_provenance_never_creates_binding(self):
        f=self.f;f.gold[0]['numeric_targets'][0]['revenue']['snapshot_sha256']='e'*64
        with self.assertRaises(WorkflowError):f.bind()
        self.assertFalse((f.root/'Completion_Source_Binding.json').exists())
    def test_saved_actual_hashes_cannot_change_on_resume(self):
        f=self.f;_,first=f.bind();saved=(f.root/'Completion_Source_Binding.json').read_bytes()
        self.assertEqual(f.bind(require_existing=True)[1],first)
        f.edit('data/raw/AAPL_filings.json',lambda v:v.update(prepared_at='2026-11-15T00:00:00Z'))
        with self.assertRaisesRegex(WorkflowError,'saved completion binding differs'):f.bind(require_existing=True)
        self.assertEqual((f.root/'Completion_Source_Binding.json').read_bytes(),saved)
    def test_prior_scope_missing_binding_cannot_be_reset(self):
        with self.assertRaisesRegex(WorkflowError,'missing'):self.f.bind(require_existing=True)
        self.assertFalse((self.f.root/'Completion_Source_Binding.json').exists())
    def test_prior_paid_pilot_actual_hash_cannot_rebind(self):
        f=self.f;f.edit('data/raw/MSFT_filings.json',lambda v:v.update(prepared_at='2026-11-15T00:00:00Z'))
        with self.assertRaisesRegex(WorkflowError,'paid pilot source binding'):f.bind()
    def test_original_year_html_remains_byte_exact(self):
        f=self.f;(f.root/f.year_name).write_bytes(b'changed')
        with self.assertRaises(WorkflowError):f.bind()
        self.assertEqual(f.diagnostics()['year_evidence'][0]['status'],'raw_hash_mismatch')
    def test_symlink_and_missing_files_reported_together(self):
        f=self.f;names=sorted(f.original);(f.root/names[0]).unlink();(f.root/names[0]).symlink_to(f.root/names[2]);(f.root/names[1]).unlink()
        with self.assertRaises(WorkflowError):f.bind()
        report=f.diagnostics();self.assertEqual(report['mismatch_count'],2)
        self.assertEqual([r['status'] for r in report['files'][:2]],['unsafe_path','missing_file'])
    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        f=self.f;name='data/raw/AAPL_companyfacts.json';(f.root/name).unlink();os.mkfifo(f.root/name)
        with self.assertRaises(WorkflowError):f.bind()
        self.assertEqual(f.diagnostics()['files'][0]['status'],'unsafe_path')
    def test_diagnostics_never_include_source_values_or_exception_text(self):
        f=self.f;f.edit('data/raw/AAPL_companyfacts.json',lambda value:value.update(secret='DO_NOT_EXPORT_VALUE'))
        with self.assertRaises(WorkflowError):f.bind()
        self.assertNotIn('DO_NOT_EXPORT_VALUE',(f.root/'Source_Cache_Diagnostics.json').read_text())


if __name__=='__main__':unittest.main()
