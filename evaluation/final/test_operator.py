"""Offline operator regressions: no provider or service connections."""
import argparse
from copy import deepcopy
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from evaluation.final import operator as op
from workflow_contracts import WorkflowError, digest


class OperatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.repo=self.folder/'repo';self.repo.mkdir();(self.repo/'.git').mkdir()
        self.output=self.folder/'results'
        self.request={'question':'Compare Microsoft operating margins and risks for the requested fiscal year.',
            'tickers':['MSFT'],'years':[2025],'include_risks':True}
        self.cases=[{'case_id':key,'split':'dev','request':self.request} for key in op.PILOT_IDS]
        self.fact={'status':'ok','value':'100','unit':'USD','concept':'Revenues','snapshot_sha256':'a'*64,'filings_snapshot_sha256':'b'*64,'filed':'2025-07-30','filing_identifiers':['0000950170-25-100235'],'source_url':'https://www.sec.gov/Archives/edgar/data/789019/000095017025100235/0000950170-25-100235-index.html'}
        self.target={'ticker':'MSFT','fiscal_year':2025,'period_start':'2024-07-01','period_end':'2025-06-30',
            'revenue':self.fact,'operating_income':{**self.fact,'value':'25','concept':'OperatingIncomeLoss'}}
        self.gold=[{'case_id':key,'numeric_targets':[self.target]} for key in op.PILOT_IDS]

    def args(self):
        return argparse.Namespace(repo=self.repo,output=self.output,pilot=True,claude_review=None,
                                  evaluator_private=self.output/'_evaluator_private',case_bundle=None)

    def binding_report(self):
        return {'status':'verified','binding_sha256':'e'*64,'original_gold_sha256':digest(self.gold),
            'bound_gold_sha256':digest(self.gold),'expected_answers_unchanged':True,'certificate_sha256':'f'*64}

    def pricing(self):
        return {'schema_version':1,'verification':{'method':'live_openrouter_models_api',
            'source_url':'https://openrouter.ai/api/v1/models','verified_at':datetime.now(timezone.utc).isoformat(),'response_sha256':'a'*64},
            'models':{model:{'family':model.split('/')[0],'prompt_usd_per_million':'0.4','completion_usd_per_million':'1.6','request_usd':'0'}
                for model in (op.TASK_MODEL,op.JUDGE_MODEL)}}

    def test_direct_file_help_does_not_shadow_stdlib_operator(self):
        result=subprocess.run([sys.executable,'-B',op.__file__,'--help'],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--evaluator-private',result.stdout)

    def test_credentials_are_narrow_no_interpolation_and_environment_wins(self):
        (self.repo/'.env').write_text('OPENROUTER_API_KEY="from_file"\nSANDBOX_API_TOKEN=${NOT_EXPANDED}\nPORTAL_REVIEW_TOKEN=never_read\nOTHER_SECRET=never_read\n')
        actual=op.load_credentials(self.repo,{'OPENROUTER_API_KEY':'from_env'})
        self.assertEqual(actual,{'OPENROUTER_API_KEY':'from_env','SANDBOX_API_TOKEN':'${NOT_EXPANDED}'})

    def test_new_output_cannot_reset_registered_pilot_allowance(self):
        op.private_directory(self.output/'private')
        registry,value=op.claim_run(self.repo,self.output,{'batch_id':'BATCH_TEST'})
        self.assertEqual(registry.parent,self.repo/'.analyst-desk-evaluation')
        self.assertEqual(list((self.repo/'.git').iterdir()),[])
        other=self.folder/'other';op.private_directory(other/'private')
        with self.assertRaises(WorkflowError) as exc:
            op.claim_run(self.repo,other,{'batch_id':'BATCH_TEST'})
        self.assertEqual(exc.exception.code,'pilot_existing_run')
        value['budget_initialized']=True;op.private_json(registry,value)
        with self.assertRaises(WorkflowError) as exc:
            op.claim_run(self.repo,self.output,{'batch_id':'BATCH_TEST'})
        self.assertEqual(exc.exception.code,'live_ledger_identity')

    def test_renaming_complete_output_preserves_registry_identity(self):
        op.private_directory(self.output/'private')
        _,original=op.claim_run(self.repo,self.output,{'batch_id':'BATCH_TEST'})
        moved=self.folder/'renamed';self.output.rename(moved)
        _,saved=op.claim_run(self.repo,moved,{'batch_id':'BATCH_TEST'})
        self.assertEqual(saved['run_uuid'],original['run_uuid'])

    def test_report_archive_excludes_private_gold_ledger_and_unlisted_files(self):
        op.private_directory(self.output/'_evaluator_private');op.private_directory(self.output/'private')
        (self.output/'_evaluator_private'/'gold.private.jsonl').write_text('DO_NOT_EXPORT')
        (self.output/'private'/'Budget.sqlite3').write_text('PRIVATE_LEDGER')
        (self.output/'.env').write_text('SECRET')
        (self.output/'random.json').write_text('UNLISTED')
        op.atomic_json(self.output/'Operator_Status.json',{'status':'paused'})
        op.atomic_json(self.output/'pilot'/'executions'/'PILOT_C2_F002_r1'/'Result.json',{'status':'completed'})
        archive=op.package_reports(self.output)
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(set(z.namelist()),{'Operator_Status.json','pilot/executions/PILOT_C2_F002_r1/Result.json'})

    def test_optional_memory_report_runs_without_model_or_source_mutation(self):
        private=op.private_directory(self.output/'_evaluator_private');op.private_directory(self.output/'private')
        self.assertEqual(op.maybe_memory_report(private,self.output)['status'],'not_run')
        for name in ('25_Publication_Approval_Binding.json','25_Publication_Receipt.json'):
            (private/name).write_text('{}')
        with patch('evaluation.final.memory_comparison.report_from_files',return_value={'model_calls':0,'network_calls':0}) as report:
            result=op.maybe_memory_report(private,self.output)
        self.assertEqual(result['status'],'completed')
        report.assert_called_once_with(private/'25_Publication_Approval_Binding.json',private/'25_Publication_Receipt.json',workspace=self.output/'private')
        self.assertTrue(op.is_public_report(Path('Memory_On_Off_Report.json')))
        self.assertTrue((self.output/'Memory_On_Off_Report.json').is_file())

    def test_frozen_bundle_checks_each_exact_artifact_and_evaluator_code(self):
        author=self.folder/'author.py';author.write_text('original frozen evaluator')
        files={'cases.dev.jsonl':''.join(json.dumps(c)+'\n' for c in self.cases),
               'gold.private.jsonl':''.join(json.dumps(g)+'\n' for g in self.gold)}
        manifest={'artifact_hashes':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in files.items()},
                  'authoring_code_sha256':op.sha_file(author),'source_hashes':{}}
        files['freeze_manifest.json']=json.dumps(manifest)
        bundle={'schema_version':'final-operator-case-bundle-v1','files':files,'bundle_sha256':digest(files),
                'original_freeze_manifest_sha256':hashlib.sha256(files['freeze_manifest.json'].encode()).hexdigest()}
        path=self.folder/'bundle.json';path.write_text(json.dumps(bundle))
        _,_,cases,gold=op.verify_bundle(path,expected_hash=bundle['bundle_sha256'],authoring_path=author)
        self.assertEqual([c['case_id'] for c in cases],list(op.PILOT_IDS))
        author.write_text('changed evaluator')
        with self.assertRaisesRegex(ValueError,'evaluator differs'):
            op.verify_bundle(path,expected_hash=bundle['bundle_sha256'],authoring_path=author)

    def test_exact_existing_cache_hash_is_required_without_overwrite(self):
        data=self.repo/'data'/'raw';data.mkdir(parents=True)
        (data/'MSFT_companyfacts.json').write_text('{"facts":{}}')
        (data/'MSFT_filings.json').write_text('{"annual_filings":[]}')
        hashes={'sec_source_snapshot/'+p.name:op.sha_file(p) for p in data.iterdir()}
        self.assertEqual(op.verify_local_sources(self.repo,{'source_hashes':hashes},self.cases)['status'],'verified')
        (data/'MSFT_companyfacts.json').write_text('changed')
        with self.assertRaises(WorkflowError) as exc:
            op.verify_local_sources(self.repo,{'source_hashes':hashes},self.cases)
        self.assertEqual(exc.exception.code,'frozen_source_mismatch')
        self.assertEqual((data/'MSFT_companyfacts.json').read_text(),'changed')

    async def test_preflight_checks_actual_tool_shapes_fact_hashes_and_sandbox(self):
        target=self.target
        class Backend:
            def __init__(self):self.calls=[];self.bad=False;self.bad_url=False
            async def call(self,service,tool,args):
                self.calls.append((service,tool,args))
                if tool=='get_coverage':return {'generation':'generation_fixture','models_sha256':'c'*64}
                if tool=='get_annual_financial_fact':
                    expected=target[args['metric']]
                    return {**expected,'ticker':'MSFT','fiscal_year':2025,'metric':args['metric'],
                        'value':int(expected['value'])+int(self.bad),'period_start':target['period_start'],'period_end':target['period_end'],
                        'sources':[{'filing_identifier':expected['filing_identifiers'][0],'url':'https://wrong.invalid/' if self.bad_url else expected['source_url'],
                            'concept':expected['concept'],'value':int(expected['value']),'filed':expected['filed']}]}
                p={'passage_id':'p_fixture','ticker':'MSFT','fiscal_year':2025,'item':args.get('item','7'),'source_sha256':'d'*64}
                if tool=='get_section':self.p=p;return {'passages':[p]}
                if tool=='get_passage':return {'passage':self.p}
                if tool=='search_filings':return {'hits':[{'passage_id':'p_fixture'}]}
                if tool=='execute_python':return {'result':'25.00','kernel_probes':{k:True for k in ('network_denied','file_open_denied','process_creation_denied')}}
                raise AssertionError(tool)
        backend=Backend();report=await op.preflight_backend(backend,self.cases,self.gold)
        self.assertEqual(report['model_calls'],0)
        self.assertTrue(report['sandbox_verified'])
        backend.bad=True
        with self.assertRaises(WorkflowError):await op.preflight_backend(backend,self.cases,self.gold)
        backend.bad=False;backend.bad_url=True
        with self.assertRaisesRegex(WorkflowError,'URLs'):await op.preflight_backend(backend,self.cases,self.gold)

    async def test_pause_after_concrete_preflight_never_calls_model_and_writes_results(self):
        messages=[]
        bundle={'bundle_sha256':op.BUNDLE_SHA256,'original_freeze_manifest_sha256':'a'*64}
        with patch.object(op,'find_bundle',return_value='fixture'),patch.object(op,'verify_bundle',return_value=(bundle,{},self.cases,self.gold)),\
             patch.object(op,'bind_sources',return_value=(self.gold,self.binding_report())),patch.object(op,'load_credentials',return_value={'OPENROUTER_API_KEY':'never_log_key','SANDBOX_API_TOKEN':'sandbox'}),\
             patch.object(op,'MCPBackend',return_value=object()),patch.object(op,'preflight_backend',new=AsyncMock(return_value={'status':'passed'})),\
             patch.object(op,'fetch_current_pricing',new=AsyncMock(return_value=self.pricing())),patch.object(op,'run_experiment',new=AsyncMock()) as run:
            audit=await op.operate(self.args(),input_fn=lambda prompt:'',print_fn=messages.append)
        self.assertEqual(audit['status'],'paused',audit)
        run.assert_not_called()
        self.assertNotIn('never_log_key','\n'.join(messages))
        self.assertTrue((self.output/'Pilot_Results.zip').is_file())
        self.assertTrue((self.output/'_evaluator_private'/'Pilot_Gold.json').is_file())

    async def test_approved_allowance_is_reused_without_reapproval_or_repricing(self):
        messages=[]
        bundle={'bundle_sha256':op.BUNDLE_SHA256,'original_freeze_manifest_sha256':'a'*64}
        patches=[patch.object(op,'find_bundle',return_value='fixture'),
            patch.object(op,'verify_bundle',return_value=(bundle,{},self.cases,self.gold)),
            patch.object(op,'bind_sources',return_value=(self.gold,self.binding_report())),
            patch.object(op,'load_credentials',return_value={'OPENROUTER_API_KEY':'never_log_key','SANDBOX_API_TOKEN':'sandbox'}),
            patch.object(op,'MCPBackend',return_value=object()),
            patch.object(op,'preflight_backend',new=AsyncMock(return_value={'status':'passed'})),
            patch.object(op,'run_experiment',new=AsyncMock(return_value={'status_counts':{'completed':6}}))]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in patches:stack.enter_context(p)
            pricing=stack.enter_context(patch.object(op,'fetch_current_pricing',new=AsyncMock(return_value=self.pricing())))
            first=await op.operate(self.args(),input_fn=lambda prompt:'APPROVE',print_fn=messages.append)
            self.assertEqual(first['status'],'completed',first)
            pricing.assert_awaited_once()
            pricing.reset_mock()
            second=await op.operate(self.args(),input_fn=lambda prompt:self.fail('Already approved'),print_fn=messages.append)
            self.assertEqual(second['status'],'completed',second)
            pricing.assert_not_awaited()
        self.assertTrue((self.output/'private'/'Budget.sqlite3').is_file())
        self.assertTrue((self.output/'private'/'Budget_Approval.json').is_file())
        report=json.loads((self.output/'Budget_Report.json').read_text())
        self.assertEqual(report['reserved_requests'],0)
        self.assertNotIn('never_log_key',(self.output/'Operator_Audit.json').read_text())

    async def test_preflight_failure_does_not_fetch_prices_or_request_approval(self):
        bundle={'bundle_sha256':op.BUNDLE_SHA256,'original_freeze_manifest_sha256':'a'*64}
        with patch.object(op,'find_bundle',return_value='fixture'),patch.object(op,'verify_bundle',return_value=(bundle,{},self.cases,self.gold)),\
             patch.object(op,'bind_sources',side_effect=WorkflowError('frozen_source_mismatch','fixture mismatch')),\
             patch.object(op,'fetch_current_pricing',new=AsyncMock()) as pricing:
            audit=await op.operate(self.args(),input_fn=lambda prompt:self.fail('No approval prompt'),print_fn=lambda line:None)
        self.assertEqual(audit['status'],'failed')
        self.assertEqual(audit['error_code'],'frozen_source_mismatch')
        pricing.assert_not_called()
        self.assertTrue((self.output/'Pilot_Results.zip').is_file())


    async def test_bound_gold_reaches_preflight_and_frozen_evaluator_without_mutating_original(self):
        original=deepcopy(self.gold);bound=deepcopy(self.gold)
        for case in bound:
            for target in case['numeric_targets']:
                for metric in ('revenue','operating_income'):
                    target[metric]['snapshot_sha256']='c'*64;target[metric]['filings_snapshot_sha256']='d'*64
        report=self.binding_report();report['bound_gold_sha256']=digest(bound)
        bundle={'bundle_sha256':op.BUNDLE_SHA256,'original_freeze_manifest_sha256':'a'*64}
        with patch.object(op,'find_bundle',return_value='fixture'),patch.object(op,'verify_bundle',return_value=(bundle,{},self.cases,self.gold)),patch.object(op,'bind_sources',return_value=(bound,report)),patch.object(op,'load_credentials',return_value={'OPENROUTER_API_KEY':'never_log_key','SANDBOX_API_TOKEN':'sandbox'}),patch.object(op,'MCPBackend',return_value=object()),patch.object(op,'preflight_backend',new=AsyncMock(return_value={'status':'passed'})) as preflight,patch.object(op,'fetch_current_pricing',new=AsyncMock(return_value=self.pricing())),patch.object(op,'run_experiment',new=AsyncMock(return_value={'status_counts':{'completed':6}})) as run:
            audit=await op.operate(self.args(),input_fn=lambda prompt:'APPROVE',print_fn=lambda text:None)
        self.assertEqual(audit['status'],'completed',audit)
        self.assertEqual(preflight.await_args.args[2],bound)
        self.assertEqual(run.await_args.kwargs['private_gold'],bound)
        self.assertIs(run.await_args.kwargs['evaluate'],op.build_cases.evaluate_case)
        self.assertEqual(self.gold,original)
        self.assertEqual(json.loads((self.output/'_evaluator_private/Pilot_Gold.json').read_text()),original)
        self.assertEqual(json.loads((self.output/'_evaluator_private/Bound_Pilot_Gold.json').read_text()),bound)
        registry=json.loads((self.repo/'.analyst-desk-evaluation/final34_pilot.json').read_text())
        self.assertEqual(registry['source_binding_sha256'],report['binding_sha256'])
        self.assertTrue(audit['source_binding']['expected_answers_unchanged'])

    async def test_console_displays_actual_failure_with_credentials_redacted(self):
        messages=[];bundle={'bundle_sha256':op.BUNDLE_SHA256,'original_freeze_manifest_sha256':'a'*64}
        with patch.object(op,'find_bundle',return_value='fixture'),patch.object(op,'verify_bundle',return_value=(bundle,{},self.cases,self.gold)),patch.object(op,'bind_sources',return_value=(self.gold,self.binding_report())),patch.object(op,'load_credentials',return_value={'OPENROUTER_API_KEY':'never_log_key','SANDBOX_API_TOKEN':'sandbox_secret'}),patch.object(op,'MCPBackend',return_value=object()),patch.object(op,'preflight_backend',new=AsyncMock(side_effect=WorkflowError('fixture_failure','Rejected never_log_key and sandbox_secret'))),patch.object(op,'fetch_current_pricing',new=AsyncMock()) as pricing:
            audit=await op.operate(self.args(),input_fn=lambda prompt:self.fail('No approval'),print_fn=messages.append)
        self.assertEqual(audit['status'],'failed');pricing.assert_not_called()
        self.assertIn('Error [fixture_failure]: Rejected [redacted] and [redacted]',messages)
        self.assertNotIn('never_log_key','\n'.join(messages));self.assertNotIn('sandbox_secret','\n'.join(messages))
        self.assertIn('Independent human review remains incomplete.',messages)


if __name__=='__main__':unittest.main()
