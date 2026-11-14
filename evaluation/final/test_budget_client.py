import asyncio
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from evaluation.final.budget_client import *


def pricing():
    return {'schema_version':1,'verification':{'method':'live_openrouter_models_api','source_url':MODELS_ENDPOINT,
        'verified_at':datetime.now(timezone.utc).isoformat(),'response_sha256':'a'*64},
        'models':{TASK_MODEL:{'family':'openai','prompt_usd_per_million':'0.4','completion_usd_per_million':'1.6','request_usd':'0'},
                  JUDGE_MODEL:{'family':'meta-llama','prompt_usd_per_million':'0.1','completion_usd_per_million':'0.32','request_usd':'0'}}}


class Fake:
    def __init__(self, body=None,status=200,exc=None):self.body=body;self.status_code=status;self.exc=exc;self.calls=[]
    def __call__(self,**kwargs):self.client_options=kwargs;return self
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    def stream(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs))
        if self.exc:raise self.exc
        model=kwargs.get('json',{}).get('model',TASK_MODEL)
        self.response=self.body if self.body is not None else {'model':model,'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],
            'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'cost':0.0001}}
        return self
    async def aiter_bytes(self):yield json.dumps(self.response).encode()


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'ledger.sqlite'
        self.pricing=pricing();self.limits=deepcopy(DEFAULT_LIMITS);self.limits.update(max_calls=4,max_calls_per_case=2)
        self.approval=approve_budget('APPROVE',self.pricing,self.limits)
        self.ledger=BudgetLedger(self.path,'final34',self.pricing,self.limits,self.approval)
        self.ledger.begin_case('case1')
    def tearDown(self):self.temp.cleanup()
    def run_call(self,fake=None,model=TASK_MODEL):
        client=BudgetClient('fake-private-token',self.ledger,'case1',client_factory=fake or Fake())
        return asyncio.run(client.complete([{'role':'user','content':'Only synthetic data'}],{'model':model,'temperature':0,'max_output_tokens':800}))
    def test_real_request_uses_current_price_filters_and_cost(self):
        fake=Fake();self.run_call(fake,JUDGE_MODEL)
        self.assertEqual(fake.calls[0][2]['json']['provider']['max_price'],{'prompt':0.1,'completion':0.32,'request':0})
        self.assertEqual(self.ledger.stats()['reserved_requests'],1)
        self.assertEqual(self.ledger.stats()['known_actual_nano_usd'],100000)
    def test_approval_is_required_and_bound_to_prices_and_cap(self):
        with self.assertRaises(WorkflowError):approve_budget('yes',self.pricing,self.limits)
        altered=deepcopy(self.limits);altered['max_calls']=5
        with self.assertRaises(WorkflowError):BudgetLedger(Path(self.temp.name)/'other','final34',self.pricing,altered,self.approval)
    def test_stale_or_unknown_prices_block(self):
        stale=deepcopy(self.pricing);stale['verification']['verified_at']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        with self.assertRaises(WorkflowError):validate_pricing(stale)
        del stale['models'][JUDGE_MODEL]
        with self.assertRaises(WorkflowError):validate_pricing(stale)
    def test_public_price_lookup_handles_exact_models_and_explicit_zero(self):
        rows=[{'id':m,'pricing':{'prompt':'0.0000001','completion':'0.0000003','request':'0'}} for m in APPROVED_MODELS]
        fake=Fake({'data':rows})
        got=asyncio.run(fetch_current_pricing(client_factory=fake))
        self.assertEqual(set(got['models']),set(APPROVED_MODELS))
        self.assertEqual(fake.calls[0][0],'GET')
        self.assertEqual(got['verification']['request_charge_policy']['catalogue_request_fields'],
            {model:'explicit_zero' for model in APPROVED_MODELS})
    def test_missing_actual_cost_poisoned(self):
        bad={'model':TASK_MODEL,'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],
             'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
        with self.assertRaises(WorkflowError):self.run_call(Fake(bad))
        self.assertTrue(self.ledger.stats()['poisoned'])
    def test_call_caps_persist_without_refunds(self):
        self.run_call();self.run_call()
        with self.assertRaises(WorkflowError):self.run_call()
        resumed=BudgetLedger(self.path,'final34',self.pricing,self.limits,self.approval)
        self.assertEqual(resumed.stats()['reserved_requests'],2)
        with self.assertRaises(WorkflowError):resumed.begin_case('case1')
    def test_interrupt_retains_unknown_charge_without_poison(self):
        with self.assertRaises(KeyboardInterrupt):self.run_call(Fake(exc=KeyboardInterrupt()))
        stats=self.ledger.stats();self.assertEqual(stats['reserved_requests'],1);self.assertFalse(stats['poisoned'])
    def test_time_limit_persists_across_resume(self):
        with sqlite3.connect(self.path) as db:db.execute("UPDATE metadata SET value=? WHERE name='started_at'",('1',))
        with self.assertRaises(WorkflowError) as caught:self.run_call()
        self.assertEqual(caught.exception.code,'live_time_limit')
        self.assertEqual(self.ledger.stats()['reserved_requests'],0)
    def test_empty_ledger_cannot_replenish_budget(self):
        self.path.write_bytes(b'')
        with self.assertRaises(WorkflowError):BudgetLedger(self.path,'final34',self.pricing,self.limits,self.approval)
    def test_changed_or_removed_ledger_fails_closed(self):
        self.path.unlink()
        with self.assertRaises(WorkflowError):self.ledger.stats()
        self.assertFalse(self.path.exists())
    def test_unattested_strict_schema_rejected_before_reservation(self):
        client=BudgetClient('fake-key',self.ledger,'case1',client_factory=Fake())
        with self.assertRaises(WorkflowError):asyncio.run(client.complete([{'role':'user','content':'test'}],
            {'model':TASK_MODEL,'temperature':0,'max_output_tokens':800,'_response_format':{'type':'json_schema'}}))
        self.assertEqual(self.ledger.stats()['reserved_requests'],0)
    def test_actual_production_critic_schema_supported(self):
        from workflow_critic_protocol import critic_protocol
        fmt=critic_protocol(0,{'text':'No source.','evidence_ids':[]},{})['response_format']
        client=BudgetClient('fake-key',self.ledger,'case1',client_factory=Fake())
        asyncio.run(client.complete([{'role':'user','content':'test'}],{'model':TASK_MODEL,'temperature':0,'max_output_tokens':800,
            '_response_format':fmt,'_assay_response_format_sha256':digest(fmt)}))
        self.assertEqual(self.ledger.stats()['reserved_requests'],1)


class PricingLookupTests(unittest.TestCase):
    def rows(self):
        # Exact pricing shapes observed in the public catalogue on 2026-11-14.
        return [{'id':TASK_MODEL,'pricing':{'prompt':'0.0000004','completion':'0.0000016',
                    'web_search':'0.01','input_cache_read':'0.0000001'}},
                {'id':JUDGE_MODEL,'pricing':{'prompt':'0.00000071','completion':'0.00000071',
                    'input_cache_read':'0.00000071'}}]
    def fetch(self, rows=None, fake=None):
        return asyncio.run(fetch_current_pricing(client_factory=fake or Fake({'data':rows if rows is not None else self.rows()})))
    def assert_rejected(self, fake, reason):
        with self.assertRaises(WorkflowError) as caught:self.fetch(fake=fake)
        self.assertEqual(caught.exception.code,'price_verification_required')
        self.assertIn('('+reason+')',str(caught.exception))
        self.assertNotIn('SECRET',str(caught.exception))
        self.assertLess(len(str(caught.exception)),200)
    def test_current_catalogue_omission_records_routing_ceiling_not_advertised_zero(self):
        fake=Fake({'data':self.rows()});got=self.fetch(fake=fake)
        self.assertEqual(got['models'][TASK_MODEL],{'family':'openai','prompt_usd_per_million':'0.4000000',
            'completion_usd_per_million':'1.6000000','request_usd':'0'})
        self.assertEqual(got['models'][JUDGE_MODEL]['prompt_usd_per_million'],'0.71000000')
        policy=got['verification']['request_charge_policy']
        self.assertEqual(policy['catalogue_request_fields'],{model:'omitted' for model in APPROVED_MODELS})
        self.assertEqual(policy['provider_max_price_request_usd'],'0')
        self.assertIn('not advertised zero',policy['interpretation'])
        self.assertEqual(fake.calls,[('GET',MODELS_ENDPOINT,{})])
        self.assertEqual(fake.client_options,{'trust_env':False,'follow_redirects':False,'timeout':30})
        self.assertEqual(got['verification']['response_sha256'],hashlib.sha256(json.dumps({'data':self.rows()}).encode()).hexdigest())
    def test_explicit_zero_representation_and_mixed_catalogue_preserved(self):
        rows=self.rows();rows[0]['pricing']['request']='0.00';got=self.fetch(rows)
        self.assertEqual(got['models'][TASK_MODEL]['request_usd'],'0.00')
        self.assertEqual(got['models'][JUDGE_MODEL]['request_usd'],'0')
        self.assertEqual(got['verification']['request_charge_policy']['catalogue_request_fields'],
            {TASK_MODEL:'explicit_zero',JUDGE_MODEL:'omitted'})
    def test_explicit_invalid_or_nonzero_request_price_stays_rejected(self):
        for value in [None,False,True,'SECRET-invalid','NaN','Infinity','-0.1','0.000001',100001,[],{}]:
            with self.subTest(value=value):
                rows=self.rows();rows[0]['pricing']['request']=value
                self.assert_rejected(Fake({'data':rows}),'request_charge_invalid')
    def test_required_token_prices_remain_required_and_bounded(self):
        for field in ['prompt','completion']:
            rows=self.rows();del rows[0]['pricing'][field]
            self.assert_rejected(Fake({'data':rows}),'required_token_price_invalid')
            for value in [None,False,True,'SECRET-invalid','NaN','Infinity','-0.1','0.100001',[],{}]:
                with self.subTest(field=field,value=value):
                    rows=self.rows();rows[0]['pricing'][field]=value
                    self.assert_rejected(Fake({'data':rows}),'required_token_price_invalid')
    def test_missing_and_duplicate_approved_ids_are_distinguished(self):
        self.assert_rejected(Fake({'data':self.rows()[:1]}),'missing_model_ids')
        rows=self.rows();rows.append(deepcopy(rows[0]))
        self.assert_rejected(Fake({'data':rows}),'duplicate_model_id')
    def test_invalid_catalogue_schema_and_json_have_safe_reasons(self):
        for body in [[],{'data':{}},{'data':[None]},{'data':[{'id':TASK_MODEL,'pricing':None}]}]:
            self.assert_rejected(Fake(body),'catalogue_schema')
        class RawFake(Fake):
            async def aiter_bytes(self):yield b'{SECRET-not-json'
        self.assert_rejected(RawFake(),'invalid_json')
    def test_http_errors_report_only_status_and_never_read_error_body(self):
        class ErrorBodyFake(Fake):
            async def aiter_bytes(self):raise AssertionError('SECRET body must not be read');yield b''
        for status in [401,429,503]:
            self.assert_rejected(ErrorBodyFake(status=status),'http_status_'+str(status))
    def test_network_errors_report_fixed_categories_without_raw_details(self):
        for exc,reason in [(httpx.ReadTimeout('SECRET timeout'),'network_timeout'),
                           (TimeoutError('SECRET timeout'),'network_timeout'),
                           (httpx.ConnectError('SECRET proxy credentials'),'connection_error'),
                           (httpx.ReadError('SECRET network'),'network_error')]:
            self.assert_rejected(Fake(exc=exc),reason)
    def test_oversized_catalogue_fails_before_parsing(self):
        class BigFake(Fake):
            async def aiter_bytes(self):
                for _ in range(11):yield b' '*1000000
        self.assert_rejected(BigFake(),'catalogue_oversized')
    def test_omitted_request_pricing_keeps_ceiling_on_both_paid_dispatches(self):
        rates=self.fetch();limits=deepcopy(DEFAULT_LIMITS)
        approval=approve_budget('APPROVE',rates,limits)
        with tempfile.TemporaryDirectory() as folder:
            ledger=BudgetLedger(Path(folder)/'ledger.sqlite','compatible-pilot',rates,limits,approval)
            ledger.begin_case('case1')
            for model in APPROVED_MODELS:
                fake=Fake();client=BudgetClient('SECRET-private-token',ledger,'case1',client_factory=fake)
                asyncio.run(client.complete([{'role':'user','content':'Synthetic test'}],
                    {'model':model,'temperature':0,'max_output_tokens':800}))
                method,url,kwargs=fake.calls[0]
                self.assertEqual((method,url),('POST',ENDPOINT))
                payload=kwargs['json'];self.assertEqual(payload['provider']['max_price']['request'],0)
                self.assertFalse(payload['provider']['allow_fallbacks'])
                self.assertTrue(payload['provider']['require_parameters'])
                self.assertNotIn('plugins',payload);self.assertNotIn('tools',payload)
            resumed=BudgetLedger(Path(folder)/'ledger.sqlite','compatible-pilot',rates,limits,approval)
            self.assertEqual(resumed.stats()['reserved_requests'],2)
            self.assertEqual(approval['pricing_identity_sha256'],digest(rates['models']))
    def test_new_verification_metadata_does_not_reset_existing_ledger_identity(self):
        rates=self.fetch();old=deepcopy(rates);del old['verification']['request_charge_policy']
        limits=deepcopy(DEFAULT_LIMITS);approval=approve_budget('APPROVE',old,limits)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'ledger.sqlite';ledger=BudgetLedger(path,'same-batch',old,limits,approval)
            ledger.begin_case('case1');fake=Fake()
            asyncio.run(BudgetClient('private-token',ledger,'case1',client_factory=fake).complete(
                [{'role':'user','content':'Synthetic test'}],{'model':TASK_MODEL,'temperature':0,'max_output_tokens':800}))
            before=ledger.stats();resumed=BudgetLedger(path,'same-batch',rates,limits,approval)
            self.assertEqual(resumed.stats(),before)
            self.assertEqual(digest(old['models']),digest(rates['models']))

if __name__=='__main__':unittest.main()
