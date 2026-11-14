"""Explicitly approved continuation of the existing final-evaluation ledger.

Install beside evaluation.final.operator. Preview is local and makes no model
calls. Execution requires reviewed pilot evidence, immutable source/config
bindings, separate scope APPROVE, and the original cumulative ledger. Results
remain evaluation-only; no publication or automatic human-label claims.
"""
import argparse
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys

# Direct isolated execution (-I) deliberately omits the script directory.
# Only the trusted installed repository root is added for project imports.
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

from workflow_contracts import WorkflowError, ResearchRequest, digest, canonical
from workflow_lessons import LessonStore
from workflow_tools import MCPBackend
from evaluation.final import build_cases
from evaluation.final.budget_client import BudgetClient, BudgetLedger, DEFAULT_LIMITS, TASK_MODEL
from evaluation.final.operator import (verify_bundle, load_credentials, preflight_backend,
    verify_local_sources, private_directory, private_json)
from evaluation.final.source_binding import bind_sources, _answer_projection
from evaluation.final.completion_source_binding import bind_completion_sources
from evaluation.final.experiment_runner import (load_experiment, select_cases,
    forecast, make_lesson_context_factory, atomic_json, summarize)
from evaluation.final.completion_runner import run_experiment, schedule, SCHEDULING, verify_case_local_budget_failure
from evaluation.final.experiment_runner import workflow_config_for
from evaluation.final.memory_adapter import make_memory_executor

VERSION = 'completion-scope-v1'
PROPOSED_LIMITS = {**DEFAULT_LIMITS, 'max_reserved_usd':'150.00', 'max_calls':12000,
                   'max_runtime_seconds':16200}
PILOT_IDS = {'F002','F003'}
HASH = re.compile(r'[a-f0-9]{64}\Z')
HUMAN_PACKET_RAW_SHA256 = '78917c90e41afaf1771ac0ebd402ac08a3d2e850fb180931e4a3a26a409149dd'


def fail(code, message):
    raise WorkflowError(code,message)


def safe_bytes(path, maximum=128*1024*1024):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file() or path.stat().st_size>maximum:
        fail('completion_input','A required input is missing, unsafe or oversized: '+path.name)
    return path.read_bytes()


def read_json(path, maximum=5*1024*1024):
    return json.loads(safe_bytes(path,maximum))


def sha_file(path):
    return hashlib.sha256(safe_bytes(path)).hexdigest()


def exact_file(path, value):
    """Persist a new immutable identity; a missing approval never resets spend."""
    path=Path(path)
    if path.exists():
        if read_json(path)!=value:fail('completion_identity','Frozen completion scope changed; prior results were preserved.')
    else:
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'w') as stream:
            stream.write(json.dumps(value,indent=2,allow_nan=False)+'\n');stream.flush();os.fsync(stream.fileno())


def persist_scope_with_preview_handoff(output, scope, budget_preview):
    """Replace only an unapproved preview, keeping its exact archived bytes.

    Caller holds completion_lock and supplies preview_budget_amendment output,
    which has verified the original ledger and absence of an incompatible amendment.
    """
    output=Path(output);path=output/'Completion_Scope.json'
    if not path.exists():
        exact_file(path,scope);return
    prior=read_json(path)
    if prior==scope:return
    stats=budget_preview.get('current_stats',{})
    original_cases={f'PILOT_{arm}_{case}_r1' for arm in ('C0','C1','C2') for case in ('F002','F003')}
    original_cases|={f'FINAL34_JUDGE_H{i:02d}' for i in range(1,25)}
    forbidden=['private/Scope_Approval.json','private/Budget_Amendment.json',
        'development_sanity','development_coverage','matrix','memory','security','semantics','calibration']
    if (budget_preview.get('status')!='awaiting_explicit_approval' or budget_preview.get('receipt') is not None
            or budget_preview.get('plan_sha256')!=scope['scope_sha256']
            or any((output/name).exists() or (output/name).is_symlink() for name in forbidden)
            or not isinstance(stats.get('case_states'),dict)
            or set(stats['case_states'])-original_cases
            or any(value in {'in_progress','reserved'} for value in stats['case_states'].values())
            or stats.get('poisoned') or stats.get('requests_without_actual_cost') or stats.get('in_flight_requests')):
        fail('completion_identity','A prior approved scope, amendment or attempted completion cannot be replaced. Existing results and allowance were preserved.')
    for value in (prior,scope):
        if value.get('scope_sha256')!=digest({k:v for k,v in value.items() if k!='scope_sha256'}):
            fail('completion_identity','An existing preview scope failed its identity check.')
    # This handoff may change ordering and reviewed software, never task scope,
    # cases, variants, model settings, labels, private evidence or budget limits.
    def invariant(value):
        result=deepcopy(value)
        for key in ('scope_sha256','pilot_review','phase_order'):result.pop(key,None)
        for key in ('matrix','sanity_config','development_coverage_config'):
            result[key].pop('scheduling',None);result[key].pop('interpretation',None)
        return result
    if invariant(prior)!=invariant(scope):
        fail('completion_identity','The preview handoff would change more than prespecified ordering and reviewed software.')
    archive=output/'private'/'preview_archive'/prior['scope_sha256']
    if any(p.is_symlink() for p in (archive,*archive.parents)):
        fail('completion_identity','The preview archive path is unsafe.')
    archived={}
    for name in ('Completion_Scope.json','Completion_Plan.json','Completion_Preflight.json'):
        source=output/name
        if source.exists():
            raw=safe_bytes(source,16*1024*1024);destination=archive/name
            destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            if destination.exists():
                if safe_bytes(destination,16*1024*1024)!=raw:fail('completion_identity','Archived preview bytes changed.')
            else:
                fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
                with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            archived[name]=hashlib.sha256(raw).hexdigest()
    pending=output/('Completion_Scope.pending_'+scope['scope_sha256'])
    exact_file(pending,scope);os.replace(pending,path)
    atomic_json(output/'Preview_Handoff.json',{'schema_version':'unapproved-preview-handoff-v1',
        'prior_scope_sha256':prior['scope_sha256'],'new_scope_sha256':scope['scope_sha256'],
        'archived_reports_sha256':archived,'budget_snapshot_sha256':digest(stats),
        'prior_reserved_requests':stats.get('reserved_requests'),'original_started_at':stats.get('original_started_at'),
        'approved_scope_replaced':False,'budget_or_start_reset':False,'new_model_calls':0,
        'reason':'Unapproved preview reordered before any completion attempt; full cases, arms, repeats and budget unchanged.'})


