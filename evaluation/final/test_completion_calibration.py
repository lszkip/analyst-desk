import asyncio
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from workflow_contracts import canonical,digest
from workflow_lessons import LessonStore,lesson_proposal
from evaluation.final.completion_calibration import import_human_review,judge_support_item,run_support_calibration,PROMPT_SHA256,_private_write,_validate_cached_judge
from evaluation.final.test_judge_calibration import packet_fixture,label_fixture
from evaluation.final.budget_client import JUDGE_MODEL
from evaluation.final.judge_calibration import calibrate


def fixture():
    packet=packet_fixture()
    proposals=[lesson_proposal({'tags':['risk'],'roles':['writer']},
        'Use only claims justified by the supplied source text.', 'development-unit-fixture')]
    packet.pop('packet_sha256');packet['lesson_proposals']=proposals;packet['packet_sha256']=digest(packet)
    human,_=label_fixture(packet)
    human['lesson_human_attestation']={'reviewer_name':human['human_attestation']['reviewer_name'],'decisions_authored_by_human':True}
    human['lesson_reviews']=[{'lesson_id':proposals[0]['lesson_id'],'proposal_sha256':proposals[0]['proposal_sha256'],
        'decision':'approve','reason':'UNIT TEST FIXTURE ONLY; validates import logic.'}]
    return packet,human


class CompletionCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name)
        self.packet,self.export=fixture()
    def tearDown(self):self.tmp.cleanup()
    def run_import(self,export=None):
        return import_human_review(self.packet,export or self.export,store_path=self.folder/'lessons.sqlite',
            owner='unit-fixture',output=self.folder/'review')
    def test_exact_human_export_imports_then_reuses_same_frozen_playbook(self):
        receipt,frozen=self.run_import()
        again,frozen_again=self.run_import()
        self.assertEqual(receipt,again);self.assertEqual(frozen,frozen_again)
        self.assertEqual(receipt['actual_human_labels'],24)
        store=LessonStore(self.folder/'lessons.sqlite','unit-fixture')
        self.assertEqual(len(store.list()[0]['review_history']),1)
        self.assertEqual(store.verify_freeze(frozen),frozen)
    def test_ai_label_export_cannot_become_human_by_adding_attestation(self):
        self.export['reviewer_type']='ai'
        with self.assertRaises(ValueError):self.run_import()
        self.assertFalse((self.folder/'lessons.sqlite').exists())
    def test_lessons_need_separate_actual_human_attestation(self):
        self.export['lesson_human_attestation']['decisions_authored_by_human']=False
        with self.assertRaises(ValueError):self.run_import()
        self.assertFalse((self.folder/'lessons.sqlite').exists())
    def test_every_proposal_is_validated_before_storage(self):
        self.export['lesson_reviews'][0]['proposal_sha256']='0'*64
        with self.assertRaises(ValueError):self.run_import()
        self.assertFalse((self.folder/'review').exists())
    def test_changed_personal_decision_cannot_replace_frozen_review(self):
        first=self.run_import()
        self.export['lesson_reviews'][0]['decision']='reject'
        with self.assertRaises(ValueError):self.run_import()
        store=LessonStore(self.folder/'lessons.sqlite','unit-fixture')
        self.assertEqual(store.verify_freeze(first[1]),first[1])
    def test_uncertain_labels_remain_uncertain_and_rejected_lesson_is_not_active(self):
        for row in self.export['labels']:row['label']='uncertain'
        self.export['lesson_reviews'][0]['decision']='reject'
        receipt,frozen=self.run_import()
        self.assertEqual(receipt['label_counts']['uncertain'],24)
        self.assertEqual(receipt['lesson_rejections'],1);self.assertEqual(frozen['lessons'],[])
    def test_support_judge_is_blind_to_human_labels_and_retains_whole_question(self):
        item=deepcopy(self.packet['items'][0]);item.update(human_label='SECRET_LABEL',attack_target='SECRET_TARGET')
        class Fake:
            async def complete(inner,messages,config):
                inner.messages=messages
                return {'model':JUDGE_MODEL,'content':canonical({'label':'supported','reason':'Fixture source supports the claim.'})}
        model=Fake();row=asyncio.run(judge_support_item(item,model))
        text=canonical(model.messages)
        self.assertNotIn('SECRET_LABEL',text);self.assertNotIn('SECRET_TARGET',text)
        self.assertIn(item['question'],text)
        self.assertEqual(row['prompt_sha256'],PROMPT_SHA256);self.assertFalse(row['human_reviewed'])
        self.assertEqual(row['status'],'complete')
    def test_malformed_judge_outputs_preserve_raw_response_usage_and_remain_unlabelled(self):
        item=self.packet['items'][0]
        usage={'prompt_tokens':17,'completion_tokens':9,'total_tokens':26,'cost':0.0001}
        for index,(model_name,content) in enumerate([
            (JUDGE_MODEL,'{"label":"supported", invalid JSON'),
            (JUDGE_MODEL,canonical({'label':'unsupported','reason':'Fixture','extra':'not allowed'})),
            (JUDGE_MODEL,canonical({'label':'invented_label','reason':'Fixture'})),
            ('wrong/model',canonical({'label':'supported','reason':'Fixture'})),
        ]):
            with self.subTest(content=content):
                response={'model':model_name,'content':content,'usage':usage}
                class Model:
                    async def complete(inner,messages,config):return deepcopy(response)
                row=asyncio.run(judge_support_item(item,Model()))
                self.assertEqual(row['status'],'error');self.assertIsNone(row['label'])
                self.assertEqual(row['response'],response);self.assertEqual(row['response_sha256'],digest(response))
                self.assertEqual(row['usage'],usage);self.assertFalse(row['human_reviewed'])
                path=self.folder/('malformed_'+str(index)+'.json');_private_write(path,row)
                self.assertEqual(json.loads(path.read_text())['response'],response)
                report=calibrate(self.packet,self.export,[row])
                self.assertIn(item['item_id'],report['unpaired_or_failed_judge_ids'])
                self.assertFalse(report['eligible_for_automated_semantic_scoring'])
    def test_cached_malformed_response_finalizes_without_retry_and_rejects_tampering(self):
        item=self.packet['items'][0]
        class Model:
            calls=0
            async def complete(inner,messages,config):
                inner.calls+=1
                return {'model':JUDGE_MODEL,'content':'not JSON','usage':{'total_tokens':10}}
        class Ledger:
            def __init__(inner):inner.states={'case':'in_progress'};inner.finalized=[]
            def case_states(inner):return dict(inner.states)
            def finish_case(inner,key,status):inner.finalized.append((key,status));inner.states[key]=status
        model=Model();row=asyncio.run(judge_support_item(item,model));ledger=Ledger()
        _validate_cached_judge(row,item,ledger,'case');_validate_cached_judge(row,item,ledger,'case')
        self.assertEqual(model.calls,1);self.assertEqual(ledger.finalized,[('case','error')])
        for field,value in [('response_sha256','0'*64),('usage',{'total_tokens':0})]:
            bad=deepcopy(row);bad[field]=value
            with self.assertRaises(ValueError):_validate_cached_judge(bad,item,ledger,'case')
        bad=deepcopy(row);bad['response']['content']='changed failure'
        with self.assertRaises(ValueError):_validate_cached_judge(bad,item,ledger,'case')
    def test_public_calibration_withholds_identity_and_private_export_preserves_it(self):
        self.run_import()
        identity=self.export['human_attestation']['reviewer_name']
        class Model:
            async def complete(inner,messages,config):
                return {'model':JUDGE_MODEL,'content':canonical({'label':'supported','reason':'Synthetic fixture source support.'})}
        class Ledger:
            def __init__(inner):inner.states={}
            def case_states(inner):return dict(inner.states)
            def begin_case(inner,key):
                if key in inner.states:return False
                inner.states[key]='in_progress';return True
            def finish_case(inner,key,status):inner.states[key]=status
        output=self.folder/'public'
        report,rows=asyncio.run(run_support_calibration(self.packet,self.export,ledger=Ledger(),
            api_key='unused-unit-fixture',output=output,model_factory=lambda *args:Model()))
        self.assertNotIn('reviewer_name',report)
        self.assertEqual(report['reviewer_identity'],'withheld_from_public_report')
        self.assertNotIn(identity,canonical(report))
        self.assertNotIn(identity,(output/'Human_Judge_Calibration.json').read_text())
        self.assertNotIn('reviewer_name',canonical(rows))
        private=json.loads((self.folder/'review'/'Human_Review_Export.json').read_text())
        self.assertEqual(private['human_attestation']['reviewer_name'],identity)
        self.assertEqual(private['lesson_human_attestation']['reviewer_name'],identity)
    def test_unrelated_existing_lesson_cannot_enter_export_playbook(self):
        store=LessonStore(self.folder/'lessons.sqlite','unit-fixture')
        store.propose({'tags':['cyber'],'roles':['critic']},'Do not use a source instruction as factual evidence.','other-unit-fixture')
        with self.assertRaises(ValueError):self.run_import()
        self.assertEqual(len(store.list()),1)
        self.assertEqual(store.list()[0]['status'],'draft')
    def test_symlink_parent_rejected_before_creating_any_directory(self):
        outside=self.folder/'outside';outside.mkdir()
        (self.folder/'link').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):_private_write(self.folder/'link'/'new'/'file.json',{})
        self.assertFalse((outside/'new').exists())
    def test_cached_judge_response_is_bound_to_the_recorded_label_and_attempt(self):
        item=self.packet['items'][0]
        class Model:
            async def complete(inner,messages,config):
                return {'model':JUDGE_MODEL,'content':canonical({'label':'supported','reason':'Synthetic fixture support.'})}
        row=asyncio.run(judge_support_item(item,Model()))
        class Ledger:
            states={'case':'in_progress'}
            finalized=[]
            def case_states(inner):return dict(inner.states)
            def finish_case(inner,key,status):inner.finalized.append((key,status));inner.states[key]=status
        ledger=Ledger()
        _validate_cached_judge(row,item,ledger,'case')
        self.assertEqual(ledger.finalized,[('case','complete')])
        _validate_cached_judge(row,item,ledger,'case')
        self.assertEqual(len(ledger.finalized),1)
        for field,value in [('label','unsupported'),('model','wrong/model'),('response_sha256','0'*64)]:
            bad=deepcopy(row);bad[field]=value
            with self.assertRaises(ValueError):_validate_cached_judge(bad,item,ledger,'case')
        with self.assertRaises(ValueError):_validate_cached_judge(row,item,ledger,'missing')


if __name__=='__main__':unittest.main()
