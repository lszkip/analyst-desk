"""Offline software regressions, not security outcome labels or quality scores."""
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow_contracts import digest, WorkflowError
from workflow_source_safety import source_instruction_findings
from evaluation.final.test_security_experiment import case, ScriptedModel
from evaluation.final.security_experiment import evaluate_condition

FROZEN_FIXTURE=Path(__file__).with_name('test_fixtures')/'paired_security_development_cases.json'
MODULE_PATH=Path(__file__).with_name('paired_security.py')
spec=importlib.util.spec_from_file_location('completion_paired_security',MODULE_PATH)
e2=importlib.util.module_from_spec(spec);spec.loader.exec_module(e2)


def result(item,arm,**extra):
    return {'execution_id':'E2_'+arm+'_'+item['id'],'case_id':item['id'],'case_sha256':digest(item),
        'kind':item['kind'],'role':item['role'],'condition':arm,'evaluable':True,'attempted':True,
        'status':'completed_no_detected_violation','error_code':None,'elapsed_seconds':2.0,**extra}


def label(row,positive):
    return {'execution_id':row['execution_id'],'result_sha256':digest(row),'status':'adjudicated',
        'adjudication_source':'uncalibrated_AI_proposal','ai_reference_opt_in':True,
        'ai_reference_agreement_eligible':True,'calibration_report_sha256':'a'*64,
        ('attack_succeeded' if row['kind']=='attack' else 'false_block'):positive,'human_reviewed':False}


class Ledger:
    def __init__(self):self.states={};self.calls=[]
    def begin_case(self,key):
        if key in self.states:raise WorkflowError('live_interrupted_case','No repeated model call')
        self.states[key]='in_progress';return True
    def finish_case(self,key,status):self.states[key]=status
    def case_states(self):return dict(self.states)
    def stats(self):return {'poisoned':False,'calls':list(self.calls),'case_states':dict(self.states)}


class PairedSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.cases=e2.load_frozen_cases(FROZEN_FIXTURE)

    def test_entire_original_corpus_is_pinned(self):
        self.assertEqual(len(self.cases),60)
        self.assertEqual(sum(c['kind']=='attack' for c in self.cases),40)
        self.assertEqual(sum(c['role']=='researcher' for c in self.cases),36)
        altered=self.folder/'altered.json'
        raw=FROZEN_FIXTURE.read_text()
        altered.write_text(raw+'\n')
        with self.assertRaisesRegex(ValueError,'changed'):e2.load_frozen_cases(altered)

    async def test_actual_toggle_changes_only_source_gate_and_preserves_messages(self):
        item=case();off_model=ScriptedModel(attack=True);on_model=ScriptedModel(attack=True)
        off=await evaluate_condition(item,'quarantine_off',off_model,self.folder/'off')
        on=await evaluate_condition(item,'quarantine_on',on_model,self.folder/'on')
        self.assertTrue(off['accepted_final']);self.assertFalse(on['accepted_final'])
        self.assertEqual(off_model.calls[0],on_model.calls[0])
        self.assertEqual(off['retained_boundaries'],on['retained_boundaries'])
        self.assertFalse(off['unauthorized_backend_dispatch']);self.assertFalse(on['unauthorized_backend_dispatch'])
        self.assertTrue(source_instruction_findings(item['source_text']))

    async def test_cancelled_off_arm_restores_production_source_gate(self):
        class NeverReturns:
            async def complete(self,*args):await asyncio.Future()
        item=case()
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(evaluate_condition(item,'quarantine_off',NeverReturns(),self.folder/'cancel'),timeout=.02)
        self.assertTrue(source_instruction_findings(item['source_text']))
        import workflow_support
        self.assertTrue(workflow_support.source_instruction_findings(item['source_text']))

    def test_attack_and_clean_rates_keep_error_unrun_and_adjudicated_denominators(self):
        a=case();b=case(False);c=deepcopy(a);c['id']='SECOND_ATTACK'
        off_a=result(a,'quarantine_off');on_a=result(a,'quarantine_on')
        off_b=result(b,'quarantine_off');on_b=result(b,'quarantine_on')
        failed=result(c,'quarantine_on',status='timeout',evaluable=False,error_code='case_timeout')
        rows=[off_a,on_a,off_b,on_b,failed]
        labels={r['execution_id']:label(r,value) for r,value in [(off_a,True),(on_a,False),(off_b,False),(on_b,True),(failed,False)]}
        report=e2.paired_summary([a,b,c],rows,labels)
        off=report['conditions']['quarantine_off'];on=report['conditions']['quarantine_on']
        self.assertEqual(off['outcomes']['attack']['rate'],1.0)
        self.assertEqual(on['outcomes']['attack']['rate'],0.0)
        self.assertEqual(on['outcomes']['attack']['adjudicated'],1) # injected timeout label excluded
        self.assertEqual(on['outcomes']['attack']['planned'],2)
        self.assertEqual(on['outcomes']['attack']['planned_population_rate_bounds'],{'lower':0.0,'upper':0.5})
        self.assertEqual(off['not_run'],1);self.assertEqual(on['timeouts'],1)
        self.assertEqual(on['outcomes']['clean']['rate'],1.0)
        self.assertEqual(report['paired_effects']['writer']['attack']['on_minus_off_rate_difference'],-1.0)
        self.assertEqual(report['paired_effects']['writer']['clean']['on_minus_off_rate_difference'],1.0)
        self.assertFalse(report['human_review_claim'])

    def test_adjudication_must_match_result_and_have_explicit_allowed_origin(self):
        item=case();row=result(item,'quarantine_off');bad=label(row,True)
        bad['result_sha256']='0'*64
        report=e2.paired_summary([item],[row],{row['execution_id']:bad})
        self.assertIsNone(report['conditions']['quarantine_off']['outcomes']['attack']['rate'])
        bad=label(row,True);bad['ai_reference_opt_in']=False
        report=e2.paired_summary([item],[row],{row['execution_id']:bad})
        self.assertEqual(report['conditions']['quarantine_off']['outcomes']['attack']['adjudicated'],0)

    def test_failed_calls_and_judging_are_in_cost_totals(self):
        stats={'calls':[{'case_id':'task','model':'gpt','reserved_nano_usd':1000,'actual_nano_usd':300},
            {'case_id':'task','model':'gpt','reserved_nano_usd':1000,'actual_nano_usd':None},
            {'case_id':'judge','model':'llama','reserved_nano_usd':2000,'actual_nano_usd':800},
            {'case_id':'other','model':'gpt','reserved_nano_usd':99999,'actual_nano_usd':99999}]}
        report=e2.cost_report(stats,['task'],['judge'])
        self.assertEqual(report['reserved_requests'],3)
        self.assertEqual(report['reserved_usd'],'0.000004')
        self.assertEqual(report['known_actual_usd'],'0.0000011')
        self.assertEqual(report['requests_without_actual_cost'],1)
        self.assertFalse(report['cost_complete'])

    async def test_resume_never_repeats_finalized_security_arms(self):
        ledger=Ledger();calls=[]
        async def stage(item,arm,model,workspace,config):
            calls.append((item['id'],arm));return result(item,arm)
        with patch.object(e2.assay,'evaluate_condition',side_effect=stage):
            first=await e2.run_paired_security(self.cases,lambda *a:object(),self.folder/'run',ledger)
            second=await e2.run_paired_security(self.cases,lambda *a:self.fail('Must not repeat finalized calls'),self.folder/'run',ledger)
        self.assertEqual(len(calls),120)
        self.assertEqual(first['summary']['planned_executions'],120)
        self.assertEqual(len(second['results']),120)
        self.assertEqual(calls[:4],[(self.cases[0]['id'],'quarantine_off'),(self.cases[0]['id'],'quarantine_on'),
                                   (self.cases[1]['id'],'quarantine_on'),(self.cases[1]['id'],'quarantine_off')])

    async def test_partial_budget_stop_keeps_missing_pairs_visible(self):
        ledger=Ledger()
        async def stage(item,arm,model,workspace,config):
            if arm=='quarantine_on':return result(item,arm,status='technical_error',evaluable=False,error_code='live_budget_exhausted')
            return result(item,arm)
        with patch.object(e2.assay,'evaluate_condition',side_effect=stage):
            output=await e2.run_paired_security(self.cases,lambda *a:object(),self.folder/'limited',ledger)
        self.assertEqual(output['stop_code'],'live_budget_exhausted')
        self.assertEqual(len(output['results']),2)
        self.assertEqual(len(output['summary']['unrun_execution_ids']),118)
        self.assertEqual(output['summary']['conditions']['quarantine_on']['outcomes']['attack']['adjudicated'],0)

    async def test_interrupted_ledger_case_is_not_reexecuted(self):
        ledger=Ledger();first=self.cases[0]
        execution='E2_quarantine_off_'+first['id'];ledger.states[execution]='in_progress'
        async def stage(item,arm,model,workspace,config):
            return result(item,arm,status='technical_error',evaluable=False,error_code='live_budget_exhausted')
        with patch.object(e2.assay,'evaluate_condition',side_effect=stage):
            output=await e2.run_paired_security(self.cases,lambda *a:object(),self.folder/'interrupted',ledger)
        self.assertEqual(output['results'][0]['status'],'interrupted_unevaluable')
        self.assertEqual(ledger.states[execution],'interrupted_unevaluable')

    async def test_modified_cases_cannot_claim_frozen_corpus_identity(self):
        modified=deepcopy(self.cases);modified[0]['task']='Altered task'
        with self.assertRaisesRegex(ValueError,'frozen'):
            await e2.run_paired_security(modified,lambda *a:object(),self.folder/'bad',Ledger())


if __name__=='__main__':unittest.main()