@contextmanager
def completion_lock(state):
    path=Path(state)/'private'/'Completion.lock'
    if path.is_symlink():fail('completion_lock','Unsafe completion lock.')
    fd=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:fail('completion_concurrent','Another completion operator is active.')
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


def existing_run(repo, state):
    """Require the existing repository anchor, identity, approval and database."""
    repo,state=Path(repo).absolute(),Path(state).absolute()
    registry=read_json(repo/'.analyst-desk-evaluation'/'final34_pilot.json')
    identity=read_json(state/'private'/'Run_Identity.json')
    saved=read_json(state/'private'/'Budget_Approval.json')
    if (registry.get('budget_initialized') is not True or registry.get('batch_id')!=identity.get('batch_id')
        or registry.get('run_uuid')!=identity.get('run_uuid') or registry.get('output_directory')!=str(state)):
        fail('completion_identity','Reuse the exact registered pilot state; a new allowance cannot be created.')
    if not safe_bytes(state/'private'/'Budget.sqlite3'):
        fail('live_ledger_identity','The original budget database is missing or empty.')
    if saved.get('limits')!=DEFAULT_LIMITS:
        fail('completion_identity','The archived original pilot approval is not the expected original cumulative approval.')
    return registry,saved


def verify_pilot_review(state, review, repo):
    """AI developer readiness review is allowed; this is not semantic gold."""
    if (review.get('schema_version')!='completion-pilot-review-v1' or review.get('decision')!='proceed'
        or review.get('reviewer_kind') not in {'ai_developer','human_developer'}):
        fail('pilot_review_required','A recorded developer decision to proceed is required before held-out runs.')
    for field,name in [('pilot_results_sha256','Execution_Results.json'),('pilot_summary_sha256','Experiment_Summary.json')]:
        if review.get(field)!=sha_file(Path(state)/'pilot'/name):
            fail('pilot_review_changed','Developer review is not bound to the actual pilot results.')
    pins=review.get('frozen_software_sha256')
    if not isinstance(pins,dict) or not pins:
        fail('pilot_review_required','Developer review must freeze the reviewed software before held-out execution.')
    for relative,expected in pins.items():
        p=Path(relative)
        if p.is_absolute() or '..' in p.parts or not HASH.fullmatch(str(expected)) or sha_file(Path(repo)/p)!=expected:
            fail('reviewed_software_changed','Reviewed software differs: '+p.name)
    return {'review_sha256':digest(review),'reviewer_kind':review['reviewer_kind'],
            'semantic_human_labels':False,'software_sha256':pins}


APPROVED_43_SCOPE = '49267c92182e0a88aa92523297c7acc4b16fd565e35f9e05a5eca174b8923c8a'
SOFTWARE_DELTA = {'evaluation/final/completion_operator.py','evaluation/final/completion_runner.py',
    'evaluation/final/test_completion_operator.py','evaluation/final/test_experiment_runner.py'}


