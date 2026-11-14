"""Synthetic protocol/integration checks; never model-quality evidence."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from workflow_agents import load_config
from workflow_contracts import digest, canonical, WorkflowError
from evaluation.final import completion_operator as c
from evaluation.final.memory_adapter import make_memory_executor
from evaluation.final.test_experiment_runner import SingleAwareModel, REQUEST
from test_research_workflow import FixtureBackend


def cases():
    return [{'case_id':'H%03d'%i,'split':'heldout','request':deepcopy(REQUEST)} for i in range(40)]+[
        {'case_id':key,'split':'dev','request':deepcopy(REQUEST)} for key in ['F002','F003']]+[
        {'case_id':'D%03d'%i,'split':'dev','request':deepcopy(REQUEST)} for i in range(18)]


def configs():
    folder=Path(c.__file__).with_name('configs')
    return (c.load_experiment(folder/'completion_matrix.json'),c.load_experiment(folder/'memory_matrix.json'))


def scope(ai_only=False):
    matrix,memory=configs()
    lesson=None if ai_only else {'manifest_sha256':'b'*64,'lessons':[{'test':'synthetic'}]}
    return c.prepare_scope(cases(),matrix,memory,bundle_sha256='a'*64,source_hashes={},review={},
        lesson_manifest=lesson,memory_sha256='c'*64,security_sha256='d'*64,ai_only=ai_only)


class ScopeTests(unittest.TestCase):
    def test_displayed_cumulative_limits_match_the_amendment_enforcer(self):
        from evaluation.final.completion_budget import COMPLETION_LIMITS
        self.assertEqual(c.PROPOSED_LIMITS,COMPLETION_LIMITS)
    def test_all_heldout_repeats_and_separate_representation_controls(self):
        plan=scope();self.assertEqual(plan['counts']['heldout_workflow_executions'],840)
        self.assertEqual(plan['counts']['memory_workflow_executions'],12)
        self.assertEqual(plan['counts']['development_sanity_executions'],6)
        self.assertEqual(plan['counts']['additional_development_executions'],18)
        self.assertEqual(plan['counts']['unique_workflow_cases'],60)
        jobs=c.schedule(c.select_cases(cases(),plan['matrix']),plan['matrix'])
        self.assertEqual(len({j['execution_id'] for j in jobs}),840)
        self.assertEqual(sum(j['variant']=='C2' for j in jobs),120)
        self.assertEqual({v['retrieval'] for v in plan['matrix']['variants'] if v['id'] in {'C2_table_bm25','C2_plain_bm25'}},
            {'table_aware_bm25','plain_text_bm25'})
    def test_e1_baselines_have_no_separate_model_critic(self):
        matrix,memory=configs()
        by_id={v['id']:v for v in matrix['variants']}
        self.assertFalse(by_id['C0']['critic']);self.assertFalse(by_id['C1']['critic'])
        self.assertTrue(by_id['C2']['critic']);self.assertFalse(by_id['C2_no_critic']['critic'])
        for baseline in ['C0','C1']:
            invalid=deepcopy(matrix)
            next(v for v in invalid['variants'] if v['id']==baseline)['critic']=True
            with self.assertRaises(WorkflowError):
                c.prepare_scope(cases(),invalid,memory,bundle_sha256='a'*64,source_hashes={},review={},
                    memory_sha256='c'*64,security_sha256='d'*64,ai_only=True)
    def test_missing_human_lessons_cannot_be_claimed_as_e3(self):
        matrix,memory=configs()
        with self.assertRaises(WorkflowError):c.prepare_scope(cases(),matrix,memory,bundle_sha256='a'*64,
            source_hashes={},review={},memory_sha256='c'*64,security_sha256='d'*64)
        plan=scope(True);self.assertEqual(plan['e3_status'],'not_run_missing_human_approved_playbook')
        self.assertEqual(plan['counts']['heldout_workflow_executions'],720)
        self.assertFalse(any(v['lessons'] for v in plan['matrix']['variants']))
        self.assertFalse(c.comparison_summaries([],{},lessons_available=False)['E3']['minimum_bar_met'])
        rejected=c.prepare_scope(cases(),matrix,memory,bundle_sha256='a'*64,source_hashes={},review={},
            lesson_manifest={'lessons':[],'manifest_sha256':'e'*64},memory_sha256='c'*64,
            security_sha256='d'*64,ai_only=True,human_review={'actual_human_labels':20})
        self.assertEqual(rejected['e3_status'],'not_run_missing_human_approved_playbook')
        self.assertEqual(rejected['counts']['support_judge_calibration_items'],24)
    def test_no_silent_case_limit_or_repeat_reduction(self):
        matrix,memory=configs()
        for change in [{'case_limit':10},{'repeats':1},{'splits':['dev']}]:
            cfg={**matrix,**change}
            with self.assertRaises(WorkflowError):c.prepare_scope(cases(),cfg,memory,bundle_sha256='a'*64,
                source_hashes={},review={},memory_sha256='c'*64,security_sha256='d'*64,ai_only=True)
    def test_preview_displays_phase_priority_and_exact_frozen_queue(self):
        plan=scope()
        with patch.object(c,'forecast',return_value={'uncapped_queue_conservative_usd_upper_bound':'1'}):
            displayed=c.preview(plan,cases(),{}, {'reserved_usd':.7,'known_actual_usd':.1,'reserved_requests':83})
        self.assertLess(displayed['phase_order'].index('matrix'),displayed['phase_order'].index('security'))
        self.assertEqual(len(displayed['heldout_case_order']),40)
        self.assertEqual(displayed['heldout_queue_policy'],c.SCHEDULING)
        self.assertEqual(displayed['cumulative_limits'],plan['proposed_cumulative_limits'])
    def test_scope_approval_binds_plan_existing_run_and_original_approval(self):
        plan=scope();registry={'batch_id':'existing','run_uuid':'unchanged'};old={'approved':True}
        with self.assertRaises(WorkflowError):c.approve_scope('',plan,registry,old)
        with self.assertRaises(WorkflowError):c.approve_scope('YES',plan,registry,old)
        approved=c.approve_scope('APPROVE',plan,registry,old)
        self.assertEqual(approved['scope_sha256'],plan['scope_sha256'])
        self.assertEqual(approved['run_uuid'],'unchanged')
        self.assertEqual(approved['original_approval_sha256'],digest(old))
        self.assertEqual(approved['cumulative_limits_sha256'],digest(plan['proposed_cumulative_limits']))
    def test_missing_existing_ledger_never_creates_fresh_allowance(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=root/'state';(state/'private').mkdir(parents=True)
            with self.assertRaises(WorkflowError):c.existing_run(root,state)
            self.assertFalse((state/'private'/'Budget.sqlite3').exists())
    def test_exact_scope_persistence_refuses_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'scope.json';c.exact_file(path,scope());before=path.read_bytes()
            c.exact_file(path,scope())
            with self.assertRaises(WorkflowError):c.exact_file(path,scope(True))
            self.assertEqual(path.read_bytes(),before)
    def test_pilot_developer_review_requires_actual_evidence_and_software_pins(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);pilot=root/'pilot';pilot.mkdir()
            for name in ['Execution_Results.json','Experiment_Summary.json']:(pilot/name).write_text('{}')
            (root/'module.py').write_text('x=1\n')
            review={'schema_version':'completion-pilot-review-v1','decision':'proceed','reviewer_kind':'ai_developer',
                'pilot_results_sha256':c.sha_file(pilot/'Execution_Results.json'),
                'pilot_summary_sha256':c.sha_file(pilot/'Experiment_Summary.json'),
                'frozen_software_sha256':{'module.py':c.sha_file(root/'module.py')}}
            result=c.verify_pilot_review(root,review,root)
            self.assertFalse(result['semantic_human_labels'])
            (root/'module.py').write_text('x=2\n')
            with self.assertRaises(WorkflowError):c.verify_pilot_review(root,review,root)
    def test_symlink_input_and_competing_lock_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'private').mkdir();(root/'x').write_text('{}');(root/'link').symlink_to(root/'x')
            with self.assertRaises(WorkflowError):c.read_json(root/'link')
            with c.completion_lock(root):
                with self.assertRaises(WorkflowError):
                    with c.completion_lock(root):self.fail('Cannot hold second scope lock')
    def test_only_proven_local_limit_is_a_bounded_readiness_failure(self):
        row={'execution_id':'case','status':'error','error':{'code':'budget_exhausted'},
            'metrics':{'llm_requests_started':1,'cost_complete':True},'evaluation':{'numeric_success':False}}
        jobs=[{'execution_id':'case'}];healthy={'poisoned':False,'requests_without_actual_cost':0,'in_flight_requests':0}
        self.assertFalse(c.sanity_readiness([row],jobs,healthy)['ready'])
        original=deepcopy(row);proof={'case':{'classification':'verified_case_local_budget_exhaustion'}}
        report=c.sanity_readiness([row],jobs,healthy,proof)
        self.assertTrue(report['ready']);self.assertEqual(report['numeric_passes'],0);self.assertEqual(row,original)
        self.assertFalse(c.sanity_readiness([row],jobs,{**healthy,'poisoned':True},proof)['ready'])

    def test_readiness_keeps_known_quality_failures_instead_of_requiring_perfection(self):
        jobs=[{'execution_id':'dev1'},{'execution_id':'dev2'}]
        rows=[{'execution_id':'dev1','status':'blocked','evaluation':{'numeric_success':False},
               'metrics':{'llm_requests_started':1,'cost_complete':True}},
              {'execution_id':'dev2','status':'completed','evaluation':{'numeric_success':True},
               'metrics':{'llm_requests_started':1,'cost_complete':True}}]
        report=c.sanity_readiness(rows,jobs,{'poisoned':False,'requests_without_actual_cost':0,'in_flight_requests':0})
        self.assertTrue(report['ready']);self.assertEqual(report['blocked'],1);self.assertEqual(report['numeric_passes'],1)
        rows[0]['metrics']['cost_complete']=False
        self.assertFalse(c.sanity_readiness(rows,jobs)['ready'])


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_interruption_exports_latest_shared_ledger_costs(self):
        class Ledger:
            def stats(self):return {'reserved_requests':91,'known_actual_usd':0.19,'reserved_usd':1.2}
        async def interrupted(args,**kwargs):
            kwargs['_session']['ledger']=Ledger();raise asyncio.CancelledError()
        with tempfile.TemporaryDirectory() as folder,patch.object(c,'_operate',interrupted):
            with self.assertRaises(asyncio.CancelledError):await c.operate(SimpleNamespace(state=folder))
            report=c.read_json(Path(folder)/'completion'/'Completion_Budget_Report.json')
            self.assertEqual(report['reserved_requests'],91);self.assertEqual(report['known_actual_usd'],0.19)
    async def test_failed_corrected_development_gate_never_starts_heldout(self):
        plan=scope(True);called=[]
        async def runner(cs,cfg,output,**kwargs):
            called.append(cfg['experiment_id']);Path(output).mkdir(parents=True)
            c.atomic_json(Path(output)/'Execution_Results.json',[{'status':'error','error':{'code':'model_http_error'}}]*6)
            return {'status_counts':{'error':6}}
        with tempfile.TemporaryDirectory() as folder, patch.object(c,'run_experiment',runner):
            result=await c.execute_scope(plan,cases(),[],folder,ledger=None,model_factory=lambda *a:None,
                backend_factory=lambda *a:None,software_test_mode=True)
        self.assertEqual(called,['COMPLETION_DEV']);self.assertFalse(result['heldout_started'])
        self.assertFalse(result['all_minimum_bar_rows_met'])
    async def test_source_change_stops_before_paid_client(self):
        class Never:
            async def complete(self,*args):raise AssertionError('Paid delegate must not run')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'module.py';path.write_text('old');expected=c.sha_file(path)
            model=c.PinnedModel(Never(),folder,{'module.py':expected});path.write_text('new')
            with self.assertRaises(WorkflowError):await model.complete([], {})
    async def test_phases_share_one_ledger_and_security_patch_is_not_concurrent(self):
        plan=scope(True);events=[]
        class Ledger:
            def stats(self):return {'poisoned':False,'requests_without_actual_cost':0,'in_flight_requests':0}
        ledger=Ledger()
        async def runner(cs,cfg,output,**kwargs):
            self.assertIs(kwargs['ledger'],ledger);events.append(cfg['experiment_id'])
            rows=[{**j,'status':'blocked','evaluation':{'numeric_success':False},
                   'metrics':{'llm_requests_started':1,'cost_complete':True}}
                  for j in c.schedule(c.select_cases(cs,cfg),cfg)]
            Path(output).mkdir(parents=True);c.atomic_json(Path(output)/'Execution_Results.json',rows)
            return {'planned_executions':len(rows)}
        async def calibration(current,output):self.assertIs(current,ledger);events.append('calibration');return {'eligible':False}
        async def security(cs,factory,output,current,**kwargs):
            self.assertIs(current,ledger);events.append('security');return {'pending_adjudication':120}
        with tempfile.TemporaryDirectory() as folder,patch.object(c,'run_experiment',runner):
            result=await c.execute_scope(plan,cases(),[],folder,ledger=ledger,model_factory=lambda *a:None,
                backend_factory=lambda *a:None,security_runner=security,security_cases=[],calibration_runner=calibration)
        self.assertEqual(events,['COMPLETION_DEV','COMPLETION_DEV_COVERAGE','calibration','COMPLETION','MEMORY','security'])
        self.assertFalse(result['all_minimum_bar_rows_met'])
        self.assertEqual(result['development_readiness']['blocked'],6)
    async def test_memory_treatment_enters_production_context_before_model(self):
        contexts={digest(REQUEST):{'off':{'enabled':False,'past_notes':[]},
            'on':{'enabled':True,'past_notes':[{'status':'historical_not_current','summary':'SYNTHETIC_PRIOR_NOTE'}]}}}
        execute=make_memory_executor(contexts)
        with tempfile.TemporaryDirectory() as folder:
            seen={}
            for enabled in [False,True]:
                name='on' if enabled else 'off';model=SingleAwareModel()
                variant={'id':name,'architecture':'team','critic':True,'lessons':False,
                         'retrieval':'production_hybrid','memory':enabled}
                state=await execute(REQUEST,variant,load_config(),model,FixtureBackend(Path(folder)/name),
                    Path(folder)/name/'execution',name)
                self.assertEqual(state['status'],'completed')
                self.assertEqual(state['memory_enabled'],enabled)
                seen[name]=canonical(model.message_content)
            self.assertIn('SYNTHETIC_PRIOR_NOTE',seen['on']);self.assertNotIn('SYNTHETIC_PRIOR_NOTE',seen['off'])
        self.assertEqual(contexts[digest(REQUEST)]['on']['past_notes'][0]['summary'],'SYNTHETIC_PRIOR_NOTE')


class FullSourceBindingTests(unittest.TestCase):
    def test_operator_delegates_to_full_canonical_binding(self):
        from evaluation.final import completion_source_binding
        self.assertIs(c.bind_completion_sources,completion_source_binding.bind_completion_sources)



class CompletionQueueTests(unittest.TestCase):
    def test_balanced_case_blocks_preserve_every_original_job(self):
        from evaluation.final.experiment_runner import schedule as original_schedule
        matrix,_=configs();selected=cases()[:40]
        for i,item in enumerate(selected):item['request']['include_risks']=bool(i%2)
        jobs=c.schedule(selected,matrix);old=original_schedule(selected,matrix)
        self.assertEqual({r['execution_id'] for r in jobs},{r['execution_id'] for r in old})
        self.assertEqual(len(jobs),840)
        order=[]
        for index in range(0,len(jobs),21):
            block=jobs[index:index+21];self.assertEqual(len({r['case_id'] for r in block}),1)
            self.assertEqual({r['repeat'] for r in block},{1,2,3});order.append(block[0]['case_id'])
            for repeat in (1,2,3):self.assertEqual(len({r['variant'] for r in block if r['repeat']==repeat}),7)
        by_id={r['case_id']:r for r in selected}
        self.assertEqual([by_id[key]['request']['include_risks'] for key in order],[False,True]*20)
        self.assertEqual(jobs,c.schedule(list(reversed(selected)),matrix))
        for row in selected:row['irrelevant_test_outcome']='must not affect ordering'
        self.assertEqual(jobs,c.schedule(selected,matrix))
        self.assertNotEqual(jobs[0]['variant'],jobs[7]['variant'])


class PreviewHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.new=scope();self.old=deepcopy(self.new)
        self.old.pop('phase_order');self.old['matrix'].pop('scheduling')
        self.old['matrix']['interpretation']='Prior prespecified repeat-major ordering'
        self.old['pilot_review']={'prior_review':'synthetic'}
        self.old['scope_sha256']=digest({k:v for k,v in self.old.items() if k!='scope_sha256'})
        self.before=json.dumps(self.old,indent=4).encode();(self.root/'Completion_Scope.json').write_bytes(self.before)
        self.preview={'status':'awaiting_explicit_approval','receipt':None,'plan_sha256':self.new['scope_sha256'],
            'current_stats':{'case_states':{'PILOT_C0_F002_r1':'completed','FINAL34_JUDGE_H01':'complete'},
                'reserved_requests':83,'original_started_at':'original-clock','poisoned':False,'requests_without_actual_cost':0,'in_flight_requests':0}}
    def test_unapproved_preview_archived_exactly_and_idempotent(self):
        c.persist_scope_with_preview_handoff(self.root,self.new,self.preview)
        archived=self.root/'private'/'preview_archive'/self.old['scope_sha256']/'Completion_Scope.json'
        self.assertEqual(archived.read_bytes(),self.before)
        self.assertEqual(c.read_json(self.root/'Completion_Scope.json'),self.new)
        receipt=(self.root/'Preview_Handoff.json').read_bytes()
        c.persist_scope_with_preview_handoff(self.root,self.new,self.preview)
        self.assertEqual((self.root/'Preview_Handoff.json').read_bytes(),receipt)
    def test_approved_amended_or_attempted_scope_never_replaced(self):
        for name in ['private/Scope_Approval.json','private/Budget_Amendment.json','matrix','calibration']:
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{}')
            with self.subTest(name=name),self.assertRaises(WorkflowError):c.persist_scope_with_preview_handoff(self.root,self.new,self.preview)
            self.assertEqual((self.root/'Completion_Scope.json').read_bytes(),self.before);path.unlink()
        self.preview['status']='already_amended'
        with self.assertRaises(WorkflowError):c.persist_scope_with_preview_handoff(self.root,self.new,self.preview)
    def test_new_scope_calls_or_unsettled_accounting_refuse_handoff(self):
        for alteration in [{'case_states':{'COMPLETION_C2_H001_r1':'completed'}},{'in_flight_requests':1},{'poisoned':True}]:
            check=deepcopy(self.preview);check['current_stats'].update(alteration)
            with self.subTest(alteration=alteration),self.assertRaises(WorkflowError):c.persist_scope_with_preview_handoff(self.root,self.new,check)
        self.assertEqual((self.root/'Completion_Scope.json').read_bytes(),self.before)
    def test_handoff_cannot_change_cases_labels_or_budget(self):
        changed=deepcopy(self.new);changed['proposed_cumulative_limits']['max_reserved_usd']='151.00'
        changed['scope_sha256']=digest({k:v for k,v in changed.items() if k!='scope_sha256'})
        check={**self.preview,'plan_sha256':changed['scope_sha256']}
        with self.assertRaises(WorkflowError):c.persist_scope_with_preview_handoff(self.root,changed,check)
        self.assertEqual((self.root/'Completion_Scope.json').read_bytes(),self.before)
    def test_tampered_old_preview_or_unsafe_archive_is_rejected(self):
        old=deepcopy(self.old);old['counts']['unique_workflow_cases']=1
        (self.root/'Completion_Scope.json').write_text(json.dumps(old))
        with self.assertRaises(WorkflowError):c.persist_scope_with_preview_handoff(self.root,self.new,self.preview)
        (self.root/'Completion_Scope.json').write_bytes(self.before);(self.root/'outside').mkdir();(self.root/'private').symlink_to(self.root/'outside')
        with self.assertRaises(WorkflowError):c.persist_scope_with_preview_handoff(self.root,self.new,self.preview)


class SoftwareCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.repo=self.root/'repo';self.state=self.root/'state';self.output=self.state/'completion';self.repo.mkdir();(self.state/'pilot').mkdir(parents=True)
        for name in ('Execution_Results.json','Experiment_Summary.json'):(self.state/'pilot'/name).write_text('{}')
        pins={}
        for name in c.SOFTWARE_DELTA|{'unchanged.py'}:
            path=self.repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('old');pins[name]=c.sha_file(path)
        self.old={'schema_version':'completion-pilot-review-v1','decision':'proceed','reviewer_kind':'ai_developer',
            'pilot_results_sha256':c.sha_file(self.state/'pilot/Execution_Results.json'),
            'pilot_summary_sha256':c.sha_file(self.state/'pilot/Experiment_Summary.json'),'frozen_software_sha256':pins}
        derived={'review_sha256':digest(self.old),'reviewer_kind':'ai_developer','semantic_human_labels':False,'software_sha256':pins}
        self.scope={'pilot_review':derived,'proposed_cumulative_limits':c.PROPOSED_LIMITS};self.scope['scope_sha256']=digest(self.scope)
        self.scopehash=self.scope['scope_sha256'];self.patch=patch.object(c,'APPROVED_43_SCOPE',self.scopehash);self.patch.start();self.addCleanup(self.patch.stop)
        c.exact_file(self.output/'Completion_Scope.json',self.scope)
        c.exact_file(self.output/'private/Scope_Approval.json',{'scope_sha256':self.scopehash})
        c.exact_file(self.output/'private/Budget_Amendment.json',{'plan_sha256':self.scopehash})
        current=dict(pins)
        for name in c.SOFTWARE_DELTA:(self.repo/name).write_text('new');current[name]=c.sha_file(self.repo/name)
        records={}
        names=[f'completion/development_sanity/executions/COMPLETION_DEV_{arm}_{case}_r1/Result.json' for arm in ('C0','C1','C2') for case in ('F002','F003')]
        names += [f'completion/development_coverage/executions/COMPLETION_DEV_COVERAGE_C2_F{i:03d}_r1/Result.json' for i in range(1,21) if i not in (2,3)]
        names += ['completion/development_sanity/Experiment_Manifest.json','completion/development_coverage/Experiment_Manifest.json','completion/development_coverage/executions/COMPLETION_DEV_COVERAGE_C2_F020_r1/Journal_Report.json','completion/Development_Readiness.json','completion/Development_Coverage_Readiness.json']
        for name in names:c.exact_file(self.state/name,{'synthetic':'unchanged'});records[name]=c.sha_file(self.state/name)
        self.proof={'schema_version':'completion-software-compatibility-v1','review_basis':'development_only_case_local_budget_classification',
            'original_scope_unchanged':True,'original_pilot_review':self.old,'approved_scope_sha256':self.scopehash,
            'cumulative_limits_sha256':digest(c.PROPOSED_LIMITS),'runtime_software_sha256':current,'original_records_sha256':records}
        self.path=self.root/'review.json';self.save()
    def save(self):self.path.write_text(json.dumps(self.proof));self.sha=c.sha_file(self.path)
    def verify(self):return c.verify_software_compatibility(self.state,self.repo,self.path,self.sha,self.old)
    def test_transition_keeps_original_scope_approval_results_and_uses_new_pins(self):
        before={str(p):p.read_bytes() for p in self.state.rglob('*.json')}
        original,pins=self.verify();self.assertEqual(original,self.scope['pilot_review']);self.assertEqual(pins,self.proof['runtime_software_sha256'])
        for path,raw in before.items():self.assertEqual(Path(path).read_bytes(),raw)
        c.atomic_json(self.output/'Development_Coverage_Readiness.json',{'reviewed':'new classification'})
        (self.output/'matrix').mkdir();self.assertEqual(self.verify(),(original,pins))
    def test_changed_extra_source_or_missing_record_is_rejected(self):
        (self.repo/'unchanged.py').write_text('changed')
        with self.assertRaises(WorkflowError):self.verify()
        (self.repo/'unchanged.py').write_text('old')
        self.proof['original_records_sha256'].pop(next(iter(self.proof['original_records_sha256'])));self.save()
        with self.assertRaises(WorkflowError):self.verify()
    def test_scope_limits_receipt_tamper_and_later_attempts_are_rejected(self):
        (self.output/'matrix').mkdir()
        with self.assertRaises(WorkflowError):self.verify()
        (self.output/'matrix').rmdir();self.verify()
        c.atomic_json(self.output/'Software_Compatibility_Review.json',{'tampered':True})
        with self.assertRaises(WorkflowError):self.verify()


if __name__=='__main__':unittest.main()
