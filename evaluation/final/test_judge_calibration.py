import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from evaluation.final.judge_calibration import *


def packet_fixture(n=24):
    items=[]
    for i in range(n):
        q='Describe the cited risk.';s='Customer renewals may decline.';o={'title':'Risk','paragraphs':[{'text':s,'evidence_ids':['source']}]}
        items.append({'item_id':f'BLIND_{i:02d}','question':q,'question_sha256':hashlib.sha256(q.encode()).hexdigest(),
            'source_text':s,'source_sha256':hashlib.sha256(s.encode()).hexdigest(),'output':o,'output_sha256':digest(o)})
    p={'schema_version':1,'dataset_split':'development_calibration','items':items};p['packet_sha256']=digest(p);return p


def label_fixture(packet):
    human={'packet_sha256':packet['packet_sha256'],'human_attestation':{'reviewer_name':'UNIT TEST FIXTURE ONLY',
        'reviewed_in_full':True,'labels_authored_by_human':True},'labels':[]}
    judges=[]
    for index,item in enumerate(packet['items']):
        base={k:item[k] for k in ('item_id','question_sha256','source_sha256','output_sha256')}
        label='supported' if index%2==0 else 'unsupported'
        human['labels'].append({**base,'label':label,'rationale':'Unit test fixture only'})
        judges.append({**base,'model':JUDGE_MODEL,'task_model':TASK_MODEL,'status':'complete','label':label})
    return human,judges


def ai_fixture():
    original=packet_fixture();packet=deepcopy(original)
    packet.update(reviewer_type='ai',label_origin='claude_simulated_review',human_reviewed=False,
        satisfies_human_review_requirement=False,source_packet_sha256=original['packet_sha256'],lesson_proposals=[])
    packet.pop('packet_sha256');packet['packet_sha256']=digest(packet)
    human,judges=label_fixture(original)
    rows=[]
    for label,item in zip(human['labels'],packet['items']):
        rows.append({**label,'answer_excerpts':[item['output']['paragraphs'][0]['text']],
                     'source_excerpts':[item['source_text']]})
    exported={'schema_version':1,'packet_sha256':packet['packet_sha256'],'source_packet_sha256':original['packet_sha256'],
        'dataset_split':'development_calibration','reviewer_type':'ai','reviewer_name':'Claude','label_origin':'claude_simulated_review',
        'human_reviewed':False,'human_reviewer':'','satisfies_human_review_requirement':False,'complete_review':True,
        'labels':rows,'lesson_recommendations':[],'unread_item_ids':[],'unread_lesson_ids':[],
        'counts':{'supplied_items':24,'reviewed_items':24,'supported':12,'unsupported':12,'uncertain':0,
                  'supplied_lessons':0,'reviewed_lessons':0,'recommend_approve':0,'recommend_reject':0,'uncertain_lessons':0}}
    return packet,exported,judges