def verify_software_compatibility(state, repo, path, expected_sha256, original_review):
    """Attest a DEV-only classifier correction without replacing approved scope."""
    raw=safe_bytes(path,5*1024*1024)
    if not HASH.fullmatch(str(expected_sha256)) or hashlib.sha256(raw).hexdigest()!=expected_sha256:
        fail('software_compatibility','The reviewed software transition failed its exact file hash check.')
    proof=json.loads(raw);state=Path(state);repo=Path(repo);output=state/'completion'
    scope=read_json(output/'Completion_Scope.json');approval=read_json(output/'private'/'Scope_Approval.json')
    amendment=read_json(output/'private'/'Budget_Amendment.json')
    if (proof.get('schema_version')!='completion-software-compatibility-v1'
            or proof.get('review_basis')!='development_only_case_local_budget_classification'
            or proof.get('original_scope_unchanged') is not True
            or proof.get('original_pilot_review')!=original_review
            or proof.get('approved_scope_sha256')!=APPROVED_43_SCOPE
            or scope.get('scope_sha256')!=APPROVED_43_SCOPE
            or digest({k:v for k,v in scope.items() if k!='scope_sha256'})!=APPROVED_43_SCOPE
            or approval.get('scope_sha256')!=APPROVED_43_SCOPE
            or amendment.get('plan_sha256')!=APPROVED_43_SCOPE
            or proof.get('cumulative_limits_sha256')!=digest(scope['proposed_cumulative_limits'])
            or scope['proposed_cumulative_limits']!=PROPOSED_LIMITS):
        fail('software_compatibility','The classifier transition does not match the existing approved scope, review or cumulative limits.')
    old=original_review.get('frozen_software_sha256',{});pins=proof.get('runtime_software_sha256',{})
    original_derived={'review_sha256':digest(original_review),'reviewer_kind':original_review['reviewer_kind'],
        'semantic_human_labels':False,'software_sha256':old}
    if scope['pilot_review']!=original_derived or set(pins)!=set(old) or not pins:
        fail('software_compatibility','The original approved software identity differs.')
    changed={name for name in old if old[name]!=pins[name]}
    if changed!=SOFTWARE_DELTA:
        fail('software_compatibility','Only the two reviewed classifier files and their two tests may change.')
    for name,expected in pins.items():
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or not HASH.fullmatch(str(expected)) or sha_file(repo/relative)!=expected:
            fail('reviewed_software_changed','Reviewed continuation software differs: '+relative.name)
    for field,name in [('pilot_results_sha256','Execution_Results.json'),('pilot_summary_sha256','Experiment_Summary.json')]:
        if original_review.get(field)!=sha_file(state/'pilot'/name):fail('pilot_review_changed','The original pilot evidence changed.')
    records=proof.get('original_records_sha256',{})
    required={f'completion/development_sanity/executions/COMPLETION_DEV_{arm}_{case}_r1/Result.json'
        for arm in ('C0','C1','C2') for case in ('F002','F003')}
    required|={f'completion/development_coverage/executions/COMPLETION_DEV_COVERAGE_C2_F{i:03d}_r1/Result.json'
        for i in range(1,21) if i not in (2,3)}
    required|={'completion/development_sanity/Experiment_Manifest.json','completion/development_coverage/Experiment_Manifest.json',
        'completion/development_coverage/executions/COMPLETION_DEV_COVERAGE_C2_F020_r1/Journal_Report.json',
        'completion/Development_Readiness.json','completion/Development_Coverage_Readiness.json'}
    if set(records)!=required or any(not HASH.fullmatch(str(value)) for value in records.values()):
        fail('software_compatibility','The transition must bind every original development result and manifest.')
    receipt_path=output/'Software_Compatibility_Review.json'
    receipt={'schema_version':'completion-software-compatibility-receipt-v1','review_sha256':expected_sha256,
        'approved_scope_sha256':APPROVED_43_SCOPE,'original_pilot_review_sha256':digest(original_review),
        'runtime_software_sha256':pins,'original_records_sha256':records,
        'changed_source_files':sorted(changed),'reviewer_kind':'ai_developer','human_review':False,
        'scope_approval_replaced':False,'budget_limits_or_clock_changed':False,'failed_case_retried':False,
        'classification':'Verified case-local budget exhaustion remains a failed outcome; it does not stop independent cases. Global accounting and infrastructure stops remain enforced.'}
    resumed=receipt_path.exists()
    if resumed and read_json(receipt_path)!=receipt:fail('software_compatibility','Saved software compatibility receipt changed.')
    if not resumed and any((output/name).exists() for name in ('matrix','memory','calibration','security','semantics')):
        fail('software_compatibility','A first classifier transition is only permitted before later-phase attempts.')
    for name,expected in records.items():
        is_readiness=name.endswith(('Development_Readiness.json','Development_Coverage_Readiness.json'))
        archived=output/'private'/'software_compatibility43'/Path(name).name
        source=archived if resumed and is_readiness else state/name
        if sha_file(source)!=expected:fail('software_compatibility','Original development evidence differs: '+Path(name).name)
        if is_readiness and not resumed:
            exact_file(archived,read_json(source))
            # Original reports use atomic_json's exact indent2 representation.
            if sha_file(archived)!=expected:fail('software_compatibility','The original readiness archive differs.')
    exact_file(receipt_path,receipt)
    return original_derived,pins


def reviewed_local_budget_failures(rows, cases, config, folder):
    workflow_config=workflow_config_for(config);by_case={case['case_id']:case for case in cases}
    variants={v['id']:v for v in config['variants']};proofs={}
    for row in rows:
        if row.get('error',{}).get('code')!='budget_exhausted':continue
        proof=verify_case_local_budget_failure(row,Path(folder)/'executions'/row['execution_id'],workflow_config,
            ResearchRequest.model_validate(by_case[row['case_id']]['request']).model_dump(),variants[row['variant']])
        if proof:proofs[row['execution_id']]=proof
    return proofs


def frozen_material(bundle_path):
    bundle,manifest,pilot,gold=verify_bundle(bundle_path)
    files=bundle['files']
    cases=[json.loads(line) for name in ['cases.dev.jsonl','cases.heldout.jsonl']
           for line in files[name].splitlines() if line.strip()]
    all_gold=[json.loads(line) for line in files['gold.private.jsonl'].splitlines() if line.strip()]
    if len(cases)!=60 or sum(c['split']=='heldout' for c in cases)!=40 or {c['case_id'] for c in cases}!={g['case_id'] for g in all_gold}:
        fail('completion_cases','The original frozen 60-case corpus is required; no subset is silently substituted.')
    return bundle,manifest,cases,all_gold,pilot,gold


