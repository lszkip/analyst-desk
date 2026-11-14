"""Run final memory/UI/evaluation regressions in an isolated disposable copy.

No real credentials, runtime data or paid model calls are permitted. Synthetic
fixture credentials and test databases live only in the private temporary copy.
This known-test guard is not an adversarial Python sandbox or a live evaluation.
Legacy security's 236 checks remain a separate invocation of security/run_checks.py.
"""
# A sibling is named operator.py. Remove this script directory before argparse
# imports enum/operator during direct-file execution without Python's -I flag.
import os
import sys
if __name__ == "__main__":
    _script_directory = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.curdir) != _script_directory]
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).absolute().parents[2]
SELF = "evaluation/final/run_offline_checks.py"
TEST_MODULES = (
    "test_workflow_memory", "test_workflow_lessons", "test_workflow_context", "test_research_app",
    "evaluation.final.test_budget_client", "evaluation.final.test_cases",
    "evaluation.final.test_experiment_runner", "evaluation.final.test_judge_calibration",
    "evaluation.final.test_security_experiment",
    "evaluation.final.test_operator",
    "evaluation.final.test_source_binding",
    "evaluation.final.test_completion_source_binding",
    "evaluation.final.test_memory_comparison",
    "evaluation.final.test_quality_gate",
    "test_workflow_requested_topics",
    "evaluation.final.test_completion_calibration",
    "evaluation.final.test_completion_operator",
    "evaluation.final.test_completion_budget",
    "evaluation.final.test_completion_semantics",
    "evaluation.final.test_paired_security",
    "deployment.test_ci_clean_install",
    # The other demo class starts subprocesses and a loopback HTTP service.
    # Only these six deterministic offline checks belong inside this guard.
    "deployment.test_live_control_demo.DemoOfflineChecks",
)
# Exact source-only inventory: no filesystem globs, .env, credentials, journals,
# private evaluator gold or downloaded SEC/runtime data are copied.
SOURCE_FILES = (
    "company_registry.py", "companies.json", "python_sandbox.py", "sandbox_worker.py",
    "retrieval_data.py", "filing_corpus.py", "filing_passages.py", "filing_supplements.py",
    "download_company_facts.py", "sec_periods.py", "sec_fiscal_years.py",
    "financial_data.py", "financial_contracts.py", "financial_metrics.py",
    "research_portal.py", "research_tools_client.py", "research_workflow.py",
    "review_portal_note.py", "run_research_workflow.py", "tool_security.py",
    "workflow_agents.py", "workflow_contracts.py", "workflow_critic_protocol.py",
    "workflow_financial_narrative.py", "workflow_render.py", "workflow_store.py",
    "workflow_support.py", "workflow_tools.py", "workflow_source_safety.py", "workflow_config.json",
    "workflow_memory.py", "workflow_lessons.py", "test_research_workflow.py",
    "test_workflow_memory.py", "test_workflow_lessons.py", "test_workflow_context.py",
    "research_app.py", "research_ui.html", "start_analyst_desk.py", "compose.final.yaml", "retrieval_gateway.conf",
    "test_research_app.py", "security/run_checks.py",
    "evaluation/final/adapters.py", "evaluation/final/budget_client.py",
    "evaluation/final/build_cases.py", "evaluation/final/experiment_runner.py",
    "evaluation/final/judge_calibration.py", "evaluation/final/security_experiment.py",
    "evaluation/final/human_review_packet.py",
    "evaluation/final/operator.py", "evaluation/final/test_operator.py",
    "evaluation/final/source_binding.py", "evaluation/final/test_source_binding.py",
    "evaluation/final/completion_source_binding.py", "evaluation/final/test_completion_source_binding.py",
    "evaluation/final/memory_comparison.py", "evaluation/final/test_memory_comparison.py",
    "evaluation/final/quality_gate.py", "evaluation/final/test_quality_gate.py",
    "evaluation/final/test_budget_client.py", "evaluation/final/test_cases.py",
    "evaluation/final/test_experiment_runner.py", "evaluation/final/test_judge_calibration.py",
    "evaluation/final/test_security_experiment.py",
    "evaluation/final/configs/e1_architecture.json", "evaluation/final/configs/e3_lessons.json",
    "evaluation/final/configs/e4_critic.json", "evaluation/final/configs/e4_representation.json",
    "evaluation/final/configs/full_matrix.json", "evaluation/final/configs/pilot.json", SELF,
    "test_workflow_requested_topics.py", "test_workflow_research_prefetch.py",
    "evaluation/final/completion_calibration.py", "evaluation/final/test_completion_calibration.py",
    "evaluation/final/completion_operator.py", "evaluation/final/test_completion_operator.py",
    "evaluation/final/completion_runner.py", "evaluation/final/memory_adapter.py",
    "evaluation/final/completion_budget.py", "evaluation/final/test_completion_budget.py",
    "evaluation/final/completion_semantics.py", "evaluation/final/test_completion_semantics.py",
    "evaluation/final/paired_security.py", "evaluation/final/test_paired_security.py",
    "evaluation/final/configs/completion_matrix.json", "evaluation/final/configs/memory_matrix.json",
    "evaluation/final/configs/paired_security.json",
    "evaluation/final/test_fixtures/paired_security_development_cases.json",
    "deployment/ci_clean_install.py", "deployment/test_ci_clean_install.py",
    "deployment/live_control_demo.py", "deployment/test_live_control_demo.py",
    # Fixture-builder imports only. Their full test suites are not selected.
    "main.py", "edgar_client.py", "output_guardrails.py", "request_guardrails.py",
    "test_api.py", "test_filing_corpus.py", "test_filing_passages.py",
    "prepare_filing_corpus.py", "prepare_filing_passages.py", "research_portal_mcp.py",
    "monitoring/exporter.py", "monitoring/tests/fixtures/google_meta_saved_snapshot.json",
)
TIMEOUT_SECONDS = 300
MAX_OUTPUT_BYTES = 2*1024*1024