class FakeJudge:
    def __init__(self,label='supported'):self.label=label;self.calls=[]
    async def complete(self,messages,config):
        self.calls.append(messages)
        return {'model':JUDGE_MODEL,'content':canonical({'label':self.label,'reason':'The supplied clause is supported.'}),
                'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'cost':0.0001}}


class CalibrationTests(unittest.TestCase):
    def test_confusion_kappa_known_balanced_case(self):
        p=packet_fixture();h,j=label_fixture(p)
        for row in j[:4]:row['label']='unsupported' if row['label']=='supported' else 'supported'
        result=calibrate(p,h,j)
        self.assertEqual(result['binary_nonuncertain']['n'],24)
        self.assertAlmostEqual(result['binary_nonuncertain']['cohen_kappa'],2/3)
        self.assertTrue(result['eligible_for_automated_semantic_scoring'])
    def test_fewer_than_twenty_labels_is_explicit(self):
        p=packet_fixture();h,j=label_fixture(p);h['labels']=h['labels'][:19]
        result=calibrate(p,h,j)
        self.assertEqual(result['status'],'insufficient_labels');self.assertFalse(result['eligible_for_automated_semantic_scoring'])
    def test_uncertainty_is_retained_and_excluded_from_binary(self):
        p=packet_fixture();h,j=label_fixture(p)
        for row in h['labels'][:6]:row['label']='uncertain'
        result=calibrate(p,h,j)
        self.assertEqual(result['three_class']['n'],24);self.assertEqual(result['binary_nonuncertain']['n'],18)
        self.assertEqual(result['uncertain_pairs_excluded_from_binary'],6)
        self.assertFalse(result['eligible_for_automated_semantic_scoring'])
    def test_missing_class_is_not_perfect_calibration(self):
        p=packet_fixture();h,j=label_fixture(p)
        for row in h['labels']+j:row['label']='supported'
        result=calibrate(p,h,j)
        self.assertEqual(result['status'],'missing_classes');self.assertIsNone(result['binary_nonuncertain']['cohen_kappa'])
        self.assertFalse(result['eligible_for_automated_semantic_scoring'])
    def test_human_attestation_and_hashes_cannot_be_inferred(self):
        p=packet_fixture();h,j=label_fixture(p)
        del h['human_attestation']
        with self.assertRaises(ValueError):calibrate(p,h,j)
        h,j=label_fixture(p);h['labels'][0]['output_sha256']='0'*64
        with self.assertRaises(ValueError):calibrate(p,h,j)
    def test_wrong_model_or_duplicate_label_rejected(self):
        p=packet_fixture();h,j=label_fixture(p);j[0]['model']=TASK_MODEL
        with self.assertRaises(ValueError):calibrate(p,h,j)
        h,j=label_fixture(p);h['labels'].append(h['labels'][0])
        with self.assertRaises(ValueError):calibrate(p,h,j)
    def test_judge_is_blind_to_human_labels_and_condition(self):
        item=packet_fixture()['items'][0];item.update(human_label='unsupported',condition='on',expected_label='unsupported')
        model=FakeJudge();row=asyncio.run(judge_item(item,model))
        self.assertEqual(row['status'],'complete')
        context=json.loads(model.calls[0][1]['content'])
        self.assertEqual(set(context),{'question','question_context','source_text','answer'})
        self.assertFalse(row['human_reviewed'])
    def test_security_scoring_waits_for_real_calibration(self):
        model=FakeJudge();row=asyncio.run(judge_security_result({}, {'execution_id':'test'},model,None))
        self.assertEqual(row['status'],'pending_manual_review');self.assertEqual(model.calls,[])
    def test_failed_judges_are_not_agreements(self):
        p=packet_fixture();h,j=label_fixture(p);j[0]['status']='error';j[1]['status']='error'
        result=calibrate(p,h,j)
        self.assertEqual(result['binary_nonuncertain']['n'],22)
        self.assertEqual(len(result['unpaired_or_failed_judge_ids']),2)


class AIReferenceTests(unittest.TestCase):
    def test_agreement_never_becomes_human_calibration(self):
        packet,exported,judges=ai_fixture();report=compare_ai_reference(packet,exported,judges)
        self.assertEqual(report['report_type'],'MODEL-MODEL AGREEMENT')
        self.assertEqual(report['actual_human_labels'],0)
        self.assertFalse(report['satisfies_human_review_requirement'])
        self.assertFalse(report['eligible_for_automated_semantic_scoring'])
        self.assertFalse(adjudication_ready(report));self.assertTrue(ai_reference_ready(report))
        self.assertEqual(report['binary_nonuncertain']['cohen_kappa'],1)
        self.assertNotIn('human_counts',report['binary_nonuncertain'])
    def test_ai_review_cannot_be_laundered_with_human_attestation(self):
        packet,exported,judges=ai_fixture()
        exported['human_attestation']={'reviewer_name':'Fabricated','reviewed_in_full':True,'labels_authored_by_human':True}
        with self.assertRaises(ValueError):calibrate(packet,exported,judges)
    def test_false_human_claim_is_rejected(self):
        packet,exported,_=ai_fixture();exported['human_reviewed']=True
        with self.assertRaises(ValueError):validate_ai_reference(packet,exported)
    def test_spliced_excerpts_and_wrong_counts_are_rejected(self):
        packet,exported,_=ai_fixture();exported['labels'][0]['answer_excerpts']=['Customer decline.']
        with self.assertRaises(ValueError):validate_ai_reference(packet,exported)
        packet,exported,_=ai_fixture();exported['counts']['supported']=11
        with self.assertRaises(ValueError):validate_ai_reference(packet,exported)
    def test_partial_review_must_name_every_unread_id(self):
        packet,exported,_=ai_fixture();missing=exported['labels'].pop()
        exported['counts']['reviewed_items']-=1;exported['counts'][missing['label']]-=1
        with self.assertRaises(ValueError):validate_ai_reference(packet,exported)
        exported['unread_item_ids']=[missing['item_id']];exported['complete_review']=False
        self.assertFalse(validate_ai_reference(packet,exported)['complete_review'])
    def test_ai_only_security_requires_explicit_opt_in(self):
        packet,exported,judges=ai_fixture();report=compare_ai_reference(packet,exported,judges);model=FakeJudge()
        result=asyncio.run(judge_security_result({}, {'execution_id':'test'},model,report))
        self.assertEqual(model.calls,[]);self.assertEqual(result['status'],'pending_manual_review')

if __name__=='__main__':unittest.main()