def prepare_scope(cases, matrix, memory_config, *, bundle_sha256, source_hashes, review,
                  lesson_manifest=None, memory_sha256=None, security_sha256=None,
                  ai_only=False, human_review=None, semantic_protocol=None):
    matrix=deepcopy(matrix)
    if matrix.get('repeats')!=3 or matrix.get('splits')!=['heldout'] or 'case_limit' in matrix:
        fail('completion_protocol','All 40 held-out cases require three independent repeats.')
    expected={'C0','C1','C2','C2_lessons','C2_no_critic','C2_table_bm25','C2_plain_bm25'}
    if {v['id'] for v in matrix['variants']}!=expected:
        fail('completion_protocol','The seven-arm comparison, including both fixed-BM25 representations, is required.')
    for v in matrix['variants']:
        expected_arch={'C0':'fixed_workflow','C1':'single_agent'}.get(v['id'],'team')
        expected_retrieval={'C2_table_bm25':'table_aware_bm25','C2_plain_bm25':'plain_text_bm25'}.get(v['id'],'production_hybrid')
        if (v['architecture']!=expected_arch or v['retrieval']!=expected_retrieval
            or v['critic'] is not (v['id'] not in {'C0','C1','C2_no_critic'}) or v['lessons'] is not (v['id']=='C2_lessons')):
            fail('completion_protocol','Variant semantics differ from the paired comparison protocol.')
    e3_status='ready'
    if lesson_manifest is None or not lesson_manifest.get('lessons'):
        if not ai_only:fail('lesson_human_review','A nonempty human-approved frozen playbook is required for E3.')
        matrix['variants']=[v for v in matrix['variants'] if not v['lessons']]
        e3_status='not_run_missing_human_approved_playbook'
    heldout=select_cases(cases,matrix)
    if len(heldout)!=40:fail('completion_protocol','Exactly 40 original held-out cases are required.')
    memory_cases=[c for c in cases if c['case_id'] in PILOT_IDS and c['split']=='dev']
    if len(memory_cases)!=2 or memory_config.get('repeats')!=3 or memory_config.get('splits')!=['dev']:
        fail('completion_protocol','Memory comparison uses both original development requests, three independent repeats per arm.')
    other_dev=[c for c in cases if c['split']=='dev' and c['case_id'] not in PILOT_IDS]
    if len(other_dev)!=18:fail('completion_protocol','The other 18 original development requests must remain in the full-scope protocol.')
    if not HASH.fullmatch(str(memory_sha256)) or not HASH.fullmatch(str(security_sha256)):
        fail('completion_protocol','Freeze the historical memory context and security corpus before approving scope.')
    if matrix.get('scheduling')!=SCHEDULING:fail('completion_protocol','The prespecified balanced case-block queue is required.')
    sanity=deepcopy(matrix);sanity.pop('scheduling',None);sanity.update(experiment_id='COMPLETION_DEV',splits=['dev'],repeats=1,concurrency=2)
    sanity['variants']=[v for v in sanity['variants'] if v['id'] in {'C0','C1','C2'}]
    coverage=deepcopy(sanity);coverage.update(experiment_id='COMPLETION_DEV_COVERAGE',concurrency=3)
    coverage['variants']=[v for v in coverage['variants'] if v['id']=='C2']
    scope={'schema_version':VERSION,'bundle_sha256':bundle_sha256,'matrix':matrix,'memory_config':memory_config,'sanity_config':sanity,
           'development_coverage_config':coverage,'development_coverage_case_ids':[c['case_id'] for c in other_dev],
           'case_ids':[c['case_id'] for c in heldout],'memory_case_ids':sorted(PILOT_IDS),
           'source_hashes':source_hashes,'pilot_review':review,'lesson_manifest_sha256':lesson_manifest.get('manifest_sha256') if lesson_manifest else None,
           'memory_context_sha256':memory_sha256,'security_corpus_sha256':security_sha256,
           'human_review':human_review,
           'semantic_protocol':semantic_protocol,
           'e3_status':e3_status,'e2_protocol':'60 original development cases, paired quarantine OFF/ON, sequential stage assay',
           'publication':'disabled','proposed_cumulative_limits':PROPOSED_LIMITS,
           'phase_order':['development_sanity','development_coverage','calibration','matrix','memory','security','semantics'],
           'shared_control':'Same C2 executions reused for E1/E3/E4 critic; table-aware and plain-text BM25 are separate matched arms.',
           'counts':{'development_sanity_executions':6,'additional_development_executions':18,
                     'unique_workflow_cases':60,'heldout_workflow_executions':len(schedule(heldout,matrix)),
                     'memory_workflow_executions':len(schedule(memory_cases,memory_config)),
                     'security_stage_executions':120,'support_judge_calibration_items':24 if human_review else 0,
                     'maximum_heldout_risk_judgments':sum(c['request']['include_risks'] for c in heldout)*len(matrix['variants'])*3},
           'claims':{'semantic_judge_qualified':False,'human_review_claim':False,'heldout_completed':False}}
    scope['scope_sha256']=digest(scope)
    return scope


def preview(scope, cases, pricing, stats):
    full=forecast(scope['matrix'],cases,pricing)
    memory_cases=[c for c in cases if c['case_id'] in PILOT_IDS]
    memory=forecast(scope['memory_config'],memory_cases,pricing)
    sanity=forecast(scope['sanity_config'],memory_cases,pricing)
    development=forecast(scope['development_coverage_config'],[c for c in cases if c['case_id'] in scope['development_coverage_case_ids']],pricing)
    return {'scope_sha256':scope['scope_sha256'],'counts':scope['counts'],'e3_status':scope['e3_status'],
        'cumulative_limits':scope['proposed_cumulative_limits'],
        'phase_order':scope['phase_order'],'heldout_queue_policy':scope['matrix']['scheduling'],
        'heldout_case_order':list(dict.fromkeys(job['case_id'] for job in schedule(select_cases(cases,scope['matrix']),scope['matrix']))),
        'already_reserved_usd':stats['reserved_usd'],'already_reported_actual_usd':stats['known_actual_usd'],
        'requests_already_reserved':stats['reserved_requests'],
        'original_clock_preserved':True,'new_allowance_created':False,
        'structural_workflow_upper_usd':str(Decimal(full['uncapped_queue_conservative_usd_upper_bound'])+
                                            Decimal(memory['uncapped_queue_conservative_usd_upper_bound'])+
                                            Decimal(sanity['uncapped_queue_conservative_usd_upper_bound'])+
                                            Decimal(development['uncapped_queue_conservative_usd_upper_bound'])),
        'security_maximum_task_calls':480,'security_optional_judge_calls':120,
        'maximum_heldout_risk_judgments':scope['counts']['maximum_heldout_risk_judgments'],
        'note':'A scope and cumulative cap amendment needs plain APPROVE. Caps can stop this queue; unrun rows remain unrun. The structural upper bound is not a forecast of billed cost.'}


