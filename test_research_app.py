"""Offline UI, operator gate and Compose checks; no model or portal calls."""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import research_app as ui
from workflow_contracts import WorkflowError
TOKEN='synthetic-ui-token-'+'x'*40
HASH='a'*64
REQUEST={'question':'Compare operating margins and competition risks for Meta in FY2025.','tickers':['META'],'years':[2025],'include_risks':True}
PLAN={'objective':'Compare operating margins and risks','outline':['Read source evidence'],'research_query':'competition risks','limitations':[]}

class StubController:
    def __init__(self): self.calls=[]
    def config(self): return {'model':'test-only','model_key_available':False,'companies':[]}
    def list_runs(self): return [{'run_id':'demo','active':False}]
    async def status(self,run): return {'run_id':run,'status':'awaiting_plan','running':False,'review':{'gate':'plan','expected_hash':HASH,'plan':PLAN},'events':[]}
    async def start(self,payload): self.calls.append(('start',payload.model_dump()));return {'accepted':True}
    async def continue_run(self,run,payload): self.calls.append(('continue',run));return {'accepted':True}
    async def decide(self,run,payload): self.calls.append(('decision',payload.model_dump()));return {'accepted':True}
    async def pause(self,run): self.calls.append(('pause',run));return {'paused':True}
    async def close(self): pass

class BrowserBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.control=StubController();self.client=TestClient(ui.create_app(self.control,token_loader=lambda:TOKEN));self.headers={'Authorization':'Bearer '+TOKEN}
    def tearDown(self): self.client.close()
    def test_health_ui_public_without_research_and_hash_csp(self):
        self.assertEqual(self.client.get('http://localhost/health').status_code,200)
        response=self.client.get('http://localhost/');self.assertEqual(response.status_code,200)
        self.assertIn('Unlock your workspace',response.text);self.assertEqual(self.control.calls,[])
        self.assertIn('sha256-',response.headers['content-security-policy'])
    def test_api_requires_ui_token(self):
        for headers in ({},{'Authorization':'Bearer wrong'}): self.assertEqual(self.client.get('http://localhost/api/runs',headers=headers).status_code,401)
        self.assertEqual(self.client.get('http://localhost/api/runs',headers=self.headers).status_code,200)
    def test_missing_server_token_fails_closed(self):
        with TestClient(ui.create_app(self.control,token_loader=lambda:None)) as client:
            self.assertEqual(client.get('http://localhost/api/config',headers=self.headers).status_code,401)
    def test_cross_origin_decision_refused(self):
        r=self.client.post('http://localhost/api/runs/demo/decision',headers={**self.headers,'Origin':'https://attacker.invalid'},json={})
        self.assertEqual(r.status_code,403);self.assertEqual(self.control.calls,[])
    def test_untrusted_host_rejected(self): self.assertEqual(self.client.get('http://attacker.invalid/health').status_code,400)
    def test_start_requires_cost_consent_and_rejects_arbitrary_commands(self):
        data={'run_id':'demo','request':REQUEST,'accept_model_costs':False}
        self.assertEqual(self.client.post('http://localhost/api/runs',headers=self.headers,json=data).status_code,422)
        data['accept_model_costs']=True;data['shell']='echo never'
        self.assertEqual(self.client.post('http://localhost/api/runs',headers=self.headers,json=data).status_code,422)
        data.pop('shell');data['run_id']='../../outside'
        self.assertEqual(self.client.post('http://localhost/api/runs',headers=self.headers,json=data).status_code,422)
        data['run_id']='demo'
        self.assertEqual(self.client.post('http://localhost/api/runs',headers=self.headers,json=data).status_code,202)
        self.assertEqual(len(self.control.calls),1)
    def test_large_body_refused(self): self.assertEqual(self.client.post('http://localhost/api/runs',headers=self.headers,content='x'*129000).status_code,413)
    def test_authenticated_sse_has_status_and_idle_without_secret(self):
        r=self.client.get('http://localhost/api/runs/demo/events',headers=self.headers)
        self.assertEqual(r.status_code,200);self.assertIn('event: status',r.text);self.assertIn('event: idle',r.text);self.assertNotIn(TOKEN,r.text)
    def test_source_html_not_interpreted(self):
        html=Path(ui.__file__).with_name('research_ui.html').read_text()
        self.assertNotIn('innerHTML',html);self.assertNotIn('insertAdjacentHTML',html);self.assertIn('textContent',html)
    def test_authenticated_pause_route(self):
        self.assertEqual(self.client.post('http://localhost/api/runs/demo/pause',headers=self.headers,json={}).status_code,200)
        self.assertEqual(self.control.calls,[('pause','demo')])

