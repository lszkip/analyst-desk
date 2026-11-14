"""Synthetic unit fixtures, never human labels or model-quality evidence."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from workflow_contracts import digest, WorkflowError
from . import completion_semantics as s

REQUEST = {'question':'Compare operating margins and cybersecurity risks for the requested periods.', 'tickers':['MSFT'], 'years':[2024,2025], 'include_risks':True}
PROPOSAL = {'label':'supported','reason':'The cited source supports the stated possibility.'}


def calibration():
    return {'eligible_for_automated_semantic_scoring':True,'judge_model':s.JUDGE_MODEL,
        'human_review_attested':True,'actual_human_labels':24,'judge_revision':'support-only-v2',
        'judge_prompt_sha256':s.CALIBRATION_PROMPT_SHA256,'binary_nonuncertain':{'n':24,'cohen_kappa':0.75}}


def fixture():
    case = {'case_id':'F003','request':deepcopy(REQUEST),'private_gold':'DO_NOT_SEND_GOLD'}
    paragraph = {'text':'Cyberattacks could disrupt services.','evidence_ids':['p_test']}
    draft = {'title':'Risk comparison','paragraphs':[paragraph]}
    passage = {'passage_id':'p_test','text':'Cyberattacks could disrupt services. FULL_SOURCE_TAIL',
        'ticker':'MSFT','fiscal_year':2024,'item':'1A','period_start':'2023-07-01','period_end':'2024-06-30',
        'source_sha256':'a'*64,'source_url':'https://example.invalid/filing'}
    record = {'kind':'passage','ticker':'MSFT','year':2024,'item':'1A','source_sha256':'a'*64,
              'source_url':passage['source_url'],'passage':passage}
    state = {'request':deepcopy(REQUEST),'risk_draft':draft,'note':{'title':draft['title'],
        'body_markdown':paragraph['text']+' [p_test]', 'citations':[{'evidence_id':'p_test','source_sha256':'a'*64,'source_url':passage['source_url']}]}}
    row = {'execution_id':'COMPLETION_C2_F003_r1','case_id':'F003','variant':'C2','repeat':1,'status':'completed',
        'result':state,'evaluation':{'numeric_success':True,'task_success':None,'private_expectation':'DO_NOT_SEND_EXPECTATION'}}
    return case,row,{'p_test':record,'uncited':{'text':'DO_NOT_SEND_UNCITED'}}


class Ledger:
    def __init__(self): self.states={}; self.started=0
    def case_states(self): return dict(self.states)
    def begin_case(self,key):
        if key in self.states: return False
        self.states[key]='in_progress'; self.started+=1; return True
    def finish_case(self,key,status): self.states[key]=status


class Model:
    def __init__(self,proposal=None): self.proposal=deepcopy(proposal or PROPOSAL); self.calls=[]
    async def complete(self,messages,config):
        self.calls.append((messages,config))
        return {'model':s.JUDGE_MODEL,'content':json.dumps(self.proposal),'usage':{'total_tokens':50}}


class ContextTests(unittest.TestCase):
    def test_only_actual_request_delivered_draft_and_full_cited_passage(self):
        case,row,records=fixture(); context=s.build_context(case,row,records); text=json.dumps(context)
        self.assertIn('FULL_SOURCE_TAIL',text)
        for hidden in ['DO_NOT_SEND_GOLD','DO_NOT_SEND_EXPECTATION','DO_NOT_SEND_UNCITED']:
            self.assertNotIn(hidden,text)
        self.assertEqual(context['cited_full_passages'][0]['fiscal_year'],2024)
    def test_changed_period_source_or_undelivered_draft_is_rejected(self):
        for change in ['period','source','delivery','request']:
            case,row,records=fixture()
            if change=='period': records['p_test']['passage']['fiscal_year']=2025
            elif change=='source': records['p_test']['source_sha256']='b'*64
            elif change=='delivery': row['result']['note']['body_markdown']='different'
            else: row['result']['request']['question']='Different request'
            with self.assertRaises(ValueError): s.build_context(case,row,records)
    def test_oversize_full_text_is_not_truncated_or_sent(self):
        case,row,records=fixture();records['p_test']['passage']['text']='x'*s.MAX_CONTEXT_BYTES
        with self.assertRaises(WorkflowError):s.build_context(case,row,records)
    def test_calibration_requires_actual_human_v2_and_threshold(self):
        self.assertTrue(s.calibration_ready(calibration()))
        for change in [{'human_review_attested':False},{'judge_revision':'original'},
                       {'actual_human_labels':19},{'binary_nonuncertain':{'n':24,'cohen_kappa':0.59}}]:
            self.assertFalse(s.calibration_ready({**calibration(),**change}))
    def test_topic_omission_does_not_silently_change_support(self):
        value=deepcopy(PROPOSAL)
        self.assertEqual(s.validate_proposal(value)['label'],'supported')
        self.assertEqual(s.PROMPT_SHA256,s.CALIBRATION_PROMPT_SHA256)
        value['task_success']=True
        with self.assertRaises(ValueError):s.validate_proposal(value)


class RunTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_adapter_output_and_evidence_fit_semantic_context(self):
        from workflow_agents import load_config
        from workflow_store import RunStore
        from .adapters import execute_variant
        from .test_experiment_runner import SingleAwareModel, variant
        from test_research_workflow import FixtureBackend
        with tempfile.TemporaryDirectory() as directory:
            for name,architecture in [('C0','fixed_workflow'),('C1','single_agent'),('C2','team')]:
                folder=Path(directory)/name
                request={**REQUEST,'years':[2025]}
                state=await execute_variant(request,variant(name,architecture),load_config(),SingleAwareModel(),
                    FixtureBackend(folder),folder/'run',name)
                self.assertEqual(state['status'],'completed')
                records=RunStore(folder/'run'/'journal.sqlite3').evidence(name)
                context=s.build_context({'request':request},{'result':state},records)
                self.assertTrue(context['cited_full_passages'])
    async def run_fixture(self,root,case,row,records,ledger,model,cal=None):
        matrix=root/'matrix';out=root/'semantics';(matrix/'executions'/row['execution_id']).mkdir(parents=True,exist_ok=True)
        (matrix/'Execution_Results.json').write_text(json.dumps([row]));(matrix/'executions'/row['execution_id']/'Evidence.json').write_text(json.dumps(records))
        result=await s.run_completion_semantics([case],matrix,ledger=ledger,model_factory=lambda *a:model,
            calibration=cal if cal is not None else calibration(),output=out)
        return result,out
    async def test_one_paid_attempt_resumes_without_replay_or_gold_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);case,row,records=fixture();before=deepcopy(row);ledger=Ledger();model=Model()
            first,out=await self.run_fixture(root,case,row,records,ledger,model)
            second,_=await self.run_fixture(root,case,row,records,ledger,model)
            self.assertEqual(first,second);self.assertEqual(len(model.calls),1);self.assertEqual(row,before)
            self.assertEqual(first['code_and_support_proposal']['true'],1)
            self.assertIsNone(first['rows'][0]['frozen_gold_task_success'])
            self.assertNotIn('DO_NOT_SEND_GOLD',json.dumps(model.calls))
            self.assertEqual(model.calls[0][0][0]['content'],s.SUPPORT_ONLY_PROMPT)
            self.assertEqual(set(json.loads(model.calls[0][0][1]['content'])),{'question','question_context','source_text','answer'})
    async def test_uncertain_remains_unknown_not_failed_or_successful(self):
        with tempfile.TemporaryDirectory() as directory:
            case,row,records=fixture();proposal={**PROPOSAL,'label':'uncertain'}
            result,_=await self.run_fixture(Path(directory),case,row,records,Ledger(),Model(proposal))
            self.assertEqual(result['code_and_support_proposal']['unknown'],1)
            self.assertEqual(result['code_and_support_proposal']['decidable_denominator'],0)
    async def test_malformed_semantic_response_is_preserved_and_not_retried(self):
        class MalformedModel(Model):
            async def complete(inner,messages,config):
                inner.calls.append((messages,config))
                return {'model':s.JUDGE_MODEL,'content':'not valid JSON',
                        'usage':{'prompt_tokens':18,'completion_tokens':3,'total_tokens':21}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);case,row,records=fixture();ledger=Ledger();model=MalformedModel()
            first,out=await self.run_fixture(root,case,row,records,ledger,model)
            result=first['rows'][0]
            self.assertEqual(result['status'],'unevaluable')
            failed=result['support_judgment']
            self.assertEqual(failed['status'],'error');self.assertIsNone(failed['label'])
            self.assertEqual(failed['response']['content'],'not valid JSON')
            self.assertEqual(failed['usage']['total_tokens'],21)
            self.assertEqual(failed['response_sha256'],digest(failed['response']))
            saved=json.loads((out/'rows'/(row['execution_id']+'.json')).read_text())
            self.assertEqual(saved['support_judgment'],failed)
            second,_=await self.run_fixture(root,case,row,records,ledger,model)
            self.assertEqual(second,first);self.assertEqual(len(model.calls),1)
            self.assertEqual(first['code_and_support_proposal']['unknown'],1)
    async def test_failed_delivery_and_ineligible_calibration_make_no_call(self):
        for failed in [True,False]:
            with tempfile.TemporaryDirectory() as directory:
                case,row,records=fixture();model=Model();ledger=Ledger()
                if failed:row['status']='blocked';row['result'].pop('note')
                result,_=await self.run_fixture(Path(directory),case,row,records,ledger,model,cal={})
                self.assertFalse(model.calls);self.assertEqual(ledger.started,0)
                self.assertEqual(result['rows'][0]['status'],'failed_delivery' if failed else 'pending_qualified_calibration')
                self.assertEqual(result['code_and_support_proposal']['false'],1 if failed else 0)
    async def test_saved_label_tamper_and_changed_evidence_are_rejected(self):
        for mode in ['label','evidence']:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);case,row,records=fixture();ledger=Ledger();model=Model()
                _,out=await self.run_fixture(root,case,row,records,ledger,model)
                if mode=='label':
                    path=out/'rows'/(row['execution_id']+'.json');saved=json.loads(path.read_text());saved['proposal']['label']='unsupported';path.write_text(json.dumps(saved))
                else:records['p_test']['passage']['text']='Changed complete evidence'
                with self.assertRaises(ValueError):await self.run_fixture(root,case,row,records,ledger,model)
                self.assertEqual(len(model.calls),1)
    async def test_crash_after_durable_row_finalizes_without_new_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);case,row,records=fixture();ledger=Ledger();model=Model()
            await self.run_fixture(root,case,row,records,ledger,model)
            key=next(iter(ledger.states));ledger.states[key]='in_progress'
            await self.run_fixture(root,case,row,records,ledger,model)
            self.assertEqual(ledger.states[key],'judged');self.assertEqual(len(model.calls),1)
    async def test_prior_attempt_without_saved_result_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            case,row,records=fixture();ledger=Ledger();model=Model()
            key='SEM_'+digest({'execution':row['execution_id'],'rubric':s.RUBRIC_SHA256})[:48]
            ledger.states[key]='in_progress'
            result,_=await self.run_fixture(Path(directory),case,row,records,ledger,model)
            self.assertFalse(model.calls);self.assertEqual(result['rows'][0]['status'],'interrupted_unevaluable')


if __name__=='__main__': unittest.main()
