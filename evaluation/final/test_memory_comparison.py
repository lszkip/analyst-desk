from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from evaluation.final.memory_comparison import verify_historical_publication,build_memory_comparison_report,HISTORICAL_STATUS
from research_portal import ResearchNote,note_hash
from workflow_contracts import digest


def fixture():
    note=ResearchNote(title='Historical note',body_markdown='A historical source described a risk.',tickers=['GOOGL','META'],
        fiscal_years=[2024,2025],citations=[{'evidence_id':'historical_source','source_sha256':'a'*64,
            'source_url':'https://www.sec.gov/Archives/edgar/data/1/historical.htm'}]).model_dump()
    h=note_hash(ResearchNote.model_validate(note))
    binding={'run_id':'historical_run','note':note,'content_hash':h,'proposal_id':'proposal_historical','revision':1}
    binding['binding_sha256']=digest(binding)
    receipt={'published':True,'portal_state':'published','two_reads_identical':True,'note_id':'note_historical',
        'proposal_id':'proposal_historical','revision':1,'content_hash':h,'published_at':1.0,'reviewer':'TEST FIXTURE ONLY'}
    return binding,receipt


class MemoryComparisonTests(unittest.TestCase):
    def test_actual_memory_off_on_and_synthetic_privacy_guard(self):
        binding,receipt=fixture()
        with tempfile.TemporaryDirectory() as parent:report=build_memory_comparison_report(binding,receipt,workspace=parent)
        self.assertTrue(report['memory_off_prior_note_absent']);self.assertTrue(report['memory_on_prior_note_visible_in_context'])
        self.assertTrue(report['memory_on_prior_note_visible_in_inventory_followup'])
        self.assertTrue(report['synthetic_watchlist_not_exposed'])
        self.assertTrue(all(row['synthetic_watchlist_literal_blocked'] for row in report['synthetic_privacy_checks']))
        self.assertEqual(report['model_calls'],0);self.assertTrue(report['temporary_database_deleted'])
    def test_modified_note_hash_rejected(self):
        binding,receipt=fixture();binding['note']['body_markdown']='Changed claim.'
        with self.assertRaises(ValueError):verify_historical_publication(binding,receipt)
    def test_binding_integrity_rejected(self):
        binding,receipt=fixture();binding['run_id']='different_run'
        with self.assertRaises(ValueError):verify_historical_publication(binding,receipt)
    def test_unpublished_receipt_cannot_enter_history(self):
        for delta in ({'published':False},{'portal_state':'approved'},{'two_reads_identical':False},{'note_id':''}):
            binding,receipt=fixture();receipt.update(delta)
            with self.subTest(delta=delta),self.assertRaises(ValueError):verify_historical_publication(binding,receipt)
    def test_proposal_revision_or_receipt_hash_mismatch_rejected(self):
        for delta in ({'proposal_id':'other_proposal'},{'revision':2},{'content_hash':'b'*64}):
            binding,receipt=fixture();receipt.update(delta)
            with self.subTest(delta=delta),self.assertRaises(ValueError):verify_historical_publication(binding,receipt)
    def test_old_receipt_not_upgraded_and_inputs_remain_unchanged(self):
        binding,receipt=fixture();before=(deepcopy(binding),deepcopy(receipt))
        report=build_memory_comparison_report(binding,receipt)
        self.assertEqual((binding,receipt),before)
        self.assertEqual(report['historical_verification']['status'],HISTORICAL_STATUS)
        self.assertTrue(report['modern_sentence_verification_not_invented'])
        self.assertFalse(report['live_model_quality_comparison'])
        self.assertEqual(report['comparison']['runs'][1]['followup']['changes'],[])
        self.assertFalse(report['comparison']['runs'][1]['followup']['semantic_change_assessed'])

if __name__=='__main__':unittest.main()
