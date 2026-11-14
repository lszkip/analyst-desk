"""Real local HTTP MCP integration, mocked model API, and separate-process resume."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from argparse import Namespace

import httpx
from langgraph.types import Command
from edgar_mcp import create_server as edgar_server
from filings_kb_mcp import create_server as kb_server
from filing_retrieval import SearchEngine
from python_sandbox_mcp import create_server as sandbox_server
from research_portal import PortalStore
from research_portal_mcp import create_server as portal_server
from research_workflow import open_workflow
from run_research_workflow import pending_gate, portal_review, run, extend_development_budget
from workflow_agents import OpenRouterModel, load_config
from workflow_tools import MCPBackend
from workflow_contracts import WorkflowError, canonical
from workflow_store import RunStore
from check_research_workflow import check as preflight_check, validate_coverage
from test_filing_retrieval import build_fixture
from test_research_tools_mcp import serve, READ_TOKEN, DRAFT_TOKEN, PUBLISH_TOKEN, REVIEW_TOKEN, SANDBOX_TOKEN
from test_research_workflow import REQUEST, FixtureModel


KEYS={"PORTAL_READ_TOKEN":READ_TOKEN,"PORTAL_DRAFT_TOKEN":DRAFT_TOKEN,"PORTAL_PUBLISH_TOKEN":PUBLISH_TOKEN,"PORTAL_REVIEW_TOKEN":REVIEW_TOKEN,"SANDBOX_API_TOKEN":SANDBOX_TOKEN}


class WorkflowHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_through_all_four_real_http_mcp_services_and_human_route(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            catalogue,models,qdrant=build_fixture(root)
            try:
                engine=SearchEngine(catalogue,models,qdrant,index_dir=root/"index")
                portal=PortalStore(root/"portal.sqlite3")
                async with serve(edgar_server(root/"raw",passages_dir=root/"passages",corpus_dir=root/"filings")) as eu, serve(kb_server(engine)) as ku, serve(sandbox_server(SANDBOX_TOKEN)) as su, serve(portal_server(portal,READ_TOKEN,DRAFT_TOKEN,PUBLISH_TOKEN,REVIEW_TOKEN)) as pu:
                    backend=MCPBackend(KEYS,{"edgar":eu,"kb":ku,"sandbox":su,"portal":pu})
                    preflight=await preflight_check(backend=backend,expected_pairs=list(catalogue.documents))
                    self.assertEqual(preflight["status"],"ok")
                    self.assertEqual(preflight["company_years"],4)
                    self.assertFalse(preflight["model_called"])
                    self.assertFalse(preflight["note_created_or_published"])
                    self.assertEqual(portal.list_notes()["total"],0)
                    with portal.connect() as db:
                        self.assertEqual(db.execute("SELECT count(*) FROM proposals").fetchone()[0],0)
                    section=await backend.call("edgar","get_section",{"ticker":"MSFT","year":2025,"item":"1A","offset":0,"limit":1})
                    self.assertTrue(section["passages"])
                    async with open_workflow(root/"workflow","http_test",REQUEST,load_config(),FixtureModel(),backend) as (w,g,o):
                        await g.ainvoke({"request":REQUEST,"researchers":{}},o)
                        gate=pending_gate(await g.aget_state(o))
                        await g.ainvoke(Command(resume={"action":"approve","expected_hash":gate["expected_hash"],"replacement":None}),o)
                        gate=pending_gate(await g.aget_state(o))
                        self.assertEqual(gate["gate"],"note")
                        self.assertIn("40.00%",gate["note"]["body_markdown"])
                        self.assertIn("25.00%",gate["note"]["body_markdown"])
                        self.assertEqual(portal.list_notes()["total"],0)
                        w.approval=await portal_review(backend,KEYS,gate,"approve","Synthetic HTTP workflow reviewer")
                        token=w.approval["token"]
                        await g.ainvoke(Command(resume={"action":"approve","expected_hash":gate["expected_hash"],"replacement":None}),o)
                        state=(await g.aget_state(o)).values
                        self.assertEqual(state["status"],"published")
                        self.assertEqual(portal.list_notes()["total"],1)
                        self.assertNotIn(token,canonical(state))
                        services={e["details"].get("service") for e in w.broker.store.report("http_test")["events"] if e["kind"]=="tool_end"}
                        self.assertEqual(services,{"edgar","kb","sandbox","portal"})
            finally:
                qdrant.close()

    async def test_backend_rejects_nonlocal_endpoints_and_missing_scoped_key(self):
        with self.assertRaises(ValueError):
            MCPBackend(KEYS,{"portal":"https://attacker.invalid"})
        backend=MCPBackend({})
        with self.assertRaisesRegex(WorkflowError,"credential"):
            await backend.call("sandbox","execute_python",{})
        self.assertNotIn("PORTAL_REVIEW_TOKEN",backend.tokens)

    async def test_preflight_rejects_incomplete_scope_duplicates_and_warnings(self):
        expected=[("MSFT",2025),("GOOGL",2025)]
        valid={"company_years":2,"pairs":[list(p) for p in expected],"coverage_warnings":[]}
        self.assertEqual(validate_coverage(valid,expected),2)
        invalid=[{**valid,"company_years":1},{**valid,"pairs":[["MSFT",2025],["MSFT",2025]]},
                 {**valid,"pairs":[["MSFT",2025],["GOOGL",2024]]},{**valid,"coverage_warnings":[{"item":"8"}]},
                 {"verified_company_years":2,"pairs":valid["pairs"],"coverage_warnings":[]}]
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(WorkflowError):
                validate_coverage(value,expected)

    async def test_preflight_refuses_empty_kernel_probe_report(self):
        backend=Namespace(call=AsyncMock(side_effect=[
            {"company_years":1,"pairs":[["MSFT",2025]],"coverage_warnings":[]},
            {"passages":[{"source_sha256":"a"*64}]},
            {"status":"ok","value":100,"ticker":"MSFT","fiscal_year":2025,"metric":"revenue","unit":"USD"},
            {"result":"25.00","kernel_probes":{}}]))
        with self.assertRaises(WorkflowError) as caught:
            await preflight_check(backend=backend,expected_pairs=[("MSFT",2025)])
        self.assertEqual(caught.exception.code,"sandbox_probes")
        self.assertEqual(backend.call.await_count,4)

    async def test_cli_refuses_piped_human_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/"test.request.json").write_text(canonical(REQUEST))
            args=Namespace(run_id="test",state_dir=root,command="review",config=None,action="approve",edited_json=None,reviewer="test")
            with patch("run_research_workflow.credentials",return_value={}),patch("run_research_workflow.sys.stdin.isatty",return_value=False):
                with self.assertRaisesRegex(WorkflowError,"interactive terminal"):
                    await run(args)


class DevelopmentRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_extension_requires_tty_exact_confirmation_and_resumable_state(self):
        with tempfile.TemporaryDirectory() as folder:
            store=RunStore(Path(folder)/"journal.sqlite3")
            store.start("dev",load_config())
            before=store.report("dev")
            args=Namespace(run_id="dev",token_limit=1100000)
            snapshot=Namespace(values={"status":"edited_note"},next=("critic",),tasks=())
            with patch("run_research_workflow.sys.stdin.isatty",return_value=False):
                with self.assertRaisesRegex(WorkflowError,"interactive terminal"):
                    extend_development_budget(args,snapshot,store)
            with patch("run_research_workflow.sys.stdin.isatty",return_value=True),patch("builtins.input",return_value="yes"),patch("sys.stdout",new_callable=io.StringIO):
                extend_development_budget(args,snapshot,store)
                for state in (Namespace(values={},next=(),tasks=()),
                              Namespace(values={"status":"blocked"},next=(),tasks=()),
                              Namespace(values={"status":"awaiting_note"},next=("note_review",),
                                  tasks=(Namespace(interrupts=(Namespace(value={"gate":"note"}),)),))):
                    with self.assertRaises(WorkflowError):
                        extend_development_budget(args,state,store)
            self.assertEqual(store.report("dev"),before)

    async def test_budget_stalled_edit_can_extend_then_resume_to_fresh_human_gate(self):
        from test_research_workflow import FixtureBackend
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); state_dir=root/"workflow"
            backend=FixtureBackend(root); model=FixtureModel(); config=load_config()
            async with open_workflow(state_dir,"dev",REQUEST,config,model,backend) as (w,g,o):
                await g.ainvoke({"request":REQUEST,"researchers":{},"revisions":0,"replans":0},o)
                gate=pending_gate(await g.aget_state(o))
                await g.ainvoke(Command(resume={"action":"approve","expected_hash":gate["expected_hash"],"replacement":None}),o)
                gate=pending_gate(await g.aget_state(o)); old_hash=gate["expected_hash"]
                edited=copy.deepcopy(gate["editable_draft"])
                edited["paragraphs"][0]["text"]="The company discusses competitive pressures."
                used=w.broker.store.report("dev")["budget"]["reserved_tokens"]
                w.broker.store.reserve("dev",tokens=config["token_budget"]-used-1)
                with self.assertRaises(WorkflowError) as caught:
                    await g.ainvoke(Command(resume={"action":"edit","expected_hash":old_hash,"replacement":edited}),o)
                self.assertEqual(caught.exception.code,"budget_exhausted")
                snapshot=await g.aget_state(o)
                self.assertEqual(snapshot.next,("critic",))
                before_state=copy.deepcopy(snapshot.values)
                before_budget=w.broker.store.report("dev")["budget"]
            (state_dir/"dev.request.json").write_text(canonical(REQUEST))
            args=Namespace(run_id="dev",state_dir=state_dir,command="extend-dev-budget",config=None,token_limit=1100000)
            call_count=len(model.calls); tool_count=len(backend.calls)
            # A failed continuation prints its own state/budget diagnostics;
            # the operator no longer needs a second SQLite extraction command.
            continue_args=Namespace(run_id="dev",state_dir=state_dir,command="continue",config=None)
            with patch("run_research_workflow.credentials",return_value={"OPENROUTER_API_KEY":"synthetic"}),patch("run_research_workflow.OpenRouterModel",return_value=model),patch("run_research_workflow.MCPBackend",return_value=backend),patch("sys.stdout",new_callable=io.StringIO) as output:
                with self.assertRaises(WorkflowError) as caught:
                    await run(continue_args)
                self.assertEqual(caught.exception.code,"budget_exhausted")
                diagnostic=json.loads(output.getvalue())["execution_diagnostics"]
                self.assertEqual(diagnostic["next_nodes"],["critic"])
                self.assertEqual(diagnostic["budget"]["reserved_tokens"],before_budget["reserved_tokens"])
                self.assertNotIn("synthetic",output.getvalue())
            self.assertEqual(len(model.calls),call_count)
            # Source replay still consumes tools, even when the model budget
            # blocks its call. Keep this new usage in the recovery assertions.
            before_budget=RunStore(state_dir/"journal.sqlite3").report("dev")["budget"]
            tool_count=len(backend.calls)
            with patch("run_research_workflow.credentials",return_value={}),patch("run_research_workflow.MCPBackend",return_value=backend),patch("run_research_workflow.sys.stdin.isatty",return_value=True),patch("builtins.input",return_value="EXTEND DEVELOPMENT dev 900000 TO 1100000"),patch("sys.stdout",new_callable=io.StringIO):
                await run(args)
            self.assertEqual((len(model.calls),len(backend.calls)),(call_count,tool_count))
            async with open_workflow(state_dir,"dev",REQUEST,config,model,backend) as (w,g,o):
                self.assertEqual((await g.aget_state(o)).values,before_state)
                self.assertEqual(w.broker.store.report("dev")["budget"],{**before_budget,"token_limit":1100000})
                await g.ainvoke(None,o)
                snapshot=await g.aget_state(o)
                self.assertEqual(snapshot.values["revisions"],0)
                self.assertEqual(snapshot.values["status"],"awaiting_note")
                self.assertNotEqual(pending_gate(snapshot)["expected_hash"],old_hash)
                self.assertEqual(backend.portal.list_notes()["total"],0)


class ModelClientTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, body, status=200):
        original=httpx.AsyncClient
        seen=[]
        def handler(request):
            seen.append(request)
            return httpx.Response(status,json=body)
        def client(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            self.assertFalse(kwargs["follow_redirects"])
            return original(transport=httpx.MockTransport(handler),**kwargs)
        with patch("workflow_agents.httpx.AsyncClient",side_effect=client):
            response=await OpenRouterModel("synthetic-key-not-real").complete([{"role":"user","content":"test"}],load_config())
        return response,seen

    def body(self):
        return {"model":"openai/gpt-4.1-mini","choices":[{"finish_reason":"stop","message":{"content":"{}"}}],"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5,"cost":0.0001}}

    async def test_fixed_model_and_endpoint_no_fallback_usage_recorded(self):
        result,seen=await self.request(self.body())
        self.assertEqual(str(seen[0].url),"https://openrouter.ai/api/v1/chat/completions")
        payload=json.loads(seen[0].content)
        self.assertEqual(payload["model"],"openai/gpt-4.1-mini")
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        self.assertEqual(result["usage"]["total_tokens"],5)

    async def test_missing_usage_truncation_or_model_substitution_is_rejected(self):
        for which in ("usage","truncated","model"):
            body=self.body()
            if which=="usage": body.pop("usage")
            elif which=="truncated": body["choices"][0]["finish_reason"]="length"
            else: body["model"]="unapproved/other-model"
            with self.subTest(which=which),self.assertRaises(WorkflowError):
                await self.request(body)

    async def test_provider_error_does_not_echo_body_or_key(self):
        with self.assertRaises(WorkflowError) as caught:
            await self.request({"message":"synthetic-key-not-real"},401)
        self.assertNotIn("synthetic-key",str(caught.exception))


class ProcessResumeTests(unittest.TestCase):
    def test_three_separate_processes_resume_same_checkpoint_and_publish_once(self):
        script='''
import asyncio,json,sys,os
from pathlib import Path
from langgraph.types import Command
from research_workflow import open_workflow
from workflow_agents import load_config
from run_research_workflow import pending_gate
from research_portal import ReviewDecision
from test_research_workflow import REQUEST,FixtureBackend,FixtureModel
async def main():
 folder=Path(sys.argv[1]); phase=int(sys.argv[2]); backend=FixtureBackend(folder)
 async with open_workflow(folder/'workflow','process_test',REQUEST,load_config(),FixtureModel(),backend) as (w,g,o):
  if phase==0: await g.ainvoke({'request':REQUEST,'researchers':{}},o)
  else:
   gate=pending_gate(await g.aget_state(o))
   if phase==2:
    approval=backend.portal.review(gate['proposal_id'],ReviewDecision(expected_hash=gate['expected_hash'],action='approve',reviewer='Synthetic process test'))
    w.approval={'proposal_id':gate['proposal_id'],'content_hash':gate['expected_hash'],'token':approval['approval_token']}
   await g.ainvoke(Command(resume={'action':'approve','expected_hash':gate['expected_hash'],'replacement':None}),o)
  snapshot=await g.aget_state(o)
  print(json.dumps({'pid':os.getpid(),'status':snapshot.values['status'],'published':backend.portal.list_notes()['total']}))
asyncio.run(main())
'''
        with tempfile.TemporaryDirectory() as folder:
            results=[]
            for phase in range(3):
                result=subprocess.run([sys.executable,"-c",script,folder,str(phase)],capture_output=True,text=True,timeout=30,check=True)
                results.append(json.loads(result.stdout.splitlines()[-1]))
            self.assertEqual([r["status"] for r in results],["awaiting_plan","awaiting_note","published"])
            self.assertEqual([r["published"] for r in results],[0,0,1])
            self.assertEqual(len({r["pid"] for r in results}),3)


if __name__=="__main__":
    unittest.main()