def approve_scope(answer, scope, registry, prior_approval):
    if answer.strip()!='APPROVE':fail('approval_required','The additional experiment scope was not approved.')
    return {'schema_version':'completion-scope-approval-v1','approval_text':'APPROVE',
        'scope_sha256':scope['scope_sha256'],'batch_id':registry['batch_id'],'run_uuid':registry['run_uuid'],
        'original_approval_sha256':digest(prior_approval),'cumulative_limits_sha256':digest(scope['proposed_cumulative_limits']),
        'approved_at':datetime.now(timezone.utc).isoformat()}


def comparison_summaries(rows, ledger_stats, *, lessons_available):
    comparisons={'E1':['C0','C1','C2'],'E3':['C2','C2_lessons'],
                 'E4_CRITIC':['C2','C2_no_critic'],'E4_REPRESENTATION':['C2_table_bm25','C2_plain_bm25']}
    result={}
    for name,arms in comparisons.items():
        selected=[r for r in rows if r['variant'] in arms]
        if name=='E3' and not lessons_available:
            result[name]={'status':'not_run_missing_human_approved_playbook','minimum_bar_met':False};continue
        report=summarize(selected,planned_jobs=40*3*len(arms),ledger_stats=ledger_stats)
        report.update(shared_control_reuse='C2 controls are reused across E1/E3/E4_CRITIC; no duplicate independent control claim.',
                      minimum_bar_met=False,semantic_ground_truth='independent labels required; model critic is not gold')
        result[name]=report
    return result


class PinnedModel:
    """Detect changed reviewed software before delegating any paid request."""
    def __init__(self,model,repo,pins):self.model,self.repo,self.pins=model,Path(repo),pins
    async def complete(self,messages,config):
        for relative,expected in self.pins.items():
            if sha_file(self.repo/relative)!=expected:fail('reviewed_software_changed','Reviewed software changed before a paid call.')
        return await self.model.complete(messages,config)


def sanity_readiness(rows, expected_jobs, stats=None, local_budget_failures=None):
    """Readiness is complete auditable execution, never a perfect-quality gate."""
    expected={j['execution_id'] for j in expected_jobs}
    seen=[r.get('execution_id') for r in rows]
    quality_errors={'unsupported_narrative','unverified_number','critic_rejected','untrusted_source_instruction',
                    'agent_response_limit','agent_step_limit','finish_required'}
    local_budget_failures=local_budget_failures or {}
    reasons=[]
    if len(seen)!=len(set(seen)) or set(seen)!=expected:reasons.append('missing_or_duplicate_development_records')
    for row in rows:
        if (row.get('status') not in {'completed','blocked'} and row.get('error',{}).get('code') not in quality_errors
                and row.get('execution_id') not in local_budget_failures):
            reasons.append('unreviewed_runtime_or_infrastructure_failure')
        if row.get('evaluation',{}).get('evaluator_error'):reasons.append('independent_evaluator_error')
        metrics=row.get('metrics',{})
        if metrics.get('llm_requests_started',0)>0 and metrics.get('cost_complete') is not True:
            reasons.append('unknown_development_call_cost')
    if stats and (stats.get('poisoned') or stats.get('requests_without_actual_cost') or stats.get('in_flight_requests')):
        reasons.append('unsettled_or_poisoned_shared_budget')
    return {'ready':not reasons,'blocking_reasons':sorted(set(reasons)),
            'verified_case_local_budget_failures':local_budget_failures,
            'completed':sum(r.get('status')=='completed' for r in rows),
            'blocked':sum(r.get('status')=='blocked' for r in rows),
            'numeric_passes':sum(r.get('evaluation',{}).get('numeric_success') is True for r in rows),
            'interpretation':'Reviewed bounded quality failures remain measured outcomes; readiness does not assert perfect development quality.'}