def helpers(root):
    path = root/"security/run_checks.py"
    if not path.is_file() or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError("missing_or_unsafe_security_helper")
    spec = importlib.util.spec_from_file_location("final_offline_security_helpers",path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_guards(private):
    """Same boundaries as security/run_checks, with local mode=rw/ro URI support.

    Test-authored .env files inside the empty disposable directory are allowed;
    no original .env is copied and every outside source read is denied.
    """
    private=private.resolve()
    denials=Counter()
    runtime=[Path(sys.prefix).resolve(),Path(sys.base_prefix).resolve(),Path('/usr/lib'),
             Path('/usr/local/lib'),Path('/lib'),Path('/usr/share/zoneinfo')]
    system={Path('/etc/localtime'),Path('/etc/mime.types'),Path('/dev/null'),
            Path('/proc/self/maps'),Path('/proc/meminfo'),Path('/proc/cpuinfo')}
    def within(path,root):return path==root or root in path.parents
    def deny(kind):
        denials[kind]+=1
        raise PermissionError("final_offline_guard_"+kind)
    def audit(event,args):
        if event in {'socket.connect','socket.bind','socket.getaddrinfo','socket.gethostbyname',
                     'socket.gethostbyaddr','socket.sendto'}:
            deny('network')
        if event=='subprocess.Popen':
            # Exercise only pinned, non-network CLI paths: the operator's
            # argument parser and the stdlib gate's synthetic negative control.
            # Both inherit the scrubbed environment; no paid command is allowed.
            allowed=(
                [sys.executable,'-B',str(private/'evaluation/final/operator.py'),'--help'],
                [sys.executable,str(private/'evaluation/final/quality_gate.py'),'--negative-control'],
            )
            if args[0]!=sys.executable or list(args[1]) not in allowed:
                deny('subprocess')
        if event=='sqlite3.connect':
            raw=str(args[0])
            if raw!=':memory:':
                if raw.startswith('file:'):
                    parts=urlsplit(raw)
                    if parts.netloc or parts.fragment or parts.query not in {'mode=rw','mode=ro'}:
                        deny('database_uri')
                    raw=unquote(parts.path)
                if not within(Path(raw).resolve(),private):deny('database_path')
        if event!='open' or not isinstance(args[0],(str,bytes,os.PathLike)):
            return
        path=Path(os.fsdecode(args[0])).resolve()
        if within(path,private):return
        flags=args[2] if len(args)>2 and isinstance(args[2],int) else 0
        mode=args[1] if len(args)>1 and isinstance(args[1],str) else ''
        if flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND) or any(c in mode for c in 'wax+'):
            deny('outside_write')
        if path in system or any(within(path,root) for root in runtime):return
        deny('outside_read')
    sys.addaudithook(audit)
    return denials


