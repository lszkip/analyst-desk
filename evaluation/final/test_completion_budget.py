"""No network: create a genuine frozen-S40 ledger and amend only temporary copies."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

HERE=Path(__file__).resolve().parent
# Installed tests load both clients from their own evaluation/final directory.
# No development-tree sibling, external file, network, or user database is used.
SOURCE=HERE.parents[1]
NEW_PATH=HERE/'completion_budget.py'
sys.path.insert(0,str(SOURCE))

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

OLD_PATH=HERE/'budget_client.py'
old=load('authentic_s40_budget_client_for_amendment_test',OLD_PATH)
new=load('completion_budget_under_test',NEW_PATH)


def pricing():
    return {'schema_version':1,'verification':{'method':'live_openrouter_models_api','source_url':old.MODELS_ENDPOINT,
        'verified_at':datetime.now(timezone.utc).isoformat(),'response_sha256':'a'*64},
        'models':{old.TASK_MODEL:{'family':'openai','prompt_usd_per_million':'0.4','completion_usd_per_million':'1.6','request_usd':'0'},
                  old.JUDGE_MODEL:{'family':'meta-llama','prompt_usd_per_million':'0.1','completion_usd_per_million':'0.32','request_usd':'0'}}}


class AmendmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'original_s40.sqlite'
        self.prices=pricing();self.old_limits=deepcopy(old.DEFAULT_LIMITS);self.new_limits=deepcopy(new.COMPLETION_LIMITS)
        self.old_approval=old.approve_budget('APPROVE',self.prices,self.old_limits);self.plan='b'*64;self.batch='original_s40_batch'
        ledger=old.BudgetLedger(self.path,self.batch,self.prices,self.old_limits,self.old_approval)
        ledger.begin_case('paid_complete');r=ledger.reserve('paid_complete',6000,800,old.TASK_MODEL)
        ledger.complete_call(r,{'prompt_tokens':1000,'completion_tokens':200,'total_tokens':1200,'cost':0.00072})
        ledger.finish_case('paid_complete','completed')
        ledger.begin_case('failed_unknown_cost');r=ledger.reserve('failed_unknown_cost',5000,800,old.TASK_MODEL)
        ledger.fail_call(r['call_id'],'live_interrupted_dispatch',poison=False);ledger.finish_case('failed_unknown_cost','interrupted_unevaluable')
        ledger.begin_case('unresolved_inflight');ledger.reserve('unresolved_inflight',4000,800,old.TASK_MODEL)
        self.before=ledger.stats()
        with sqlite3.connect(self.path) as db:self.start=db.execute("SELECT value FROM metadata WHERE name='started_at'").fetchone()[0]
        self.original_approval=deepcopy(self.old_approval)
    def tearDown(self):self.temp.cleanup()
    def args(self):return (self.path,self.batch,self.prices,self.old_limits,self.old_approval,self.new_limits,self.plan)
    def amend(self,answer='APPROVE'):return new.amend_existing_budget(*self.args(),answer=answer)
    def query(self,sql,*params):
        with sqlite3.connect(self.path) as db:return db.execute(sql,params).fetchall()
    def test_frozen_original_client_and_true_old_ledger_reopen(self):
        self.assertEqual(new.COMPLETION_LIMITS['max_reserved_usd'],'150.00')
        self.assertEqual(old.DEFAULT_LIMITS['max_reserved_usd'],'10.00')
        self.assertEqual(new.COMPLETION_LIMITS['max_calls'],12000)
        self.assertEqual(new.COMPLETION_LIMITS['max_runtime_seconds'],16200)
        self.assertEqual(hashlib.sha256(OLD_PATH.read_bytes()).hexdigest(),'13dcc615844634e6f36a4337eb13d762cd57930e76879b912f19a722806eb8d7')
        reopened=old.BudgetLedger(self.path,self.batch,self.prices,self.old_limits,self.old_approval)
        self.assertEqual(reopened.stats(),self.before)
    def test_preview_is_read_only_and_requires_new_explicit_approval(self):
        before=self.path.read_bytes();preview=new.preview_budget_amendment(*self.args())
        self.assertEqual(preview['status'],'awaiting_explicit_approval');self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(preview['current_stats']['reserved_requests'],3)
        for answer in (None,'yes','APPROVE '+self.plan):
            with self.subTest(answer=answer),self.assertRaises(old.WorkflowError):self.amend(answer)
        self.assertEqual(before,self.path.read_bytes())
    def test_all_old_usage_ids_and_cases_preserved_and_next_id_continues(self):
        ledger,receipt=self.amend();after=ledger.stats()
        for field in ('reserved_requests','reserved_nano_usd','known_actual_nano_usd','requests_without_actual_cost','in_flight_requests','case_states','calls'):
            self.assertEqual(self.before[field],after[field],field)
        self.assertEqual(after['limits'],self.new_limits);self.assertEqual(after['original_started_at'],self.start)
        self.assertEqual(receipt['old_approval'],self.original_approval);self.assertEqual(self.old_approval,self.original_approval)
        self.assertEqual(receipt['prior_stats_sha256'],new.digest(receipt['prior_stats']))
        self.assertFalse(ledger.begin_case('paid_complete'))
        with self.assertRaises(old.WorkflowError):ledger.begin_case('unresolved_inflight')
        ledger.begin_case('new_execution');r=ledger.reserve('new_execution',3000,800,old.TASK_MODEL)
        self.assertEqual(r['call_id'],4);self.assertEqual(ledger.stats()['reserved_requests'],4)
    def test_reopen_and_idempotent_replay_do_not_add_allowance_or_history(self):
        ledger,first=self.amend();contents=self.path.read_bytes()
        reopened,second=self.amend(None)
        self.assertEqual(first,second);self.assertEqual(contents,self.path.read_bytes())
        self.assertEqual(self.query('SELECT COUNT(*) FROM budget_amendments')[0][0],1)
        direct=new.BudgetLedger(self.path,self.batch,self.prices,self.new_limits,first['new_scoped_approval'])
        self.assertEqual(direct.stats(),reopened.stats())
        preview=new.preview_budget_amendment(*self.args())
        self.assertEqual(preview['receipt'],first);self.assertEqual(preview['status'],'already_amended')
    def test_changed_plan_or_limits_after_amendment_rejected_even_with_approve(self):
        self.amend();before=self.path.read_bytes();self.plan='c'*64
        with self.assertRaises(old.WorkflowError):self.amend()
        self.plan='b'*64;self.new_limits['max_calls']=11999
        with self.assertRaises(old.WorkflowError):self.amend()
        self.assertEqual(before,self.path.read_bytes())
    def test_original_start_is_preserved_and_expired_old_scope_can_extend_only_to_new_deadline(self):
        original=str(time.time()-15000)
        with sqlite3.connect(self.path) as db:db.execute("UPDATE metadata SET value=? WHERE name='started_at'",(original,))
        ledger,receipt=self.amend()
        self.assertEqual(ledger.stats()['original_started_at'],original);self.assertEqual(receipt['original_started_at'],original)
        remaining=new.preview_budget_amendment(*self.args())['remaining_seconds']
        self.assertTrue(1100<remaining<1201)
        with sqlite3.connect(self.path) as db:db.execute("UPDATE metadata SET value=? WHERE name='started_at'",(str(time.time()),))
        with self.assertRaises(old.WorkflowError):ledger.stats()
    def test_expired_amended_deadline_is_never_reset(self):
        with sqlite3.connect(self.path) as db:db.execute("UPDATE metadata SET value=? WHERE name='started_at'",(str(time.time()-17000),))
        before=self.path.read_bytes()
        with self.assertRaises(old.WorkflowError) as caught:self.amend()
        self.assertEqual(caught.exception.code,'live_time_limit');self.assertEqual(before,self.path.read_bytes())
    def test_missing_truncated_or_mismatched_original_cannot_initialize_new_budget(self):
        self.path.unlink()
        with self.assertRaises(old.WorkflowError):self.amend()
        self.assertFalse(self.path.exists())
        self.path.write_bytes(b'')
        with self.assertRaises(old.WorkflowError):self.amend()
        self.assertEqual(self.path.stat().st_size,0)
    def test_ledger_identity_and_approval_mismatch_rejected(self):
        before=self.path.read_bytes();self.batch='other_batch'
        with self.assertRaises(old.WorkflowError):self.amend()
        self.batch='original_s40_batch';self.old_approval['limits_sha256']='d'*64
        with self.assertRaises(old.WorkflowError):self.amend()
        self.assertEqual(before,self.path.read_bytes())
    def test_cap_below_consumed_and_changed_local_bounds_rejected(self):
        self.new_limits['max_reserved_usd']='0.000001'
        with self.assertRaises((ValueError,old.WorkflowError)):self.amend()
        self.new_limits=deepcopy(new.COMPLETION_LIMITS);self.new_limits['max_calls_per_case']=51
        with self.assertRaises(old.WorkflowError):self.amend()
        self.new_limits=deepcopy(new.COMPLETION_LIMITS);self.new_limits['max_reserved_usd']='151'
        with self.assertRaises(ValueError):self.amend()
    def test_history_cannot_update_delete_or_replace(self):
        _,record=self.amend()
        statements=[('UPDATE budget_amendments SET record_json=?',('{}',)),('DELETE FROM budget_amendments',()),
                    ('INSERT OR REPLACE INTO budget_amendments VALUES (?,?,?)',(record['amendment_id'],'{}','e'*64))]
        for sql,params in statements:
            with self.subTest(sql=sql),self.assertRaises(sqlite3.DatabaseError):self.query(sql,*params)
        self.assertEqual(new.preview_budget_amendment(*self.args())['receipt'],record)
    def test_atomic_failure_rolls_back_identity_and_audit_schema(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TRIGGER simulated_commit_failure BEFORE UPDATE ON metadata WHEN NEW.name='identity' BEGIN SELECT RAISE(ABORT,'test rollback'); END")
        before=self.query('SELECT * FROM metadata ORDER BY name');calls=self.query('SELECT * FROM calls ORDER BY call_id')
        with self.assertRaises(sqlite3.DatabaseError):self.amend()
        self.assertEqual(before,self.query('SELECT * FROM metadata ORDER BY name'));self.assertEqual(calls,self.query('SELECT * FROM calls ORDER BY call_id'))
        self.assertEqual(self.query("SELECT name FROM sqlite_master WHERE name='budget_amendments'"),[])
        old.BudgetLedger(self.path,self.batch,self.prices,self.old_limits,self.old_approval)
    def test_poison_is_preserved_not_cleared_by_new_budget(self):
        with sqlite3.connect(self.path) as db:db.execute("INSERT INTO metadata VALUES ('poison','model_http_error')")
        ledger,receipt=self.amend();self.assertTrue(ledger.stats()['poisoned']);self.assertTrue(receipt['prior_stats']['poisoned'])
        with self.assertRaises(old.WorkflowError):ledger.begin_case('blocked_new_case')
    def test_fake_scoped_approval_cannot_create_a_fresh_extended_ledger(self):
        approval=new.approve_budget_amendment('APPROVE',self.prices,self.old_limits,self.old_approval,self.new_limits,self.plan)
        target=Path(self.temp.name)/'fresh.sqlite'
        with self.assertRaises(old.WorkflowError):new.BudgetLedger(target,self.batch,self.prices,self.new_limits,approval)
        self.assertFalse(target.exists())
    def test_impossible_original_usage_reservation_and_sequence_states_rejected(self):
        statements=[
            "UPDATE calls SET actual_nano_usd=reserved_nano_usd+1 WHERE call_id=1",
            "UPDATE calls SET reserved_nano_usd=0 WHERE call_id=1",
            "UPDATE calls SET prompt_tokens=-1 WHERE call_id=1",
            "UPDATE calls SET total_tokens=1 WHERE call_id=1",
            "UPDATE calls SET completion_tokens=output_allowance+1 WHERE call_id=1",
            "UPDATE calls SET actual_nano_usd=0 WHERE call_id=3",
            "UPDATE calls SET input_allowance=0 WHERE call_id=3",
            "UPDATE sqlite_sequence SET seq=0 WHERE name='calls'",
            "DELETE FROM calls WHERE call_id=2"]
        pristine=self.path.read_bytes()
        for sql in statements:
            with self.subTest(sql=sql):
                self.path.write_bytes(pristine)
                with sqlite3.connect(self.path) as db:db.execute(sql)
                before=self.path.read_bytes()
                with self.assertRaises(old.WorkflowError):self.amend()
                self.assertEqual(before,self.path.read_bytes())
    def test_impossible_usage_after_amendment_is_rejected_on_reopen_and_dispatch(self):
        ledger,receipt=self.amend()
        with sqlite3.connect(self.path) as db:db.execute('UPDATE calls SET actual_nano_usd=reserved_nano_usd+1 WHERE call_id=1')
        with self.assertRaises(old.WorkflowError):ledger.stats()
        with self.assertRaises(old.WorkflowError):ledger.begin_case('new_unapproved_dispatch')
        with self.assertRaises(old.WorkflowError):new.BudgetLedger(self.path,self.batch,self.prices,self.new_limits,receipt['new_scoped_approval'])
    def test_parallel_same_amendment_is_single_idempotent_transaction(self):
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.amend(),range(2)))
        self.assertEqual(results[0][1],results[1][1]);self.assertEqual(self.query('SELECT COUNT(*) FROM budget_amendments')[0][0],1)
        self.assertEqual(results[0][0].stats()['reserved_requests'],3)

if __name__=='__main__':unittest.main()