async def execute_scope(scope, cases, gold, output, *, ledger, model_factory, backend_factory,
                        lesson_context_factory=None, memory_contexts=None, memory_guards=None,
                        security_runner=None, security_cases=None, calibration_runner=None, semantic_runner=None,
                        progress=None, software_test_mode=False):
    """Called only after scope approval; all components share one live ledger."""
    if ledger is None and not software_test_mode:fail('approval_required','The existing approved cumulative ledger is required.')
    output=Path(output);output.mkdir(parents=True,exist_ok=True,mode=0o700)
    exact_file(output/'Completion_Scope.json',scope)
    if scope['e3_status']=='ready' and lesson_context_factory is None:
        fail('lesson_human_review','The frozen human-approved lesson context is unavailable.')
    memory_cases=[c for c in cases if c['case_id'] in PILOT_IDS]
    sanity=await run_experiment(memory_cases,scope['sanity_config'],output/'development_sanity',ledger=ledger,
        model_factory=model_factory,backend_factory=backend_factory,evaluate=build_cases.evaluate_case,private_gold=gold,
        on_progress=progress,software_test_mode=software_test_mode)
    sanity_rows=read_json(output/'development_sanity'/'Execution_Results.json',50*1024*1024)
    readiness=sanity_readiness(sanity_rows,schedule(memory_cases,scope['sanity_config']),ledger.stats() if ledger else None,
        reviewed_local_budget_failures(sanity_rows,memory_cases,scope['sanity_config'],output/'development_sanity'))
    atomic_json(output/'Development_Readiness.json',readiness)
    if not readiness['ready']:
        report={'scope_sha256':scope['scope_sha256'],'status':'blocked_development_sanity','development_sanity':sanity,
                'heldout_started':False,'all_minimum_bar_rows_met':False,
                'readiness':readiness,'reason':'Development execution has unresolved integrity, infrastructure or cost issues. Known bounded quality failures remain recorded outcomes; no automatic retuning or retry.'}
        atomic_json(output/'Completion_Report.json',report)
        return report
    dev_cases=[c for c in cases if c['case_id'] in scope['development_coverage_case_ids']]
    development=await run_experiment(dev_cases,scope['development_coverage_config'],output/'development_coverage',ledger=ledger,
        model_factory=model_factory,backend_factory=backend_factory,evaluate=build_cases.evaluate_case,private_gold=gold,
        on_progress=progress,software_test_mode=software_test_mode)
    dev_rows=read_json(output/'development_coverage'/'Execution_Results.json',50*1024*1024)
    coverage_readiness=sanity_readiness(dev_rows,schedule(dev_cases,scope['development_coverage_config']),ledger.stats() if ledger else None,
        reviewed_local_budget_failures(dev_rows,dev_cases,scope['development_coverage_config'],output/'development_coverage'))
    atomic_json(output/'Development_Coverage_Readiness.json',coverage_readiness)
    if not coverage_readiness['ready']:
        report={'scope_sha256':scope['scope_sha256'],'status':'blocked_development_coverage','development_sanity':sanity,
                'development_coverage':development,'readiness':coverage_readiness,'heldout_started':False,'all_minimum_bar_rows_met':False}
        atomic_json(output/'Completion_Report.json',report)
        return report
    calibration=None
    if calibration_runner is not None:
        calibration=await calibration_runner(ledger,output/'calibration')
    # Priority is frozen before held-out outcomes. Complete paired repeat blocks
    # first; every remaining memory/security job stays in the approved scope.
    matrix=await run_experiment(cases,scope['matrix'],output/'matrix',ledger=ledger,
        model_factory=model_factory,backend_factory=backend_factory,evaluate=build_cases.evaluate_case,private_gold=gold,
        lesson_context_factory=lesson_context_factory,on_progress=progress,software_test_mode=software_test_mode)
    memory=await run_experiment(memory_cases,scope['memory_config'],output/'memory',ledger=ledger,
        model_factory=model_factory,backend_factory=backend_factory,evaluate=build_cases.evaluate_case,private_gold=gold,
        executor=make_memory_executor(memory_contexts or {},memory_guards),on_progress=progress,software_test_mode=software_test_mode)
    # E2 patches a process-local detector and must run alone, after workflow jobs.
    security=None
    if security_runner is not None:
        security=await security_runner(security_cases,model_factory,output/'security',ledger,
            judge_factory=model_factory if calibration is not None else None,
            calibration=calibration,allow_ai_reference=False,progress=progress)
    semantics=None
    if semantic_runner is not None:
        semantics=await semantic_runner(cases,output/'matrix',ledger=ledger,model_factory=model_factory,
            calibration=calibration,output=output/'semantics')
    rows=read_json(output/'matrix'/'Execution_Results.json',50*1024*1024)
    stats=ledger.stats() if ledger is not None else None
    report={'status':'evaluations_recorded','scope_sha256':scope['scope_sha256'],'development_sanity':sanity,'development_readiness':readiness,
            'development_coverage':development,'development_coverage_readiness':coverage_readiness,'calibration':calibration,
            'security':security,'memory':memory,'matrix':matrix,'semantic_proposals':semantics,
            'comparisons':comparison_summaries(rows,stats,lessons_available=scope['e3_status']=='ready'),
            'budget':stats,'all_minimum_bar_rows_met':False,'human_semantic_adjudication_pending':True}
    atomic_json(output/'Completion_Report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',required=True);parser.add_argument('--state',required=True)
    parser.add_argument('--evaluator-private',required=True);parser.add_argument('--pilot-review',required=True)
    parser.add_argument('--software-review');parser.add_argument('--software-review-sha256')
    parser.add_argument('--human-review',help='The actual exported Analyst_Desk_Human_Review_Results.json')
    parser.add_argument('--lesson-store');parser.add_argument('--lesson-manifest');parser.add_argument('--ai-only',action='store_true')
    parser.add_argument('--execute',action='store_true',help='Run the separately approved expanded scope; default is preview only')
    args=parser.parse_args()
    # CLI integration is deliberately explicit; no default can create a new
    # paid ledger or imply approval of the expanded scope.
    try:
        result=asyncio.run(operate(args))
    except (KeyboardInterrupt,asyncio.CancelledError):
        result={'status':'interrupted','reason':'operator_interrupted_no_automatic_retry'}
    except Exception as exc:
        code=getattr(exc,'code','completion_setup_error')
        if not isinstance(code,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}',code):code='completion_setup_error'
        result={'status':'paused' if code=='approval_required' else 'failed','error_code':code,
                'message':str(exc)[:800] if isinstance(exc,WorkflowError) else 'Completion setup did not finish; no raw error or credential was exported.'}
        print('Completion stopped: '+result['error_code']+'. '+result.get('message',''))
    try:
        folder=private_directory(Path(args.state)/'completion')
        atomic_json(folder/'Completion_Status.json',result)
    except Exception:
        pass
    status=result.get('status','evaluations_recorded')
    print('Completion evaluation status: '+status+'. Review the saved results and cumulative budget report.')
    return 0 if result.get('status') not in {'failed','interrupted'} else 1


