"""Development reflection, exact review and held-out freeze software checks."""
import copy
from pathlib import Path
import tempfile
import unittest

from workflow_contracts import WorkflowError, digest
from workflow_lessons import LessonStore, lesson_proposal, reflection_proposal

CONDITION={"tags":["risk","source_support"],"roles":["writer","critic"]}
GUIDANCE="When assessing a risk claim, retain the source's uncertainty and use independent eligible evidence for every substantive assertion."


class LessonTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"memory.sqlite3"
        self.store=LessonStore(self.path,"analyst_alice")
        self.proposal=self.store.propose(CONDITION,GUIDANCE,"S32 saved development critic failure")

    def approve(self,proposal=None):
        row=proposal or self.proposal
        return self.store.review(row["lesson_id"],row["proposal_sha256"],"approve","Synthetic human action in an offline test only.",human_confirmed=True)

    def test_draft_never_auto_claims_human_review_or_enters_context(self):
        self.assertEqual(self.proposal["status"],"draft")
        self.assertFalse(self.proposal["human_reviewed"])
        self.assertEqual(self.store.relevant(["risk"]),[])
        self.assertEqual(self.store.context(["risk"],"writer")["lessons"],[])
        self.assertEqual(self.store.freeze(["risk"])["lessons"],[])

    def test_exact_hash_human_review_and_reason_required(self):
        for expected,action,reason,confirmed in [("0"*64,"approve","Reason",True),
            (self.proposal["proposal_sha256"],"approve","",True),
            (self.proposal["proposal_sha256"],"approve","Reason",False)]:
            with self.subTest(expected=expected,reason=reason,confirmed=confirmed),self.assertRaises(WorkflowError):
                self.store.review(self.proposal["lesson_id"],expected,action,reason,confirmed)
        approved=self.approve()
        self.assertEqual(approved["review"]["expected_hash"],self.proposal["proposal_sha256"])
        self.assertEqual(len(self.store.relevant(["risk"])),1)

    def test_rejected_reason_retained_across_reopen_and_not_retrieved(self):
        self.store.review(self.proposal["lesson_id"],self.proposal["proposal_sha256"],"reject",
                          "Too broad: would discard useful evidence.",human_confirmed=True)
        reopened=LessonStore(self.path,"analyst_alice")
        self.assertEqual(reopened.list()[0]["review"]["reason"],"Too broad: would discard useful evidence.")
        self.assertEqual(reopened.relevant(["risk"]),[])
        self.assertEqual(reopened.freeze(["risk"])["lessons"],[])

    def test_condition_retrieves_only_relevant_role_and_tags(self):
        self.approve()
        self.assertEqual(self.store.context(["margin"],"writer")["lessons"],[])
        self.assertEqual(self.store.context(["risk"],"quant")["lessons"],[])
        self.assertEqual(len(self.store.context(["risk"],"critic")["lessons"]),1)
        context=self.store.context(["risk"],"writer",max_chars=256)
        self.assertEqual(context["lessons"],[]);self.assertTrue(context["truncated"])

    def test_frozen_manifest_is_exact_and_invalidated_by_approval_change(self):
        self.approve();manifest=self.store.freeze(["risk"])
        self.assertEqual(self.store.verify_freeze(manifest),manifest)
        self.assertEqual(len(self.store.context(["risk"],"writer",manifest=manifest)["lessons"]),1)
        changed=copy.deepcopy(manifest);changed["lessons"][0]["guidance"]="Changed after freeze"
        with self.assertRaises(WorkflowError):self.store.verify_freeze(changed)
        self.store.review(self.proposal["lesson_id"],self.proposal["proposal_sha256"],"reject","Withdrawn after review.",True)
        with self.assertRaises(WorkflowError):self.store.verify_freeze(manifest)
        self.assertEqual(len(self.store.list()[0]["review_history"]),2)

    def test_new_relevant_approved_lesson_invalidates_freeze(self):
        self.approve();manifest=self.store.freeze(["risk"])
        row=self.store.propose({"tags":["risk"],"roles":["writer"]},"When writing risk comparisons, do not infer complete filing coverage from samples.","Earlier development scope failure")
        self.approve(row)
        with self.assertRaises(WorkflowError):self.store.verify_freeze(manifest)

    def test_foreign_owner_or_forged_approved_manifest_cannot_be_used(self):
        self.approve();manifest=self.store.freeze(["risk"])
        bob=LessonStore(self.path,"analyst_bob")
        self.assertEqual(bob.list(),[])
        with self.assertRaises(WorkflowError):bob.verify_freeze(manifest)
        manifest["owner_scope"]=bob.storage.owner_scope
        manifest["manifest_sha256"]=digest({k:v for k,v in manifest.items() if k!="manifest_sha256"})
        with self.assertRaises(WorkflowError):bob.verify_freeze(manifest)

    def test_heldout_or_success_cannot_generate_development_reflection(self):
        for failure in ({"case_id":"heldout_case","split":"held_out","passed":False},
                        {"case_id":"dev_case","split":"development","passed":True}):
            with self.subTest(failure=failure),self.assertRaises(WorkflowError):reflection_proposal(failure,GUIDANCE,CONDITION)
        result=reflection_proposal({"case_id":"development_failed_case","split":"development","passed":False},GUIDANCE,CONDITION)
        self.assertEqual(result["status"],"draft");self.assertFalse(result["human_reviewed"])
        with self.assertRaises(WorkflowError):self.store.propose(CONDITION,GUIDANCE,"heldout_case",dataset="held_out")

    def test_proposal_hash_changes_with_condition_guidance_or_failure(self):
        original=self.proposal["proposal_sha256"]
        changed=[lesson_proposal({"tags":["risk"],"roles":["writer"]},GUIDANCE,"S32 saved development critic failure"),
                 lesson_proposal(CONDITION,GUIDANCE+" Seek fresh sources.","S32 saved development critic failure"),
                 lesson_proposal(CONDITION,GUIDANCE,"Different development failure")]
        self.assertTrue(all(row["proposal_sha256"]!=original for row in changed))
        self.assertEqual(self.store.propose(CONDITION,GUIDANCE,"S32 saved development critic failure"),self.proposal)


if __name__=="__main__":unittest.main()