class FakeStore:
    def __init__(self): self.events=[]
    def report(self,run): return {'budget':{'token_limit':900000,'reserved_tokens':0},'metrics':{'llm_requests_started':0},'events':self.events}
    def evidence(self,run): return {}
    def event(self,run,kind,role,details): self.events.append({'seq':len(self.events)+1,'kind':kind,'role':role,'details':details,'at':0})
class FakeHarness:
    def __init__(self): self.store=FakeStore();self.values={};self.gate=None;self.next=('supervisor',);self.resumes=[];self.review_calls=[];self.approvals=[];self.block=False
    def snapshot(self):
        tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value=self.gate)])] if self.gate else []
        return SimpleNamespace(values=self.values,next=self.next,tasks=tasks)
    @asynccontextmanager
    async def open(self,folder,run,request,config,model,backend,**kwargs):
        harness=self;workflow=SimpleNamespace(broker=SimpleNamespace(store=self.store),approval=None)
        class Graph:
            async def aget_state(self,options): return harness.snapshot()
            async def astream(self,value,options,stream_mode):
                if harness.block: await asyncio.sleep(60)
                if value is not None and hasattr(value,'resume'):
                    harness.resumes.append(value.resume);harness.approvals.append(workflow.approval);action=value.resume['action']
                    if action=='edit':
                        harness.gate={**harness.gate,'expected_hash':'b'*64,'plan':value.resume['replacement']};harness.values={'status':'awaiting_plan','plan':value.resume['replacement']}
                    else: harness.gate=None;harness.next=();harness.values={'status':'rejected' if action=='reject' else 'published'}
                else:
                    harness.values={'status':'awaiting_plan','plan':PLAN};harness.gate={'gate':'plan','expected_hash':HASH,'plan':PLAN};harness.next=('plan_review',)
                yield {'supervisor':{}}
        yield workflow,Graph(),{}
    async def review(self,backend,keys,gate,action,reviewer):
        self.review_calls.append((gate,action,reviewer))
        return {'token':'SYNTHETIC_CAPABILITY_NEVER_EXPORT','content_hash':gate['expected_hash'],'proposal_id':'synthetic'} if action=='approve' else None

class WorkflowOperatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.h=FakeHarness();self.keys={'OPENROUTER_API_KEY':'synthetic-model-key','PORTAL_REVIEW_TOKEN':'synthetic-review-key'}
        self.c=ui.ResearchController(Path(self.tmp.name),key_loader=lambda:self.keys,model_factory=lambda key:object(),backend_factory=lambda keys:object(),workflow_factory=self.h.open,review_function=self.h.review)
    async def asyncTearDown(self): await self.c.close()
    async def start(self):
        await self.c.start(ui.StartRequest(run_id='demo',request=REQUEST,accept_model_costs=True));await self.c.task
    def decision(self,action='approve'):
        return ui.BrowserDecision(action=action,expected_hash=HASH,replacement=None,reviewer='Synthetic test operator',confirmation=action.upper(),accept_model_costs=True)
    async def test_plan_pauses_and_config_survives_restart(self):
        await self.start();self.assertEqual((await self.c.status('demo'))['review']['gate'],'plan');self.assertEqual(self.h.resumes,[])
        other=ui.ResearchController(Path(self.tmp.name),key_loader=lambda:self.keys,workflow_factory=self.h.open,backend_factory=lambda keys:object())
        self.assertEqual((await other.status('demo'))['review']['expected_hash'],HASH)
    async def test_continue_cannot_approve_gate(self):
        await self.start()
        with self.assertRaises(WorkflowError): await self.c.continue_run('demo',ui.ContinueRequest(accept_model_costs=True))
        self.assertEqual(self.h.resumes,[])
    async def test_stale_hash_and_confirmation_refused(self):
        await self.start();d=self.decision();d.expected_hash='f'*64
        with self.assertRaises(WorkflowError): await self.c.decide('demo',d)
        d=self.decision();d.confirmation='REJECT'
        with self.assertRaises(WorkflowError): await self.c.decide('demo',d)
        self.assertEqual(self.h.resumes,[])
    async def test_plan_edit_requires_new_review(self):
        await self.start();d=self.decision('edit');d.replacement={**PLAN,'objective':'Edited scoped objective'}
        await self.c.decide('demo',d);await self.c.task
        s=await self.c.status('demo');self.assertEqual(s['status'],'awaiting_plan');self.assertEqual(s['review']['expected_hash'],'b'*64);self.assertEqual(self.h.review_calls,[])
    async def test_reject_without_model_key(self):
        await self.start();self.keys.pop('OPENROUTER_API_KEY');d=self.decision('reject');d.accept_model_costs=False
        await self.c.decide('demo',d);await self.c.task;self.assertEqual((await self.c.status('demo'))['status'],'rejected')
    async def test_note_approval_uses_separate_review_capability_not_exported(self):
        await self.start();self.h.gate={'gate':'note','expected_hash':HASH,'proposal_id':'synthetic','note':{'title':'Synthetic note'}};self.h.values={'status':'awaiting_note'}
        with patch.object(ui,'require_current_support') as support:
            await self.c.decide('demo',self.decision());await self.c.task
        support.assert_called_once();self.assertEqual(self.h.review_calls[0][1],'approve');self.assertEqual(self.h.approvals[0]['token'],'SYNTHETIC_CAPABILITY_NEVER_EXPORT')
        self.assertNotIn('SYNTHETIC_CAPABILITY_NEVER_EXPORT',json.dumps(await self.c.status('demo')))
    async def test_failed_support_prevents_portal_approval(self):
        await self.start();self.h.gate={'gate':'note','expected_hash':HASH};self.h.values={'status':'awaiting_note'}
        with patch.object(ui,'require_current_support',side_effect=WorkflowError('source_instruction','blocked')):
            await self.c.decide('demo',self.decision());await self.c.task
        self.assertEqual(self.h.review_calls,[]);self.assertEqual(self.h.resumes,[]);self.assertEqual(self.c.errors['demo']['code'],'source_instruction')
    async def test_pause_keeps_manifest_and_reservations(self):
        self.h.block=True;await self.c.start(ui.StartRequest(run_id='demo',request=REQUEST,accept_model_costs=True));await asyncio.sleep(.02);await self.c.pause('demo')
        self.assertTrue((Path(self.tmp.name)/'demo.request.json').is_file());self.assertEqual(self.c.errors['demo']['code'],'execution_interrupted')
    async def test_existing_run_and_path_traversal_refused(self):
        await self.start()
        with self.assertRaises(WorkflowError): await self.start()
        with self.assertRaises(WorkflowError): self.c.path('../../escape','.request.json')
    async def test_missing_key_creates_no_run(self):
        self.keys.pop('OPENROUTER_API_KEY')
        with self.assertRaises(WorkflowError): await self.start()
        self.assertEqual(self.c.list_runs(),[])
    async def test_parallel_run_refused(self):
        self.h.block=True;await self.c.start(ui.StartRequest(run_id='demo',request=REQUEST,accept_model_costs=True));await asyncio.sleep(.02)
        with self.assertRaises(WorkflowError): await self.c.start(ui.StartRequest(run_id='second',request=REQUEST,accept_model_costs=True))
        self.assertFalse((Path(self.tmp.name)/'second.request.json').exists())

class StartupAndComposeTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('start_test',Path(ui.__file__).with_name('start_analyst_desk.py'));self.s=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.s)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_setup_preserves_credentials_and_token_on_repeat(self):
        (self.root/'.env').write_text('OPENROUTER_API_KEY=synthetic-model-secret-not-real\n');first=self.s.setup(self.root);raw=(self.root/'.env').read_bytes();token=(self.root/'data/research_app/access_token').read_bytes();second=self.s.setup(self.root)
        self.assertEqual(first,second);self.assertEqual(raw,(self.root/'.env').read_bytes());self.assertEqual(token,(self.root/'data/research_app/access_token').read_bytes());self.assertEqual(len({first[k] for k in self.s.LOCAL_KEYS}),len(self.s.LOCAL_KEYS))
    def test_setup_never_replaces_bad_existing_secret(self):
        (self.root/'.env').write_text('OPENROUTER_API_KEY=synthetic-model-secret-not-real\nPORTAL_REVIEW_TOKEN=short\n');raw=(self.root/'.env').read_bytes()
        with self.assertRaises(ValueError): self.s.setup(self.root)
        self.assertEqual(raw,(self.root/'.env').read_bytes())
    def test_log_scrub(self): self.assertNotIn('s'*40,self.s.scrub('error '+('s'*40),{'key':'s'*40}))
    def test_startup_report_directory_is_new_and_rejects_symlinks(self):
        requested=self.root/'nested'/'startup'
        self.assertEqual(self.s.new_report_directory(self.root,str(requested),'fixture'),requested)
        with self.assertRaises(FileExistsError): self.s.new_report_directory(self.root,str(requested),'fixture')
        linked=self.root/'linked';linked.symlink_to(requested,target_is_directory=True)
        with self.assertRaises(ValueError): self.s.new_report_directory(self.root,str(linked/'output'),'fixture')
        self.assertFalse((requested/'output').exists())
        self.assertEqual(self.s.new_report_directory(self.root,None,'fixture').name,'39_Analyst_Desk_Startup_fixture')
    def test_compose_loopback_and_role_secrets(self):
        import yaml
        c=yaml.safe_load(Path(ui.__file__).with_name('compose.final.yaml').read_text());s=c['services']
        self.assertEqual(s['research-app']['network_mode'],'service:retrieval-gateway');self.assertIn('127.0.0.1:8080:8080',s['retrieval-gateway']['ports'])
        self.assertNotIn('OPENROUTER_API_KEY',s['python-sandbox']['environment']);self.assertNotIn('PORTAL_REVIEW_TOKEN',s['python-sandbox']['environment']);self.assertNotIn('environment',s['edgar'])
        self.assertEqual(c['volumes']['grafana-data']['name'],'analyst-desk-monitoring_grafana-data')
        for service in s.values():
            for port in service.get('ports',[]): self.assertTrue(port.startswith('127.0.0.1:'))
    def test_published_services_have_a_noninternal_gateway_network(self):
        import yaml
        c=yaml.safe_load(Path(ui.__file__).with_name('compose.final.yaml').read_text())
        for name,service in c['services'].items():
            if service.get('ports'):
                self.assertTrue(any(not c['networks'][network].get('internal',False)
                                    for network in service.get('networks',[])),name)
    def test_edgar_host_route_uses_existing_gateway_and_preserves_isolation(self):
        import yaml
        c=yaml.safe_load(Path(ui.__file__).with_name('compose.final.yaml').read_text());services=c['services']
        self.assertFalse(services['edgar'].get('ports'))
        self.assertEqual(services['edgar']['networks'],['retrieval'])
        self.assertTrue(c['networks']['retrieval']['internal'])
        owners=[name for name,service in services.items() if '127.0.0.1:8001:8001' in service.get('ports',[])]
        self.assertEqual(owners,['retrieval-gateway'])
        gateway=Path(ui.__file__).with_name('retrieval_gateway.conf').read_text()
        self.assertEqual(gateway.count('listen 8001;'),1)
        route=gateway.split('listen 8001;',1)[1].split('\n    }',1)[0]
        self.assertIn('set $edgar_upstream edgar:8001;',route)
        self.assertIn('proxy_pass http://$edgar_upstream;',route)
        self.assertNotIn('$arg_',route)
    def test_health_http_error_retains_status_without_response_or_exception_text(self):
        from urllib.error import HTTPError
        secret='DO_NOT_EXPORT_SYNTHETIC_CREDENTIAL'
        error=HTTPError(self.s.CHECKS['edgar'],403,secret,{'X-Secret':secret},None)
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.side_effect=error
            result=self.s.probe(self.s.CHECKS['edgar'])
        self.assertEqual(result,{'healthy':False,'http_status':403,'error_category':'http_error'})
        self.assertNotIn(secret,json.dumps(result))
    def test_health_connection_failures_export_only_bounded_categories(self):
        from urllib.error import URLError
        secret='DO_NOT_EXPORT_SYNTHETIC_CREDENTIAL'
        cases=[(URLError(ConnectionRefusedError(secret)),'connection_refused'),
               (URLError(TimeoutError(secret)),'timeout'),(TimeoutError(secret),'timeout'),
               (URLError(OSError(secret)),'connection_error'),(OSError(secret),'connection_error'),
               (ValueError(secret),'probe_error')]
        for error,category in cases:
            with self.subTest(category=category),patch('urllib.request.build_opener') as build:
                build.return_value.open.side_effect=error
                result=self.s.probe(self.s.CHECKS['edgar'])
                self.assertEqual(result,{'healthy':False,'error_category':category})
                self.assertNotIn(secret,json.dumps(result))
    def test_startup_failure_names_the_failed_service_in_report_and_console(self):
        output=self.root/'report'
        def probe(url):
            return {'healthy':False,'error_category':'connection_refused'} if url==self.s.CHECKS['edgar'] else {'healthy':True,'http_status':200}
        with patch.object(self.s,'ROOT',self.root),patch.object(self.s,'prerequisites',return_value=[]),patch.object(self.s,'dotenv_values',return_value={}),patch.object(self.s,'setup',return_value={}),patch.object(self.s,'command',return_value=''),patch.object(self.s,'probe',side_effect=probe),patch('sys.argv',['start_analyst_desk.py','--report-dir',str(output)]),patch('builtins.print') as printed:
            self.assertEqual(self.s.main(),1)
        report=json.loads((output/'Startup_Report.json').read_text())
        self.assertEqual(report['sequence'],39)
        self.assertEqual(report['failed_service_names'],['edgar'])
        self.assertEqual(report['health']['edgar']['error_category'],'connection_refused')
        self.assertTrue(any('failed: edgar.' in str(call.args) for call in printed.call_args_list))
        self.assertFalse(list(output.glob('*.zip')))

class MemoryOperatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.control=ui.ResearchController(Path(self.tmp.name),key_loader=lambda:{})
        self.client=TestClient(ui.create_app(self.control,token_loader=lambda:TOKEN))
        self.headers={'Authorization':'Bearer '+TOKEN}
    def tearDown(self): self.client.close()
    def test_preference_correction_and_delete_require_current_hash(self):
        url='http://localhost/api/memory/preferences/detail_level'
        first=self.client.put(url,headers=self.headers,json={'value':'concise'}).json()
        self.assertEqual(first['value'],'concise')
        self.assertEqual(self.client.put(url,headers=self.headers,json={'value':'detailed'}).status_code,400)
        changed=self.client.put(url,headers=self.headers,json={'value':'detailed','expected_hash':first['content_sha256']}).json()
        self.assertEqual(changed['value'],'detailed')
        self.assertEqual(self.client.request('DELETE',url,headers=self.headers,json={'expected_hash':first['content_sha256']}).status_code,400)
        self.assertEqual(self.client.request('DELETE',url,headers=self.headers,json={'expected_hash':changed['content_sha256']}).status_code,200)
    def test_watchlist_and_owner_boundaries(self):
        url='http://localhost/api/memory/watchlist/focus'
        response=self.client.put(url,headers=self.headers,json={'label':'Private synthetic label','note':'Local-only synthetic note'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get('http://localhost/api/memory').status_code,401)
        self.assertEqual(self.client.put(url,headers=self.headers,json={'label':'x','owner':'another-person'}).status_code,422)
        self.assertEqual(self.client.put('http://localhost/api/memory/published_notes/fake',headers=self.headers,json={'value':'forged'}).status_code,400)
    def test_lesson_review_is_explicit_and_hash_bound(self):
        lesson=self.control.lesson_memory().propose({'tags':['risk'],'roles':['critic']},'Check every claimed fact against the supplied original evidence.','synthetic-development-case')
        url='http://localhost/api/lessons/'+lesson['lesson_id']+'/review'
        body={'expected_hash':lesson['proposal_sha256'],'action':'approve','reason':'Synthetic operator test fixture','confirmation':'REJECT'}
        self.assertEqual(self.client.post(url,headers=self.headers,json=body).status_code,400)
        self.assertEqual(self.control.lesson_memory().list()[0]['status'],'draft')
        body['confirmation']='APPROVE';body['expected_hash']='f'*64
        self.assertEqual(self.client.post(url,headers=self.headers,json=body).status_code,400)
        body['expected_hash']=lesson['proposal_sha256']
        self.assertEqual(self.client.post(url,headers=self.headers,json=body).status_code,200)
        self.assertEqual(self.control.lesson_memory().list()[0]['status'],'approved')

if __name__=='__main__':unittest.main()
