"""Operator entry point: six development pilot runs, optionally AI/AI agreement.

Run with --pilot --repo /existing/analyst-desk --output /private/pilot-output.
No held-out experiment, publication, automatic human label or lesson approval.
"""
from __future__ import annotations
# This filename matches the stdlib operator module. Remove the script directory
# before argparse/enum import it during direct-file execution.
import sys
import os
if __name__ == "__main__":
    _script_directory = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.curdir) != _script_directory]
    sys.path.insert(0, os.path.dirname(os.path.dirname(_script_directory)))
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import uuid
import zipfile

# Direct execution works from a staged package without installing that package
# into the original repository. --repo selects existing data and .env only.
RUNTIME_ROOT = Path(__file__).resolve().parents[2]
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from workflow_contracts import WorkflowError, canonical, digest
from company_registry import filing_url
from workflow_tools import MCPBackend
from evaluation.final import build_cases
from evaluation.final.source_binding import bind_sources
from evaluation.final.budget_client import (BudgetClient, BudgetLedger, DEFAULT_LIMITS,
    TASK_MODEL, JUDGE_MODEL, approve_budget, fetch_current_pricing)
from evaluation.final.experiment_runner import (atomic_json, forecast, load_experiment,
    read_jsonl, run_experiment, schedule)

BUNDLE_SHA256 = 'af8fa2144ec0a99783dbb54ced1e29b42ae2c5ca2064a951be97d65fac410206'
PILOT_IDS = ('F002', 'F003')
PUBLIC_REPORT_NAMES = ('Operator_Status.json','Operator_Audit.json','Pilot_Plan.json',
    'Preflight_Report.json','Cost_Forecast.json','Budget_Report.json','AI_Judge_Rows.json',
    'AI_Model_Agreement.json','Memory_On_Off_Report.json','Pilot_Results.zip')


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def private_directory(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Evaluation directories cannot contain symlinks')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def private_json(path, value):
    atomic_json(path, value)
    Path(path).chmod(0o600)


def load_credentials(repo, environ=None):
    """Read only the two needed credentials; no interpolation or portal key."""
    environ = os.environ if environ is None else environ
    names = ('OPENROUTER_API_KEY', 'SANDBOX_API_TOKEN')
    values = {name: environ[name] for name in names if environ.get(name)}
    env_path = Path(repo)/'.env'
    if env_path.is_file():
        if env_path.stat().st_size > 1024*1024:
            raise ValueError('The existing .env is unexpectedly large')
        from dotenv import dotenv_values
        lines = [line for line in env_path.read_text().splitlines()
                 if re.match(r'^\s*(?:export\s+)?(?:OPENROUTER_API_KEY|SANDBOX_API_TOKEN)\s*=', line)]
        parsed = dotenv_values(stream=io.StringIO('\n'.join(lines)), interpolate=False)
        for name in names:
            if name not in values and parsed.get(name):
                values[name] = parsed[name]
    for name in names:
        if not isinstance(values.get(name), str) or not values[name].strip():
            raise WorkflowError('missing_credential', 'The existing environment/.env is missing '+name+'.')
    return values


def find_bundle(output, explicit=None, evaluator_private=None):
    candidates = ([Path(explicit)] if explicit else []) + [
        Path(evaluator_private or Path(output)/'_evaluator_private')/'Operator_Case_Bundle.json',
        RUNTIME_ROOT.parent/'evaluator_private'/'Operator_Case_Bundle.json']
    for path in candidates:
        if path.is_file():
            return path
    raise WorkflowError('case_bundle_missing', 'The frozen Operator_Case_Bundle.json was not supplied. No paid call was made.')


def verify_bundle(path, *, expected_hash=BUNDLE_SHA256, authoring_path=None):
    path = Path(path)
    if path.stat().st_size > 5*1024*1024:
        raise ValueError('Frozen case bundle is oversized')
    bundle = json.loads(path.read_text())
    files = bundle.get('files', {})
    if bundle.get('schema_version') != 'final-operator-case-bundle-v1' or digest(files) != expected_hash or bundle.get('bundle_sha256') != expected_hash:
        raise ValueError('Frozen case bundle fingerprint changed')
    manifest = json.loads(files['freeze_manifest.json'])
    if hashlib.sha256(files['freeze_manifest.json'].encode()).hexdigest() != bundle['original_freeze_manifest_sha256']:
        raise ValueError('Original freeze manifest changed')
    for name, value in files.items():
        if Path(name).name != name or not isinstance(value, str):
            raise ValueError('Invalid frozen artifact name')
        if name != 'freeze_manifest.json' and hashlib.sha256(value.encode()).hexdigest() != manifest['artifact_hashes'].get(name):
            raise ValueError('Frozen artifact changed: '+name)
    authoring_path = Path(authoring_path or build_cases.__file__)
    if sha_file(authoring_path) != manifest['authoring_code_sha256']:
        raise ValueError('The numerical evaluator differs from the frozen authoring code')
    cases = [json.loads(line) for line in files['cases.dev.jsonl'].splitlines() if line.strip()]
    cases = [row for row in cases if row['case_id'] in PILOT_IDS]
    if {row['case_id'] for row in cases} != set(PILOT_IDS) or any(row['split'] != 'dev' for row in cases):
        raise ValueError('Pilot must contain exactly the two frozen development cases')
    gold = [json.loads(line) for line in files['gold.private.jsonl'].splitlines() if line.strip()]
    gold = [row for row in gold if row['case_id'] in PILOT_IDS]
    if {row['case_id'] for row in gold} != set(PILOT_IDS):
        raise ValueError('Pilot private targets are incomplete')
    return bundle, manifest, cases, gold


def verify_local_sources(repo, manifest, cases):
    data = Path(repo)/'data'/'raw'
    tickers = sorted({t for case in cases for t in case['request']['tickers']})
    checked = {}
    required = {ticker+suffix for ticker in tickers for suffix in ('_companyfacts.json','_filings.json')}
    for ticker in tickers:
        path = data/(ticker+'_filings.json')
        if path.is_file():
            history = json.loads(path.read_text())
            for filing in history.get('annual_filings', []):
                evidence = filing.get('fiscal_year_evidence')
                if evidence:
                    value = evidence.get('source_sha256','')
                    if not re.fullmatch(r'[0-9a-f]{64}',value):
                        raise ValueError('Invalid cached original-filing fingerprint')
                    required.add('sec_fiscal_years/'+value+'.html')
    for relative in sorted(required):
        expected = manifest['source_hashes'].get('sec_source_snapshot/'+relative)
        path = data/relative
        if not expected or not path.is_file() or sha_file(path) != expected:
            raise WorkflowError('frozen_source_mismatch', 'Existing SEC cache does not match frozen pilot inputs: data/raw/'+relative+'. Nothing was downloaded or overwritten.')
        checked['data/raw/'+relative] = expected
    return {'status':'verified','companies':tickers,'source_hashes':checked,
            'scope':'Only sources required by the two development pilot cases; no claim that all held-out source caches were checked.'}


def _assert_ok(result, operation):
    if not isinstance(result, dict) or result.get('status') == 'error':
        raise WorkflowError('pilot_preflight_failed', operation+' did not return usable local evidence. No paid call was made.')
    return result


async def preflight_backend(backend, cases, gold):
    """Read actual MCP tools and check numerical identities before any model call."""
    coverage = _assert_ok(await backend.call('kb','get_coverage',{}),'Filings KB coverage')
    if not coverage.get('generation') or not coverage.get('models_sha256'):
        raise WorkflowError('pilot_preflight_failed','The production retrieval generation/model fingerprints are missing.')
    targets = {(row['ticker'],row['fiscal_year']):row for item in gold for row in item['numeric_targets']}
    facts_checked, sources_checked = [], []
    slots = {(t,y,item) for case in cases for t in case['request']['tickers'] for y in case['request']['years']
             for item in ['7', *(['1A'] if case['request']['include_risks'] else [])]}
    for ticker,year in sorted(targets):
        target=targets[(ticker,year)]
        for metric in ('revenue','operating_income'):
            actual=await backend.call('edgar','get_annual_financial_fact',{'ticker':ticker,'metric':metric,'year':year,'as_of':None})
            expected=target[metric]
            if expected['status']=='ok':
                wanted={'status':'ok','ticker':ticker,'fiscal_year':year,'metric':metric,
                    'value':int(expected['value']),'unit':'USD','concept':expected['concept'],
                    'period_start':target['period_start'],'period_end':target['period_end'],
                    'filed':expected['filed'],'filing_identifiers':expected['filing_identifiers'],
                    'snapshot_sha256':expected['snapshot_sha256'],'filings_snapshot_sha256':expected['filings_snapshot_sha256']}
                if any(actual.get(key)!=value for key,value in wanted.items()):
                    raise WorkflowError('pilot_preflight_failed',f'EDGAR {ticker} FY{year} {metric} does not match the frozen source facts.')
                sources=actual.get('sources',[])
                if (expected['source_url']!=filing_url(ticker,expected['filing_identifiers'][0])
                        or len(sources)!=len(expected['filing_identifiers'])
                        or {s.get('filing_identifier') for s in sources}!=set(expected['filing_identifiers'])
                        or any(s.get('url')!=filing_url(ticker,s['filing_identifier'])
                               or s.get('concept')!=expected['concept'] or s.get('value')!=int(expected['value'])
                               or s.get('filed')!=expected['filed'] for s in sources)):
                    raise WorkflowError('pilot_preflight_failed',f'EDGAR {ticker} FY{year} {metric} source filing identities or URLs do not match the frozen evidence.')
            elif actual.get('status')=='ok':
                raise WorkflowError('pilot_preflight_failed','EDGAR substituted an unavailable GAAP input.')
            facts_checked.append({'ticker':ticker,'year':year,'metric':metric,'response_sha256':digest(actual)})
    for ticker,year,item in sorted(slots):
        section=_assert_ok(await backend.call('edgar','get_section',{'ticker':ticker,'year':year,'item':item,'offset':0,'limit':1}),'EDGAR section')
        passages=section.get('passages',[])
        if not passages:
            raise WorkflowError('pilot_preflight_failed',f'Required source section has no registered passage: {ticker} FY{year} Item {item}.')
        first=passages[0]
        reread=_assert_ok(await backend.call('kb','get_passage',{'passage_id':first['passage_id']}),'Filings KB passage')
        if reread.get('passage')!=first:
            raise WorkflowError('pilot_preflight_failed','EDGAR and KB do not expose the same original source passage.')
        found=_assert_ok(await backend.call('kb','search_filings',{'request':{'query':'operating performance risk factors',
            'tickers':[ticker],'years':[year],'items':[item],'kind':'all','limit':1,'decompose':False}}),'Production hybrid retrieval')
        if not found.get('hits'):
            raise WorkflowError('pilot_preflight_failed','Production hybrid search returned no hits for an available pilot source slot.')
        sources_checked.append({'ticker':ticker,'year':year,'item':item,'passage_id':first['passage_id'],
            'source_sha256':first['source_sha256'],'search_sha256':digest(found)})
    calculated=_assert_ok(await backend.call('sandbox','execute_python',{
        'code':'result = (Decimal(inputs["income"]) / Decimal(inputs["revenue"]) * 100).quantize(Decimal("0.01"))',
        'inputs':{'income':'25','revenue':'100'}}),'Isolated calculation')
    if calculated.get('result')!='25.00' or not all(calculated.get('kernel_probes',{}).get(k) is True for k in ('network_denied','file_open_denied','process_creation_denied')):
        raise WorkflowError('pilot_preflight_failed','Sandbox arithmetic or isolation probes failed.')
    return {'status':'passed','retrieval_generation':coverage['generation'],'retrieval_models_sha256':coverage['models_sha256'],
            'facts_checked':facts_checked,'source_slots_checked':sources_checked,'sandbox_verified':True,'model_calls':0,
            'preflight_operations_excluded_from_task_tool_counts':True}


def claim_run(repo, output, identity):
    """A new output name cannot silently replenish an existing batch allowance."""
    repo, output = Path(repo), Path(output)
    registry_dir = repo/'.analyst-desk-evaluation'
    private_directory(registry_dir)
    registry_path=registry_dir/'final34_pilot.json'
    run_path=output/'private'/'Run_Identity.json'
    if registry_path.exists():
        registered=json.loads(registry_path.read_text())
        if registered['batch_id']!=identity['batch_id']:
            raise WorkflowError('pilot_identity_changed','The repository already has a different frozen final pilot identity.')
        if not run_path.exists():
            raise WorkflowError('pilot_existing_run','A pilot already exists for this repository. Reuse its complete output folder: '+registered['output_directory'])
        stored=json.loads(run_path.read_text())
        if stored.get('run_uuid')!=registered.get('run_uuid') or stored.get('batch_id')!=identity['batch_id']:
            raise WorkflowError('pilot_identity_changed','This output is not the registered pilot; no allowance was reset.')
        if registered.get('budget_initialized') and not (output/'private'/'Budget.sqlite3').is_file():
            raise WorkflowError('live_ledger_identity','The registered cost ledger is missing; no allowance was reset.')
        return registry_path, registered
    if run_path.exists():
        raise WorkflowError('pilot_identity_changed','An output identity exists without its repository registry; reconcile it before paid calls.')
    value={**identity,'run_uuid':uuid.uuid4().hex,'output_directory':str(output),'budget_initialized':False}
    private_json(run_path,value)
    # Exclusive registry creation prevents concurrent launchers choosing two ledgers.
    try:
        fd=os.open(registry_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        raise WorkflowError('pilot_concurrent_launch','Another pilot launcher claimed this repository; no paid call was made.') from None
    with os.fdopen(fd,'w') as stream:
        stream.write(canonical(value)+'\n')
    return registry_path,value


def maybe_memory_report(evaluator_private, output):
    binding=Path(evaluator_private)/'25_Publication_Approval_Binding.json'
    receipt=Path(evaluator_private)/'25_Publication_Receipt.json'
    if not binding.is_file() or not receipt.is_file():
        return {'status':'not_run','reason':'optional_archived_publication_inputs_absent','model_calls':0}
    from evaluation.final.memory_comparison import report_from_files
    report=report_from_files(binding,receipt,workspace=Path(output)/'private')
    atomic_json(Path(output)/'Memory_On_Off_Report.json',report)
    return {'status':'completed','model_calls':0,'network_calls':0,'human_review_performed':False,
            'scope':'deterministic historical memory comparison; no live model quality claim'}


def find_ai_packet(review_path, output, evaluator_private=None):
    review=json.loads(Path(review_path).read_text())
    candidates=[Path(evaluator_private or Path(output)/'_evaluator_private')/'Claude_Review_Packet.json']
    for folder in (Path(review_path).parent,Path(review_path).parent.parent/'package'):
        candidates.extend(sorted(folder.glob('*Cases_and_Lessons.json')))
    from evaluation.final.judge_calibration import validate_ai_reference
    for path in candidates:
        if path.is_file():
            packet=json.loads(path.read_text())
            if packet.get('packet_sha256')==review.get('packet_sha256'):
                validate_ai_reference(packet,review)
                return packet,review
    raise WorkflowError('ai_reference_packet_missing','The matching hash-bound Claude case packet is required; no AI reference was treated as human review.')


async def run_ai_comparison(packet,reference,key,ledger,output):
    from evaluation.final.judge_calibration import judge_item, compare_ai_reference, validate_packet
    items=validate_packet(packet)
    rows=[]
    folder=private_directory(Path(output)/'ai_judge')
    for item_id,item in items.items():
        if not re.fullmatch(r'[A-Za-z0-9_]+',item_id):
            raise ValueError('Unsafe judge item ID')
        path=folder/(item_id+'.json')
        case_id='FINAL34_JUDGE_'+item_id
        if path.exists():
            row=json.loads(path.read_text())
        else:
            stats=ledger.stats()
            if stats.get('poisoned'):
                break
            try:
                if not ledger.begin_case(case_id):
                    raise WorkflowError('live_interrupted_case','Completed judge attempt has no saved row; it will not be repeated.')
                row=await judge_item(item,BudgetClient(key,ledger,case_id,purpose='AI_model_comparison'))
                private_json(path,row)
                ledger.finish_case(case_id,row['status'])
            except WorkflowError as exc:
                row={k:item[k] for k in ('item_id','question_sha256','source_sha256','output_sha256')}
                row.update(model=JUDGE_MODEL,task_model=TASK_MODEL,status='error',label=None,error_code=exc.code,human_reviewed=False)
                private_json(path,row)
        rows.append(row)
        atomic_json(Path(output)/'AI_Judge_Rows.json',rows)
        if row.get('error_code') in {'live_budget_exhausted','live_time_limit','live_accounting_error','model_http_error','model_unavailable','live_response_oversized'}:
            break
    report=compare_ai_reference(packet,reference,rows)
    atomic_json(Path(output)/'AI_Model_Agreement.json',report)
    return report


def is_public_report(relative):
    parts=Path(relative).parts
    if len(parts)==1:
        return parts[0] in PUBLIC_REPORT_NAMES and parts[0]!='Pilot_Results.zip'
    if len(parts)==2 and parts[0]=='pilot':
        return parts[1] in {'Experiment_Manifest.json','Progress.json','Execution_Results.json','Experiment_Summary.json'}
    if len(parts)==4 and parts[:2]==('pilot','executions'):
        return bool(re.fullmatch(r'[A-Za-z0-9_]+',parts[2])) and parts[3] in {'Result.json','Evidence.json','Journal_Report.json','Model_Responses.json'}
    if len(parts)==2 and parts[0]=='ai_judge':
        return bool(re.fullmatch(r'[A-Za-z0-9_]+\.json',parts[1]))
    return False


def package_reports(output):
    output=Path(output)
    archive=output/'Pilot_Results.zip'
    temporary=output/'Pilot_Results.zip.partial'
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(output.rglob('*')):
            if not path.is_file() or path.is_symlink() or path in {archive,temporary}:
                continue
            relative=path.relative_to(output)
            if not is_public_report(relative):
                continue
            z.write(path,str(relative))
    temporary.replace(archive)
    return archive


async def operate(args, *, input_fn=input, print_fn=print):
    output=private_directory(args.output)
    private_directory(output/'private')
    evaluator_private=private_directory(getattr(args,'evaluator_private',None) or output/'_evaluator_private')
    audit={'schema_version':1,'mode':'development_pilot','started_at':datetime.now(timezone.utc).isoformat(),
        'human_reviewed':False,'satisfies_human_review_requirement':False,'heldout_started':False,'publication_enabled':False,
        'isolation':'No agent filesystem or arbitrary-code tool; private targets remain evaluator-side. This is not an OS/container isolation claim.'}
    ledger=None
    keys={}
    try:
        repo=Path(args.repo).resolve()
        if not repo.is_dir():
            raise ValueError('--repo must identify the existing Analyst Desk repository')
        bundle,manifest,cases,gold=verify_bundle(find_bundle(output,getattr(args,'case_bundle',None),evaluator_private))
        config=load_experiment(Path(__file__).with_name('configs')/'pilot.json')
        config['case_limit']=2
        audit.update(case_ids=[c['case_id'] for c in cases],case_bundle_sha256=bundle['bundle_sha256'],freeze_manifest_sha256=bundle['original_freeze_manifest_sha256'])
        batch_id='FINAL34_PILOT_'+digest([bundle['bundle_sha256'],config])[:20]
        registry_path,registered=claim_run(repo,output,{'batch_id':batch_id,'case_bundle_sha256':bundle['bundle_sha256']})
        # The only extracted request/target files are the selected development rows.
        private_json(evaluator_private/'Pilot_Cases.json',cases)
        private_json(evaluator_private/'Pilot_Gold.json',gold)
        audit['memory_comparison']=maybe_memory_report(evaluator_private,output)
        bound_gold,source_report=bind_sources(repo,manifest,cases,gold,
            evaluator_private/'Verified_Source_Equivalence.json',output/'private'/'Source_Binding.json',
            frozen_bundle_sha256=bundle['bundle_sha256'],
            original_freeze_manifest_sha256=bundle['original_freeze_manifest_sha256'],
            expected_binding_sha256=registered.get('source_binding_sha256'),
            require_existing=bool(registered.get('budget_initialized') or (output/'private'/'Budget.sqlite3').exists()
                                  or (output/'pilot'/'Experiment_Manifest.json').exists()))
        registered['source_binding_sha256']=source_report['binding_sha256']
        private_json(registry_path,registered)
        private_json(evaluator_private/'Bound_Pilot_Gold.json',bound_gold)
        audit['source_binding']={key:source_report[key] for key in ('binding_sha256','original_gold_sha256',
            'bound_gold_sha256','expected_answers_unchanged','certificate_sha256')}
        keys=load_credentials(repo)
        backend=MCPBackend({'SANDBOX_API_TOKEN':keys['SANDBOX_API_TOKEN']})
        preflight=await preflight_backend(backend,cases,bound_gold)
        preflight['local_source_check']=source_report
        atomic_json(output/'Preflight_Report.json',preflight)
        optional_ai=find_ai_packet(args.claude_review,output,evaluator_private) if getattr(args,'claude_review',None) else None
        atomic_json(output/'Pilot_Plan.json',{'case_ids':audit['case_ids'],'jobs':schedule(cases,config),'task_model':TASK_MODEL,
            'judge_model':JUDGE_MODEL,'optional_ai_judge_items':len(optional_ai[0]['items']) if optional_ai else 0,
            'heldout_runs':0,'human_review_required_remains_unmet':True})
        saved_path=output/'private'/'Budget_Approval.json'
        if saved_path.exists():
            saved=json.loads(saved_path.read_text())
            pricing,limits,approval=saved['pricing'],saved['limits'],saved['approval']
            if limits!=DEFAULT_LIMITS:
                raise WorkflowError('live_ledger_identity','The saved pilot limits differ; no allowance was reset.')
            print_fn('Resuming the existing approved pilot allowance; completed attempts will not be repeated.')
        else:
            if registered.get('budget_initialized'):
                raise WorkflowError('live_ledger_identity','The saved approval is missing; no allowance was reset.')
            pricing=await fetch_current_pricing()
            limits=dict(DEFAULT_LIMITS)
            cost_forecast=forecast(config,cases,pricing)
            cost_forecast.update(shared_approved_cap_usd=limits['max_reserved_usd'],shared_max_requests=limits['max_calls'],shared_runtime_seconds=limits['max_runtime_seconds'],
                optional_ai_judge_items=len(optional_ai[0]['items']) if optional_ai else 0)
            atomic_json(output/'Cost_Forecast.json',cost_forecast)
            print_fn('Preflight passed. Six DEVELOPMENT runs: F002/F003 × C0/C1/C2. No held-out runs or publication.')
            print_fn('Task model: '+TASK_MODEL+'; independent judge: '+JUDGE_MODEL+'.')
            print_fn('Verified current token prices (USD per million tokens) and enforced request-price ceilings: '+canonical(pricing['models']))
            print_fn('Every model request enforces a $0 flat-request fee ceiling; an omitted catalogue fee is not advertised as verified zero.')
            print_fn('Shared cumulative limits: $10 maximum reserved allowance, 3 hours, 3000 requests. Failed calls count. The pilot may stop earlier.')
            print_fn('Conservative uncapped pilot upper bound: $'+str(cost_forecast['uncapped_queue_conservative_usd_upper_bound'])+'.')
            if optional_ai:
                print_fn('After the pilot: up to 24 independent judge calls under this SAME allowance; Claude comparison is AI-only, with no human-review credit.')
            answer=input_fn('Enter APPROVE to start, or press Enter to pause: ')
            if answer.strip()!='APPROVE':
                audit.update(status='paused',reason='operator_did_not_approve',model_calls=0)
                return audit
            approval=approve_budget(answer,pricing,limits)
            private_json(saved_path,{'pricing':pricing,'limits':limits,'approval':approval})
        registered.update(budget_initialized=True,output_directory=str(output))
        private_json(registry_path,registered)
        ledger=BudgetLedger(output/'private'/'Budget.sqlite3',batch_id,pricing,limits,approval)
        summary=await run_experiment(cases,config,output/'pilot',ledger=ledger,
            model_factory=lambda run_id,cfg:BudgetClient(keys['OPENROUTER_API_KEY'],ledger,run_id,purpose='development_pilot'),
            backend_factory=lambda variant:backend,evaluate=build_cases.evaluate_case,private_gold=bound_gold,
            on_progress=lambda row:print_fn(row['execution_id']+': '+row['status']))
        audit.update(status='completed',pilot_summary=summary)
        if optional_ai and not ledger.stats().get('poisoned'):
            audit['ai_comparison']=await run_ai_comparison(*optional_ai,keys['OPENROUTER_API_KEY'],ledger,output)
        return audit
    except (KeyboardInterrupt,asyncio.CancelledError):
        audit.update(status='interrupted',reason='operator_interrupt_no_automatic_retry')
        return audit
    except Exception as exc:
        message=str(exc)
        for secret in keys.values():
            if secret:
                message=message.replace(secret,'[redacted]')
        audit.update(status='failed',error_code=getattr(exc,'code',type(exc).__name__),error_message=message[:1500])
        return audit
    finally:
        if ledger is not None:
            try:
                atomic_json(output/'Budget_Report.json',ledger.stats())
            except Exception as exc:
                audit['budget_report_error']=getattr(exc,'code',type(exc).__name__)
        audit['finished_at']=datetime.now(timezone.utc).isoformat()
        atomic_json(output/'Operator_Audit.json',audit)
        atomic_json(output/'Operator_Status.json',{k:v for k,v in audit.items() if k not in {'pilot_summary','ai_comparison'}})
        archive=package_reports(output)
        print_fn('Results: '+str(archive))
        print_fn('Status: '+audit.get('status','unknown')+'.')
        if audit.get('status')=='failed':
            print_fn('Error ['+str(audit.get('error_code','unknown'))+']: '+audit.get('error_message','The operator did not complete.'))
        print_fn('Independent human review remains incomplete.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot',action='store_true',required=True)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--claude-review',type=Path)
    parser.add_argument('--evaluator-private',type=Path)
    parser.add_argument('--case-bundle',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    result=asyncio.run(operate(args))
    return 0 if result.get('status') in {'completed','paused'} else 1


if __name__=='__main__':
    raise SystemExit(main())
