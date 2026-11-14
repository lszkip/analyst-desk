"""Synthetic harness regressions, never reported as task-quality evidence."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.final.adapters import (ConfiguredModel, LocalCorpusBackend,
    NoPublicationBackend, execute_variant)
from evaluation.final.experiment_runner import (forecast, load_experiment,
    run_experiment, schedule, summarize, workflow_config_for)
from workflow_agents import load_config
from workflow_contracts import WorkflowError, canonical
from workflow_store import RunStore

# Reuses existing explicitly synthetic backend to test production control flow.
from test_research_workflow import FixtureBackend, FixtureModel

REQUEST = {'question': 'Compare Microsoft operating margins for the requested fiscal year.',
           'tickers': ['MSFT'], 'years': [2025], 'include_risks': False}


def variant(name='C2', architecture='team', critic=True, lessons=False):
    return {'id':name,'architecture':architecture,'critic':critic,'lessons':lessons,'retrieval':'production_hybrid'}


def config(variants=None, repeats=1):
    return {'schema_version':'final-experiments-v1','experiment_id':'TEST','splits':['heldout'],
        'repeats':repeats,'concurrency':2,'case_timeout_seconds':30,
        'workflow':{'model':'openai/gpt-4.1-mini','temperature':0,'token_budget':900000,'tool_budget':240},
        'variants':variants or [variant()]}


class SingleAwareModel(FixtureModel):
    def __init__(self):
        super().__init__()
        self.message_content = []

    async def complete(self, messages, config):
        self.message_content.append(canonical(messages))
        context = json.loads(messages[1]['content'])
        role = json.loads(messages[0]['content'])['role']
        if role == 'researcher' and context['company_scope'] is None:
            self.calls.append((role, context))
            missing = context['saved_evidence']['missing_slots']
            if missing:
                return self.response({'tool':'get_section','arguments':{**missing[0],'offset':0},'final':None})
            if not any(row['tool']=='collect_financials' for row in context['completed_tool_requests']):
                return self.response({'tool':'collect_financials','arguments':{},'final':None})
            ids = [key for slot in context['saved_evidence']['slots'] for key in slot['sample_ids']]
            return self.response({'tool':'finish','arguments':{},'final':{'title':'Company operating margin comparison','paragraphs':[{'text':'The cited company passage describes competitive pressures.', 'evidence_ids':ids[:1]}]}})
        return await super().complete(messages, config)


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    async def test_all_three_architectures_execute_real_production_guards(self):
        counts = {}
        for name, architecture in [('C0','fixed_workflow'),('C1','single_agent'),('C2','team')]:
            backend, model = FixtureBackend(self.folder/name), SingleAwareModel()
            result = await execute_variant(REQUEST, variant(name,architecture), load_config(), model,
                backend, self.folder/name/'run', name)
            self.assertEqual(result['status'],'completed', result)
            self.assertFalse(result['human_reviewed'])
            self.assertIsNone(result['publication'])
            self.assertTrue(result['verification'])
            self.assertFalse(any(call[0]=='portal' for call in backend.calls))
            roles = [role for role,_ in model.calls]
            counts[name] = roles
            self.assertIn('critic', roles)
        self.assertNotIn('supervisor',counts['C0'])
        self.assertNotIn('researcher',counts['C0'])
        self.assertNotIn('writer',counts['C1'])
        self.assertNotIn('quant',counts['C1'])
        self.assertIn('supervisor',counts['C2'])
        self.assertIn('quant',counts['C2'])

    async def test_completion_c0_is_one_tool_free_writer_after_fixed_collection(self):
        backend,model=FixtureBackend(self.folder/'fixed'),SingleAwareModel()
        request={**REQUEST,'include_risks':True,'question':'Compare Microsoft operating margins and competitive risks for the requested fiscal year.'}
        original_config=load_config();saved_config=copy.deepcopy(original_config)
        result=await execute_variant(request,variant('C0','fixed_workflow',False),original_config,model,
            backend,self.folder/'fixed'/'run','fixed')
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual([role for role,_ in model.calls],['writer'])
        context=model.calls[0][1]
        self.assertEqual(context['tools'],{})
        self.assertEqual(context['progress']['max_steps'],1)
        self.assertTrue(context['progress']['finish_required'])
        self.assertEqual(original_config,saved_config)
        self.assertFalse(any(call[0]=='portal' for call in backend.calls))
        self.assertGreater(len([call for call in backend.calls if call[1]=='get_annual_financial_fact']),2)

    async def test_completion_c0_cannot_choose_tools_or_repair_with_more_model_calls(self):
        request={**REQUEST,'include_risks':True}
        for mode in ['tool','malformed']:
            backend=FixtureBackend(self.folder/mode)
            class Attempt(FixtureModel):
                async def complete(model,messages,cfg):
                    model.calls.append((json.loads(messages[0]['content'])['role'],json.loads(messages[1]['content'])))
                    model.backend_count=len(backend.calls)
                    self.assertEqual(cfg['token_budget'],load_config()['token_budget'])
                    self.assertEqual(cfg['tool_budget'],load_config()['tool_budget'])
                    return model.response({'tool':'get_passage','arguments':{'passage_id':'untrusted-request'},'final':None}
                        if mode=='tool' else {'tool':'finish','arguments':{},'final':{'invalid':'response'}})
            model=Attempt()
            with self.subTest(mode=mode),self.assertRaises(WorkflowError):
                await execute_variant(request,variant('C0','fixed_workflow',False),load_config(),model,
                    backend,self.folder/mode/'run',mode)
            self.assertEqual(len(model.calls),1)
            self.assertEqual(len(backend.calls),model.backend_count)

    async def test_completion_c1_has_only_the_single_worker_and_code_validation(self):
        backend,model=FixtureBackend(self.folder/'single'),SingleAwareModel()
        result=await execute_variant(REQUEST,variant('C1','single_agent',False),load_config(),model,
            backend,self.folder/'single'/'run','single')
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual({role for role,_ in model.calls},{'researcher'})
        self.assertTrue(all(context['company_scope'] is None for _,context in model.calls))
        self.assertFalse(result['semantic_support_verified'])
        self.assertGreater(len([call for call in backend.calls if call[1]=='get_annual_financial_fact']),2)

    async def test_critic_off_really_omits_model_critic_but_keeps_reverification(self):
        backend, model = FixtureBackend(self.folder/'off'), SingleAwareModel()
        result = await execute_variant(REQUEST, variant('off','fixed_workflow',False),load_config(),model,backend,self.folder/'off'/'run','off')
        self.assertEqual(result['status'],'completed')
        self.assertFalse(result['semantic_support_verified'])
        self.assertEqual(model.calls, [])
        fact_reads = [call for call in backend.calls if call[1]=='get_annual_financial_fact']
        self.assertGreater(len(fact_reads),2)

    async def test_proven_case_local_limit_does_not_stop_the_next_case(self):
        from evaluation.final.completion_runner import run_experiment as completion_run
        started=[]
        async def execute(request,variant,cfg,model,backend,folder,run,**kwargs):
            started.append(run)
            if '_A_' in run:
                store=RunStore(Path(folder)/'journal.sqlite3')
                store.start(run,{**cfg,'request':request,'evaluation_variant':variant})
                store.reserve(run,tokens=cfg['token_budget']-10)
                store.reserve(run,tokens=11)
            return {'status':'blocked'}
        cases=[{'case_id':key,'split':'heldout','request':REQUEST} for key in ['A','B']]
        cfg=config([variant('C0','fixed_workflow',False)]);cfg['concurrency']=1
        report=await completion_run(cases,cfg,self.folder/'local-budget',software_test_mode=True,
            executor=execute,model_factory=lambda *a:None,backend_factory=lambda *a:None)
        self.assertEqual(len(started),2);self.assertEqual(report['status_counts'],{'error':1,'blocked':1})
        rows=json.loads((self.folder/'local-budget/Execution_Results.json').read_text())
        self.assertEqual(rows[0]['error']['code'],'budget_exhausted')
        self.assertTrue(rows[0]['case_local_budget_review']['failed_outcome_retained'])

    async def test_unproven_local_or_real_global_limit_still_stops_queue(self):
        from evaluation.final.completion_runner import run_experiment as completion_run
        for code in ['budget_exhausted','live_budget_exhausted','live_time_limit','live_accounting_error']:
            started=[]
            async def execute(*args,**kwargs):started.append(True);raise WorkflowError(code,'Synthetic stop')
            cfg=config([variant('C0','fixed_workflow',False)]);cfg['concurrency']=1
            report=await completion_run([{'case_id':k,'split':'heldout','request':REQUEST} for k in ['A','B']],
                cfg,self.folder/code,software_test_mode=True,executor=execute,
                model_factory=lambda *a:None,backend_factory=lambda *a:None)
            self.assertEqual(len(started),1);self.assertEqual(report['status_counts'],{'error':1,'unrun':1})

    def test_local_classifier_reads_existing_exact_journal_without_mutating(self):
        from evaluation.final.completion_runner import verify_case_local_budget_failure
        cfg=load_config();v=variant('C0','fixed_workflow',False);folder=self.folder/'proof';folder.mkdir()
        row={'execution_id':'case','error':{'code':'budget_exhausted'},'metrics':{'cost_complete':True,'llm_requests_started':0}}
        self.assertIsNone(verify_case_local_budget_failure(row,folder,cfg,REQUEST,v))
        self.assertFalse((folder/'journal.sqlite3').exists())
        store=RunStore(folder/'journal.sqlite3');store.start('case',{**cfg,'request':REQUEST,'evaluation_variant':v})
        store.reserve('case',tokens=cfg['token_budget']-1);before=(folder/'journal.sqlite3').read_bytes()
        self.assertIsNotNone(verify_case_local_budget_failure(row,folder,cfg,REQUEST,v))
        self.assertEqual((folder/'journal.sqlite3').read_bytes(),before)
        for change in [{'execution_id':'wrong'},{'metrics':{'cost_complete':False,'llm_requests_started':0}}]:
            self.assertIsNone(verify_case_local_budget_failure({**row,**change},folder,cfg,REQUEST,v))
        self.assertIsNone(verify_case_local_budget_failure(row,folder,{**cfg,'token_budget':cfg['token_budget']+1},REQUEST,v))
        with store.db(True) as db:db.execute("UPDATE runs SET config_hash='wrong' WHERE id='case'")
        self.assertIsNone(verify_case_local_budget_failure(row,folder,cfg,REQUEST,v))

    def test_other_active_calls_are_distinguished_from_unknown_cost_or_poison(self):
        from evaluation.final.completion_runner import shared_accounting_ready
        stats={'poisoned':False,'stop_code':None,'requests_without_actual_cost':1,'in_flight_requests':1,
            'calls':[{'status':'reserved','case_id':'other','actual_nano_usd':None}]}
        self.assertTrue(shared_accounting_ready(stats,{'other'}))
        self.assertFalse(shared_accounting_ready(stats,set()))
        self.assertFalse(shared_accounting_ready({**stats,'poisoned':True},{'other'}))
        self.assertFalse(shared_accounting_ready({**stats,'requests_without_actual_cost':2},{'other'}))

    async def test_no_publication_even_if_backend_would_accept(self):
        wrapped = NoPublicationBackend(FixtureBackend(self.folder/'portal'))
        with self.assertRaises(WorkflowError) as exc:
            await wrapped.call('portal','prepare_note',{})
        self.assertEqual(exc.exception.code,'evaluation_publication_forbidden')
        self.assertEqual(wrapped.backend.calls,[])

    async def test_private_gold_never_reaches_model_and_repeats_have_fresh_cache(self):
        models = []
        def model_factory(execution_id,cfg):
            model=SingleAwareModel(); models.append(model); return model
        def evaluator(case_id,result,records,gold):
            self.assertEqual(gold['secret'],'GOLD_MARKER_NEVER_IN_CONTEXT')
            self.assertTrue(records)
            return {'numeric_success':result['status']=='completed','task_success':True}
        cases=[{'case_id':'CASE_A','split':'heldout','request':REQUEST,'gold_leak_trap':'PUBLIC_METADATA_NOT_AGENT_INPUT'}]
        cfg=config([variant('C0','fixed_workflow')],repeats=3)
        summary=await run_experiment(cases,cfg,self.folder/'batch',software_test_mode=True,model_factory=model_factory,
            backend_factory=lambda v:FixtureBackend(self.folder/v['id']),evaluate=evaluator,
            private_gold={'secret':'GOLD_MARKER_NEVER_IN_CONTEXT'})
        self.assertEqual(summary['by_variant']['C0']['numeric_passes'],3)
        self.assertEqual(len(models),3)
        self.assertTrue(all(model.calls for model in models))
        for model in models:
            self.assertNotIn('GOLD_MARKER',canonical(model.message_content))
            self.assertNotIn('PUBLIC_METADATA_NOT_AGENT_INPUT',canonical(model.message_content))
        self.assertEqual(summary['by_variant']['C0']['model_response_cache_hits'],0)
        before=len(models)
        await run_experiment(cases,cfg,self.folder/'batch',software_test_mode=True,model_factory=model_factory,
            backend_factory=lambda v:FixtureBackend(self.folder/v['id']),evaluate=evaluator,
            private_gold={'secret':'GOLD_MARKER_NEVER_IN_CONTEXT'})
        self.assertEqual(len(models),before)

    async def test_missing_reviewed_lessons_skips_without_model_call(self):
        summary=await run_experiment([{'case_id':'CASE_A','split':'heldout','request':REQUEST}],
            config([variant('lessons',lessons=True)]),self.folder/'lessons',
            software_test_mode=True,model_factory=lambda *a:self.fail('Must not call model'),backend_factory=lambda *a:self.fail('Must not call backend'))
        self.assertEqual(summary['status_counts'],{'skipped':1})

    async def test_failed_model_cost_and_error_preserved_not_passed(self):
        class FailedModel:
            async def complete(self,*args):
                raise WorkflowError('model_unavailable','Synthetic failed call')
        summary=await run_experiment([{'case_id':'CASE_A','split':'heldout','request':REQUEST}],config(),self.folder/'error',
            software_test_mode=True,model_factory=lambda *a:FailedModel(),backend_factory=lambda v:FixtureBackend(self.folder/'error_backend'))
        self.assertEqual(summary['status_counts'],{'error':1})
        self.assertFalse(summary['by_variant']['C2']['cost_complete'])
        self.assertEqual(summary['by_variant']['C2']['numeric_passes'],0)
        self.assertEqual(summary['by_variant']['C2']['model_requests_started'],1)

    async def test_live_without_durable_approved_ledger_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'durable shared budget'):
            await run_experiment([{'case_id':'A','split':'heldout','request':REQUEST}],config(),self.folder/'no_budget',
                model_factory=lambda *a:self.fail('No dispatch'),backend_factory=lambda *a:None)

    async def test_global_budget_failure_leaves_remaining_jobs_explicitly_unrun(self):
        class Exhausted:
            async def complete(self,*args):
                raise WorkflowError('live_budget_exhausted','Synthetic fixed budget stop')
        cfg=config([variant('C0','fixed_workflow'),variant('C2')]);cfg['concurrency']=1
        summary=await run_experiment([{'case_id':'A','split':'heldout','request':REQUEST}],cfg,self.folder/'cap',
            software_test_mode=True,model_factory=lambda *a:Exhausted(),backend_factory=lambda v:FixtureBackend(self.folder/'cap_backend'))
        self.assertEqual(summary['status_counts'],{'error':1,'unrun':1})
        self.assertEqual(summary['planned_executions'],2)
        self.assertEqual(summary['by_variant']['C2']['planned'],1)

    async def test_changed_frozen_configuration_cannot_resume(self):
        cases=[{'case_id':'A','split':'heldout','request':REQUEST}]
        cfg=config([variant('C0','fixed_workflow',False)])
        kwargs={'software_test_mode':True,'model_factory':lambda *a:SingleAwareModel(),
                'backend_factory':lambda v:FixtureBackend(self.folder/'freeze_backend')}
        await run_experiment(cases,cfg,self.folder/'frozen',**kwargs)
        cfg['workflow']['tool_budget']=241
        with self.assertRaisesRegex(ValueError,'identity changed'):
            await run_experiment(cases,cfg,self.folder/'frozen',**kwargs)

    async def test_schema_attestation_added_without_modifying_caller_config(self):
        class Capture:
            async def complete(self,messages,cfg):
                self.cfg=cfg; return {}
        capture=Capture(); wrapper=ConfiguredModel(capture)
        cfg={'_response_format':{'type':'json_schema','json_schema':{'name':'x'}}}
        await wrapper.complete([{'role':'system','content':'{}'}],cfg)
        self.assertIn('_assay_response_format_sha256',capture.cfg)
        self.assertNotIn('_assay_response_format_sha256',cfg)

    def test_paired_schedule_rotates_arms_and_keeps_three_independent_repeats(self):
        cfg=config([variant('C0','fixed_workflow'),variant('C1','single_agent'),variant('C2')],3)
        jobs=schedule([{'case_id':'A','split':'heldout'},{'case_id':'B','split':'heldout'}],cfg)
        self.assertEqual(len(jobs),18)
        self.assertEqual([j['variant'] for j in jobs[:6]],['C0','C1','C2','C1','C2','C0'])
        self.assertEqual(len({j['execution_id'] for j in jobs}),18)

    def test_unrun_failures_and_unknown_labels_stay_in_denominators(self):
        rows=[{'variant':'x','status':status,'elapsed_seconds':seconds,'evaluation':evaluation} for status,seconds,evaluation in
              [('completed',1,{'numeric_success':True,'task_success':None}),('error',3,{'numeric_success':False,'task_success':False}),('unrun',None,{})]]
        result=summarize(rows,planned_jobs=3)['by_variant']['x']
        self.assertEqual(result['planned'],3)
        self.assertEqual(result['numeric_passes_per_planned'],1/3)
        self.assertEqual(result['unadjudicated_task_outcomes'],2)
        self.assertEqual(result['p95_elapsed_seconds'],3)

    def test_all_shipped_configs_validate_and_forecast_without_calls(self):
        from evaluation.final.experiment_runner import VERSION as WORKFLOW_VERSION
        from evaluation.final.paired_security import VERSION as SECURITY_VERSION, validate_config
        for path in Path(__file__).with_name('configs').glob('*.json'):
            with self.subTest(config=path.name):
                raw=json.loads(path.read_text())
                if path.name=='paired_security.json':
                    self.assertEqual(raw.get('schema_version'),SECURITY_VERSION,
                                     'Unknown paired-security configuration schema')
                    self.assertEqual(validate_config(raw),raw)
                else:
                    self.assertEqual(raw.get('schema_version'),WORKFLOW_VERSION,
                                     'Unknown workflow configuration schema')
                    cfg=load_experiment(path)
                    workflow_config_for(cfg)
                    value=forecast(cfg,[{'case_id':'A','split':cfg['splits'][0],'request':REQUEST}])
                    self.assertTrue(value['independent_cache_per_execution'])


class RepresentationTests(unittest.TestCase):
    def test_real_search_units_switch_retains_original_whole_source(self):
        table={'table_id':'T','caption':'Operating performance','column_count':2,'rows':[
            [{'cell_id':'C1','row':0,'column':0,'text':'Operating income','nested_tables':[]},
             {'cell_id':'C2','row':0,'column':1,'text':'25','nested_tables':[]}]]}
        passage={'passage_id':'p_original','ticker':'MSFT','company':'Microsoft','fiscal_year':2025,
            'item':'7','item_heading':'Financial discussion','kind':'table','text':'Flattened filing text',
            'table':table,'context_before':'Performance table','source_sha256':'a'*64,'source_url':'https://www.sec.gov/fixture'}
        class Catalogue:
            manifest_sha='b'*64
            passages={'p_original':passage}
            def check_live(self): pass
            def get(self,pid): return self.passages[pid]
        aware=LocalCorpusBackend(Catalogue(),'.',plain_text=False)
        plain=LocalCorpusBackend(Catalogue(),'.',plain_text=True)
        self.assertIn('Operating income',aware.units[0]['text'])
        self.assertNotIn('Operating income',plain.units[0]['text'])
        self.assertEqual(aware.units[0]['cell_ids'],['C1','C2'])
        self.assertEqual(plain.units[0]['cell_ids'],[])
        self.assertEqual(aware.catalogue.get('p_original'),plain.catalogue.get('p_original'))


if __name__=='__main__':
    unittest.main()