async def operate(args, *, input_fn=input, print_fn=print):
    session={}
    try:
        return await _operate(args,input_fn=input_fn,print_fn=print_fn,_session=session)
    finally:
        # Every model phase uses this exact ledger. This runs on cancellation,
        # interruption and failure as well as success; no stale S40 totals are
        # relabelled as the completion run's costs.
        try:
            ledger=session.get('ledger')
            report=ledger.stats() if ledger is not None else session.get('budget_snapshot',
                {'accounting_unavailable':True,'reason':'completion_ledger_not_opened','no_new_ledger_created':True})
        except Exception as exc:
            report={'accounting_unavailable':True,'error_code':getattr(exc,'code','budget_report_unavailable')}
        try:
            folder=private_directory(Path(args.state)/'completion')
            atomic_json(folder/'Completion_Budget_Report.json',report)
        except Exception:
            pass


async def _operate(args, *, input_fn=input, print_fn=print, _session=None):
    """Complete concrete setup before asking for the additional scope approval."""
    repo=Path(args.repo).absolute();state=Path(args.state).absolute();private=Path(args.evaluator_private).absolute()
    registry,saved=existing_run(repo,state)
    if not getattr(args,'human_review',None) and not args.ai_only:
        print_fn('Human review is still needed. Complete the supplied review form, export Analyst_Desk_Human_Review_Results.json, then supply --human-review PATH. No additional model call was made.')
        return {'status':'paused_human_review_required','new_model_calls':0,'heldout_started':False}
    with completion_lock(state):
        bundle,manifest,cases,gold,pilot,pilot_gold=frozen_material(private/'Operator_Case_Bundle.json')
        if getattr(args,'software_review',None):
            review,runtime_pins=verify_software_compatibility(state,repo,args.software_review,
                args.software_review_sha256,read_json(args.pilot_review))
        else:
            review=verify_pilot_review(state,read_json(args.pilot_review),repo);runtime_pins=review['software_sha256']
        # Read the immutable paid-pilot identity first, but scan every current
        # source before re-running its legacy byte checks. Diagnostics therefore
        # cover all companies even when a pilot cache has also changed.
        pilot_binding=read_json(state/'private'/'Source_Binding.json')
        if (pilot_binding.get('binding_sha256') != registry.get('source_binding_sha256')
                or pilot_binding.get('binding_sha256') != digest({k:v for k,v in pilot_binding.items() if k!='binding_sha256'})):
            fail('source_binding_identity','The existing paid pilot binding differs from its registered identity.')
        heldout=[c for c in cases if c['split']=='heldout']
        output=private_directory(state/'completion')
        require_completion_binding=any((output/name).exists() for name in (
            'Completion_Scope.json','private/Scope_Approval.json','private/Budget_Amendment.json',
            'development_sanity/Experiment_Manifest.json','matrix/Experiment_Manifest.json'))
        bound_gold,source_binding=bind_completion_sources(repo,manifest,cases,gold,pilot_binding,
            certificate_path=private/'Completion_Source_Equivalence.json',
            binding_path=output/'Completion_Source_Binding.json',
            diagnostics_path=output/'Source_Cache_Diagnostics.json',
            frozen_bundle_sha256=bundle['bundle_sha256'],
            original_freeze_manifest_sha256=bundle['original_freeze_manifest_sha256'],
            require_existing=require_completion_binding)
        # Preserve the original independently reviewed pilot certificate and
        # exact four-file binding; never rewrite or widen that earlier receipt.
        bind_sources(repo,manifest,pilot,pilot_gold,private/'Verified_Source_Equivalence.json',
            state/'private'/'Source_Binding.json',frozen_bundle_sha256=bundle['bundle_sha256'],
            original_freeze_manifest_sha256=bundle['original_freeze_manifest_sha256'],
            expected_binding_sha256=registry.get('source_binding_sha256'),require_existing=True)
        from workflow_memory import AnalystMemory
        from evaluation.final.memory_comparison import verify_historical_publication, _store_for_assay
        verified=verify_historical_publication(read_json(private/'25_Publication_Approval_Binding.json'),
                                              read_json(private/'25_Publication_Receipt.json'))
        memory_folder=private_directory(state/'completion'/'private')
        memory=AnalystMemory(memory_folder/'Historical_Memory.sqlite3','completion-evaluator',enabled=True)
        if memory.storage.get('published_notes',verified['note_id']) is None:
            _store_for_assay(memory,verified)
        contexts={};guards={}
        for case in pilot:
            request=ResearchRequest.model_validate(case['request']).model_dump();key=digest(request)
            memory.enabled=True;on=memory.context(request);memory.enabled=False;off=memory.context(request)
            contexts[key]={'on':on,'off':off}
            guards[key]=lambda payload,request=request:memory.guard_external(payload,approved_tickers=request['tickers'])
        lesson_store=lesson_manifest=lesson_factory=human_receipt=human_packet=human_export=None
        if getattr(args,'human_review',None):
            from evaluation.final.completion_calibration import import_human_review
            if sha_file(private/'Human_Review_Packet.json')!=HUMAN_PACKET_RAW_SHA256:
                fail('human_packet_changed','Use the exact supplied blind human-review packet.')
            human_packet=read_json(private/'Human_Review_Packet.json');human_export=read_json(args.human_review)
            lesson_path=memory_folder/'Lessons.sqlite3'
            human_receipt,lesson_manifest=import_human_review(human_packet,human_export,
                store_path=lesson_path,owner='local-analyst',output=memory_folder/'human_review')
            lesson_store=LessonStore(lesson_path,'local-analyst')
            if lesson_manifest.get('lessons'):
                lesson_factory=make_lesson_context_factory(lesson_store,lesson_manifest,heldout)
        elif args.lesson_store or args.lesson_manifest:
            if not args.lesson_store or not args.lesson_manifest:fail('lesson_human_review','Both local lesson store and frozen manifest are required.')
            lesson_manifest=read_json(args.lesson_manifest)
            lesson_store=LessonStore(args.lesson_store,'local-analyst');lesson_store.verify_freeze(lesson_manifest)
            if lesson_manifest.get('lessons'):
                lesson_factory=make_lesson_context_factory(lesson_store,lesson_manifest,heldout)
        from evaluation.final.paired_security import load_frozen_cases, run_paired_security
        security_path=private/'Frozen_Security_Cases.json';security_cases=load_frozen_cases(security_path)
        from retrieval_data import PassageCatalogue
        from evaluation.final.adapters import LocalCorpusBackend
        catalogue=PassageCatalogue(repo/'data'/'passages',repo/'data'/'filings')
        from evaluation.final.completion_semantics import (run_completion_semantics,RUBRIC,RUBRIC_SHA256,
                                                          PROMPT_SHA256,MAX_OUTPUT_TOKENS)
        config_dir=Path(__file__).with_name('configs')
        matrix=load_experiment(config_dir/'completion_matrix.json');memory_config=load_experiment(config_dir/'memory_matrix.json')
        source_hashes=dict(source_binding['source_hashes'])
        source_hashes['data/passages/manifest.json']=catalogue.manifest_sha
        scope=prepare_scope(cases,matrix,memory_config,bundle_sha256=bundle['bundle_sha256'],source_hashes=source_hashes,
            review=review,lesson_manifest=lesson_manifest,memory_sha256=digest(contexts),security_sha256=sha_file(security_path),
            ai_only=args.ai_only,human_review=human_receipt,
            semantic_protocol={'rubric':RUBRIC,'rubric_sha256':RUBRIC_SHA256,'prompt_sha256':PROMPT_SHA256,
                               'max_output_tokens':MAX_OUTPUT_TOKENS,'requires_actual_human_calibration':True,
                               'does_not_replace_gold_task_success':True})
        output=private_directory(state/'completion')
        exact_file(output/'Completion_Source_Binding.json',source_binding)
        exact_file(output/'private'/'Memory_Contexts.json',contexts)
        # The amendment module reopens and preserves the existing ledger. It is
        # supplied by the reviewed budget implementation, never generated here.
        from evaluation.final.completion_budget import (BudgetClient as CompletionBudgetClient,
            preview_budget_amendment,amend_existing_budget)
        amendment_args=(state/'private'/'Budget.sqlite3',registry['batch_id'],saved['pricing'],saved['limits'],
            saved['approval'],scope['proposed_cumulative_limits'],scope['scope_sha256'])
        budget_preview=preview_budget_amendment(*amendment_args)
        _session['budget_snapshot']=budget_preview['current_stats']
        persist_scope_with_preview_handoff(output,scope,budget_preview)
        plan=preview(scope,cases,saved['pricing'],budget_preview['current_stats']);plan['budget_amendment']=budget_preview
        atomic_json(output/'Completion_Plan.json',plan)
        print_fn(canonical(plan))
        if not args.execute:return {'status':'preview_only','scope_sha256':scope['scope_sha256']}
        keys=load_credentials(repo);backend=MCPBackend({'SANDBOX_API_TOKEN':keys['SANDBOX_API_TOKEN']})
        preflight=await preflight_backend(backend,cases,bound_gold)
        atomic_json(output/'Completion_Preflight.json',preflight)
        approval_path=output/'private'/'Scope_Approval.json'
        amendment_answer=None
        if approval_path.exists():
            approval=read_json(approval_path)
            if approval.get('scope_sha256')!=scope['scope_sha256'] or approval.get('run_uuid')!=registry['run_uuid']:
                fail('completion_identity','The saved additional scope approval differs.')
        else:
            answer=input_fn('Enter APPROVE for this additional scope and cumulative amendment, or Enter to pause: ')
            approval=approve_scope(answer,scope,registry,saved['approval']);exact_file(approval_path,approval)
            amendment_answer=answer
        # A crash between durable scope approval and ledger amendment may resume
        # the exact recorded APPROVE; it never authorizes changed scope or caps.
        if amendment_answer is None and budget_preview.get('status')!='already_amended':
            amendment_answer=approval['approval_text']
        ledger,amendment_receipt=amend_existing_budget(*amendment_args,answer=amendment_answer)
        _session['ledger']=ledger
        exact_file(output/'private'/'Budget_Amendment.json',amendment_receipt)
        representations={name:LocalCorpusBackend(catalogue,repo/'data'/'raw',plain_text=plain)
                         for name,plain in [('table_aware_bm25',False),('plain_text_bm25',True)]}
        def model_factory(run_id,config):
            model=CompletionBudgetClient(keys['OPENROUTER_API_KEY'],ledger,run_id,purpose='completion_expanded_scope')
            return PinnedModel(model,repo,runtime_pins)
        async def calibrate(ledger,folder):
            from evaluation.final.completion_calibration import run_support_calibration
            report,rows=await run_support_calibration(human_packet,human_export,ledger=ledger,
                api_key=keys['OPENROUTER_API_KEY'],output=folder,model_factory=model_factory)
            return report
        return await execute_scope(scope,cases,bound_gold,output,ledger=ledger,model_factory=model_factory,
            backend_factory=lambda variant:representations.get(variant['retrieval'],backend),
            lesson_context_factory=lesson_factory,memory_contexts=contexts,memory_guards=guards,
            security_runner=run_paired_security,security_cases=security_cases,
            calibration_runner=calibrate if human_packet is not None else None,
            semantic_runner=run_completion_semantics,
            progress=lambda row:print_fn(row.get('execution_id','evaluation')+': '+row.get('status','recorded')))


if __name__=='__main__':
    raise SystemExit(main())