def worker(root, report_path):
    root=Path(root).absolute()
    security=helpers(root)
    guards=install_guards(root)
    sys.path.insert(0,str(root))
    report={"overall_passed":False,"tests":[],"counts":{}}
    try:
        loader=unittest.TestLoader()
        suite=unittest.TestSuite(loader.loadTestsFromName(name) for name in TEST_MODULES)
        expected=suite.countTestCases()
        result=security.SafeResults()
        suite.run(result)
        rows=sorted(result.rows.values(),key=lambda row:row['id'])
        counts=Counter(row['status'] for row in rows)
        report.update(tests=rows,counts={"discovered":expected,"executed":result.testsRun,
            "passed":counts['passed'],"failed":counts['failed'],"errors":counts['error'],
            "skipped":counts['skipped'],"expected_failures":counts['expected_failure'],
            "unexpected_successes":counts['unexpected_success']},
            subtest_counts=dict(result.subtest_counts),
            module_test_counts={name:sum(r['id'].startswith(name+'.') for r in rows) for name in TEST_MODULES})
        report['overall_passed']=(expected>0 and expected==result.testsRun==len(rows)==counts['passed']
            and all(report['module_test_counts'].values()) and not loader.errors)
        if not report['overall_passed']:report['failure_category']='offline_regressions_failed'
    except BaseException as exc:
        report.update(failure_category='test_process_interrupted' if isinstance(exc,KeyboardInterrupt) else 'test_process_error',exception_type=type(exc).__name__)
    report['guard_denials']=dict(guards)
    security.new_json(Path(report_path),report)
    return 0 if report['overall_passed'] else 1


def run(root):
    security=helpers(root)
    report={"schema_version":1,"scope":"Offline final memory, UI and evaluation implementation checks with synthetic models/backends.",
        "not_measured":["real_model_quality","human_judge_calibration","live_attack_success","deployment_health"],
        "external_model_calls":0,"network_access_permitted":False,"original_runtime_data_read":False,
        "permitted_subprocesses":["Exact pinned operator.py --help", "Exact pinned stdlib quality_gate.py --negative-control"],
        "real_credentials_forwarded":False,"started_utc":security.now(),"overall_passed":False,
        "test_modules":list(TEST_MODULES),"counts":{}}
    try:
        if len(SOURCE_FILES)!=len(set(SOURCE_FILES)):raise ValueError('duplicate_source_manifest_entry')
        sources={name:security.read_source(root,name) for name in SOURCE_FILES}
        before={name:security.sha(raw) for name,raw in sources.items()}
        report['source_sha256']=before
        with tempfile.TemporaryDirectory(prefix='analyst-desk-final-offline-') as temporary:
            private=Path(temporary);private.chmod(0o700)
            for name,raw in sources.items():
                destination=private/name;destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_bytes(raw)
            worker_report=private/'worker_report.json'
            command=[sys.executable,'-I','-B',str(private/SELF),'--_worker',str(private),str(worker_report)]
            with tempfile.TemporaryFile() as output:
                child=subprocess.Popen(command,cwd=private,env=security.clean_environment(private),
                    stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
                start=time.monotonic();category=None
                try:
                    while child.poll() is None:
                        if time.monotonic()-start>TIMEOUT_SECONDS:category='test_timeout';break
                        if output.tell()>MAX_OUTPUT_BYTES:category='test_output_limit';break
                        time.sleep(.1)
                finally:
                    if child.poll() is None:
                        os.killpg(child.pid,signal.SIGTERM)
                        try:child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=5)
                output.seek(0);raw=output.read(MAX_OUTPUT_BYTES)
                report['worker']={"exit_code":child.returncode,"seconds":round(time.monotonic()-start,3),
                    "output_bytes":len(raw),"output_sha256":security.sha(raw)}
            if category:report['failure_category']=category
            elif worker_report.is_file() and worker_report.stat().st_size<=MAX_OUTPUT_BYTES:
                inner=json.loads(worker_report.read_text())
                report.update({key:inner[key] for key in ('tests','counts','subtest_counts','module_test_counts',
                    'guard_denials','failure_category','exception_type') if key in inner})
                report['overall_passed']=child.returncode==0 and inner.get('overall_passed') is True
            else:report['failure_category']='test_report_unavailable'
        report['source_unchanged']=all(security.sha(security.read_source(root,name))==value for name,value in before.items())
        if not report['source_unchanged']:report['overall_passed']=False
    except BaseException as exc:
        report.update(overall_passed=False,failure_category='test_interrupted' if isinstance(exc,KeyboardInterrupt) else 'test_preflight_error',exception_type=type(exc).__name__)
    report['finished_utc']=security.now()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path)
    parser.add_argument('--_worker',nargs=2,help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args._worker:return worker(*args._worker)
    security=helpers(ROOT)
    path=args.report.absolute() if args.report else None
    if path and (not security.safe_path(path) or not path.parent.is_dir() or os.path.lexists(path)):
        parser.error('--report requires a new file in an existing safe directory')
    result=run(ROOT)
    if path:security.new_json(path,result)
    print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))
    return 0 if result['overall_passed'] else 1


if __name__=='__main__':
    with helpers(ROOT).termination_as_interrupt():
        raise SystemExit(main())
