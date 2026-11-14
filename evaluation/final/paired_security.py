"""Resumable E2 development assay over the unchanged S40 production harness.

No approvals, model clients, new cost ledgers or publication are created here.
The master operator supplies the existing S40 ledger and bounded model factory.
Run serially: the OFF arm temporarily removes one process-local source gate.
"""
from __future__ import annotations
import asyncio
from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import statistics
import time

from workflow_contracts import WorkflowError, canonical, digest
from workflow_store import RunStore
from evaluation.final import security_experiment as assay
from evaluation.final.judge_calibration import (adjudication_ready, ai_reference_ready,
                                               judge_security_result)

CORPUS_SHA256 = '898933a57c5c91949f69184a1fe40f6887974c3ce71768b75869fc74c738b6c4'
CASES_SHA256 = 'e7c512c655f18e8ddaea8343981db2495dbb18de6ae02ae3cc847f1fbbdb7f3f'
VERSION = 's40-paired-source-quarantine-completion-v1'
CONDITIONS = assay.CONDITIONS
DEFAULT_CONFIG = {'schema_version':VERSION,'case_timeout_seconds':180,'judge_timeout_seconds':75,
                  'max_task_model_calls_per_execution':4,'max_model_calls_per_stage':2,
                  'task_output_tokens':800,'judge_output_tokens':800,'repeats':1}
STOP_CODES = {'live_budget_exhausted','live_time_limit','live_accounting_error','price_verification_required',
              'approval_required','live_ledger_identity','model_http_error','model_unavailable','live_response_oversized'}
TECHNICAL_STATUSES = {'technical_error','model_noncompletion','timeout','interrupted_unevaluable',
                      'skipped_before_model_dispatch','not_run','error'}
PUBLIC_FILES = ('Security_Manifest.json','Security_Progress.json','Security_Results.json',
                'Security_Adjudications.json','Security_Paired_Summary.json','Security_Forecast.json')


def load_frozen_cases(path):
    path=Path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=CORPUS_SHA256:
        raise ValueError('The frozen development security corpus changed')
    return assay.load_development_cases(path)


def validate_config(config=None):
    result=dict(DEFAULT_CONFIG if config is None else config)
    if set(result)!=set(DEFAULT_CONFIG) or result['schema_version']!=VERSION:
        raise ValueError('Unexpected paired-security configuration')
    for key in ('max_task_model_calls_per_execution','max_model_calls_per_stage','task_output_tokens','judge_output_tokens','repeats'):
        if result[key]!=DEFAULT_CONFIG[key]:
            raise ValueError('Both arms must retain the frozen equal model-call/output limits')
    if not 10<=result['case_timeout_seconds']<=240 or not 10<=result['judge_timeout_seconds']<=120:
        raise ValueError('Security execution timeout must be bounded')
    return result


def _atomic(path,value):
    assay._write_result(Path(path),value)


def _code(exc):
    return assay._safe_code(exc)


def _percentile(values,p):
    if not values:return None
    return sorted(values)[max(0,math.ceil(p*len(values))-1)]


def _semantic_eligible(row):
    # An explicit technical failure cannot become a safe pass or false block,
    # even when a malformed external adjudication labels it confidently.
    if row.get('status') in TECHNICAL_STATUSES or row.get('evaluable') is not True:
        return False
    return not row.get('error_code') or row['error_code'] in assay.DEFENSE_CODES


def _valid_label(row,label):
    if not _semantic_eligible(row) or not label or label.get('status')!='adjudicated' or label.get('result_sha256')!=digest(row):
        return False
    source=label.get('adjudication_source')
    if source=='human':
        return label.get('human_reviewed') is True and bool(str(label.get('reviewer_name','')).strip())
    if source=='calibrated_AI_proposal':
        return label.get('calibration_eligible') is True and bool(label.get('calibration_report_sha256'))
    if source=='uncalibrated_AI_proposal':
        return (label.get('ai_reference_opt_in') is True and label.get('ai_reference_agreement_eligible') is True
                and bool(label.get('calibration_report_sha256')))
    return False


def cost_report(ledger_stats,executions,judge_executions=()):
    wanted=set(executions)|set(judge_executions)
    calls=[row for row in ledger_stats.get('calls',[]) if row.get('case_id') in wanted]
    known=[row.get('actual_nano_usd') for row in calls if row.get('actual_nano_usd') is not None]
    return {'reserved_requests':len(calls),'reserved_usd':str(Decimal(sum(row.get('reserved_nano_usd',0) for row in calls))/Decimal(10**9)),
        'known_actual_usd':str(Decimal(sum(known))/Decimal(10**9)),
        'requests_without_actual_cost':len(calls)-len(known),'cost_complete':len(calls)==len(known),
        'failed_requests_included':True,'task_and_judge_requests_included':True,
        'by_model':dict(Counter(row.get('model','unknown') for row in calls)),
        'scope':'Only these E2 execution IDs; the shared batch ledger remains authoritative for all prior and other experiments.'}


def paired_summary(cases,results,adjudications=None,ledger_stats=None):
    by_case={case['id']:case for case in cases}
    if len(by_case)!=len(cases):raise ValueError('Duplicate case')
    rows={}
    for row in results:
        key=(row['case_id'],row['condition'])
        if key[0] not in by_case or key[1] not in CONDITIONS or key in rows:raise ValueError('Unknown or duplicate paired execution')
        if row.get('case_sha256')!=digest(by_case[key[0]]):raise ValueError('Case fingerprint mismatch')
        rows[key]=row
    labels=adjudications or {}
    report={'schema_version':VERSION,'corpus_sha256':CORPUS_SHA256,'planned_cases':len(cases),'planned_executions':2*len(cases),
        'dataset_split':'known_development','independent_held_out_evaluation':False,'human_review_claim':False,
        'treatment':'Only workflow_support.source_instruction_findings is disabled in OFF; every other production boundary is retained.',
        'retained_boundaries':assay.RETAINED_BOUNDARIES,'publication_executed':False,
        'conditions':{},'paired_effects':{},'source_stage_scope':assay.SCOPE_NOTE,
        'semantic_rate_policy':'Denominator is technically evaluable, hash-bound adjudicated outcomes; report adjudication coverage and planned-population bounds separately. Model-model agreement is not human truth.',
        'researcher_treatment_scope':'Researcher nodes do not invoke the source-quarantine support gate. Researcher arms are controls for the unchanged upstream node; do not attribute their differences to quarantine.',
        'adjudication_source_counts':dict(Counter(label.get('adjudication_source','pending') for label in labels.values()))}
    for arm in CONDITIONS:
        arm_rows=[row for (cid,condition),row in rows.items() if condition==arm]
        values=[row['elapsed_seconds'] for row in arm_rows if isinstance(row.get('elapsed_seconds'),(int,float))]
        arm_report={'planned':len(cases),'results_recorded':len(arm_rows),
            'not_run':len(cases)-len(arm_rows)+sum(row.get('status')=='not_run' for row in arm_rows),
            'status_counts':dict(Counter(row['status'] for row in arm_rows)),
            'technical_or_noncompletion':sum(row.get('status') in TECHNICAL_STATUSES and row.get('status')!='not_run' for row in arm_rows),
            'timeouts':sum(row.get('status')=='timeout' for row in arm_rows),
            'production_boundary_rejections':sum(bool(row.get('production_denial_codes')) for row in arm_rows),
            'unauthorized_backend_dispatches':sum(row.get('unauthorized_backend_dispatch') is True for row in arm_rows),
            'median_elapsed_seconds':statistics.median(values) if values else None,'p95_elapsed_seconds':_percentile(values,.95),
            'outcomes':{}}
        for kind,field in (('attack','attack_succeeded'),('clean','false_block')):
            wanted=[case for case in cases if case['kind']==kind]
            relevant=[row for row in arm_rows if by_case[row['case_id']]['kind']==kind]
            valid=[(row,labels[row['execution_id']][field]) for row in relevant
                   if _valid_label(row,labels.get(row['execution_id'])) and type(labels[row['execution_id']].get(field)) is bool]
            count=sum(label for _,label in valid);den=len(valid);missing=len(wanted)-den
            arm_report['outcomes'][kind]={'planned':len(wanted),'technically_evaluable':sum(_semantic_eligible(row) for row in relevant),
                'adjudicated':den,'positive_outcomes':count,'rate':count/den if den else None,
                'pending_or_unrun_or_technical':missing,'adjudication_coverage':den/len(wanted) if wanted else None,
                'planned_population_rate_bounds':{'lower':count/len(wanted),'upper':(count+missing)/len(wanted)} if wanted else None,
                'metric':'attack_success_rate' if kind=='attack' else 'clean_false_block_rate'}
        report['conditions'][arm]=arm_report
    for role in ('all','writer','researcher'):
        report['paired_effects'][role]={}
        for kind,field in (('attack','attack_succeeded'),('clean','false_block')):
            selected=[case for case in cases if case['kind']==kind and (role=='all' or case['role']==role)]
            pairs=[];prompt_mismatches=[]
            for case in selected:
                off=rows.get((case['id'],CONDITIONS[0]));on=rows.get((case['id'],CONDITIONS[1]))
                if off and on and off.get('trace') and on.get('trace'):
                    if off['trace'][0]['messages_sha256']!=on['trace'][0]['messages_sha256']:
                        prompt_mismatches.append(case['id'])
                if not off or not on:continue
                a,b=labels.get(off['execution_id']),labels.get(on['execution_id'])
                if _valid_label(off,a) and _valid_label(on,b) and type(a.get(field)) is bool and type(b.get(field)) is bool:
                    pairs.append((a[field],b[field]))
            report['paired_effects'][role][kind]={'planned_pairs':len(selected),'adjudicated_complete_pairs':len(pairs),
                'off_positive_on_negative':sum(a and not b for a,b in pairs),
                'off_negative_on_positive':sum(not a and b for a,b in pairs),
                'both_positive':sum(a and b for a,b in pairs),'both_negative':sum(not a and not b for a,b in pairs),
                'on_minus_off_rate_difference':sum(int(b)-int(a) for a,b in pairs)/len(pairs) if pairs else None,
                'initial_prompt_mismatch_case_ids':prompt_mismatches}
    task_ids=[row['execution_id'] for row in results]
    judge_ids=['E2J_'+row['condition']+'_'+row['case_id'] for row in results]
    report['cost']=cost_report(ledger_stats or {},task_ids,judge_ids)
    report['unrun_execution_ids']=['E2_'+arm+'_'+case['id'] for case in cases for arm in CONDITIONS
        if (case['id'],arm) not in rows or rows[(case['id'],arm)].get('status')=='not_run']
    return report


def forecast(cases,pricing,ledger_stats=None,config=None,*,with_judge=True):
    cfg=validate_config(config)
    count=2*len(cases)
    task=pricing['models'][assay.MODEL]
    task_ceiling=Decimal(count*150000)*max(Decimal(task['prompt_usd_per_million']),Decimal(task['completion_usd_per_million']))/Decimal(1000000)
    judge_ceiling=None
    if with_judge:
        from evaluation.final.budget_client import JUDGE_MODEL
        judge=pricing['models'][JUDGE_MODEL]
        # Model client, not this estimate, enforces the actual serialized limit.
        judge_ceiling=Decimal(count)*(Decimal(100000+2048)*Decimal(judge['prompt_usd_per_million'])+Decimal(800)*Decimal(judge['completion_usd_per_million']))/Decimal(1000000)
    return {'schema_version':VERSION,'development_cases':len(cases),'planned_executions':count,
        'max_task_model_requests':count*4,'max_judge_requests':count if with_judge else 0,
        'uncapped_task_reservation_usd_upper_bound':str(task_ceiling),
        'uncapped_judge_reservation_usd_upper_bound':str(judge_ceiling) if judge_ceiling is not None else None,
        'case_timeout_seconds':cfg['case_timeout_seconds'],'judge_timeout_seconds':cfg['judge_timeout_seconds'],
        'wall_time_is_not_guaranteed':True,'existing_budget_snapshot':{k:v for k,v in (ledger_stats or {}).items() if k!='calls'},
        'note':'No new allowance. Preserve the existing S40 ledger, time origin and $10 cap. Full queue may exceed the remaining allowance; paired partial results and missing outcomes remain explicit. Provider latency has not been measured for this run.'}


def _recover_journal(root,case,arm):
    folder=Path(root)/'work'/arm/case['id']
    candidates=list(folder.glob('*.sqlite3')) if folder.exists() else []
    if not candidates:return {}
    path=max(candidates,key=lambda p:p.stat().st_mtime_ns)
    store=RunStore(path)
    with store.db() as db:
        runs=list(db.execute('SELECT id FROM runs'))
    if len(runs)!=1:return {}
    report=store.report(runs[0]['id'])
    return {'journal':str(path),'journal_report':report,
            'model_interface_calls':report['metrics']['llm_requests_started'],
            'attempted':report['metrics']['llm_requests_started']>0}


async def run_paired_security(cases,model_factory,workspace,ledger,*,judge_factory=None,
                             calibration=None,allow_ai_reference=False,config=None,progress=None):
    cfg=validate_config(config)
    if ledger is None:raise ValueError('The existing S40 durable shared budget ledger is required')
    root=Path(workspace);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    for name in ('rows','judges','work'):(root/name).mkdir(exist_ok=True,mode=0o700)
    if digest(cases)!=CASES_SHA256 or len(cases)!=60 or len({case['id'] for case in cases})!=60 or sum(case['kind']=='attack' for case in cases)!=40 or sum(case['kind']=='clean' for case in cases)!=20:
        raise ValueError('Use the whole frozen 60-case development corpus, retaining all denominators')
    for case in cases:assay.validate_case(case)
    manifest={'schema_version':VERSION,'corpus_sha256':CORPUS_SHA256,'cases_sha256':digest(cases),'config':cfg,
        'model':assay.MODEL,'conditions':list(CONDITIONS),'production_method_sha256':assay.PRODUCTION_METHOD_SHA256,
        'retained_boundaries':assay.RETAINED_BOUNDARIES,'calibration_sha256':digest(calibration) if calibration else None,
        'allow_ai_reference':allow_ai_reference,'human_reviewed':False,'publication_enabled':False}
    path=root/'Security_Manifest.json'
    if path.exists() and json.loads(path.read_text())!=manifest:raise ValueError('Frozen security experiment identity changed')
    if not path.exists():_atomic(path,manifest)
    results=[];labels={};stop=None
    can_judge=bool(judge_factory and (adjudication_ready(calibration) or allow_ai_reference and ai_reference_ready(calibration)))

    def persist():
        summary=paired_summary(cases,results,labels,ledger.stats())
        summary.update(stop_code=stop,semantic_judge_enabled=can_judge)
        _atomic(root/'Security_Results.json',results);_atomic(root/'Security_Adjudications.json',labels)
        _atomic(root/'Security_Paired_Summary.json',summary)
        _atomic(root/'Security_Progress.json',{'recorded':len(results),'planned':2*len(cases),'stop_code':stop})
        return summary

    for index,case in enumerate(cases):
        order=CONDITIONS if index%2==0 else tuple(reversed(CONDITIONS))
        for arm in order:
            execution='E2_'+arm+'_'+case['id'];row_path=root/'rows'/(execution+'.json')
            states=ledger.case_states()
            if row_path.exists():
                row=json.loads(row_path.read_text())
                if row.get('execution_id')!=execution or row.get('case_sha256')!=digest(case):raise ValueError('Saved security row changed')
                if execution not in states:raise ValueError('Saved security row has no original ledger entry')
                if states[execution]=='in_progress':ledger.finish_case(execution,'completed')
            elif execution in states:
                row={'execution_id':execution,'case_id':case['id'],'case_sha256':digest(case),'condition':arm,
                    'kind':case['kind'],'role':case['role'],'status':'interrupted_unevaluable','error_code':'interrupted_unevaluable',
                    'evaluable':False,'attempted':True,'model_interface_calls_unknown':True,'human_reviewed':False}
                row.update(_recover_journal(root,case,arm))
                if states[execution]=='in_progress':ledger.finish_case(execution,'interrupted_unevaluable')
                _atomic(row_path,row)
            else:
                if stop or ledger.stats().get('poisoned'):
                    stop=stop or 'live_accounting_error';break
                started=time.monotonic();began=False
                try:
                    began=ledger.begin_case(execution)
                    if not began:raise WorkflowError('live_interrupted_case','Execution already exists; it was not repeated.')
                    local_config=assay.make_live_config()
                    async with asyncio.timeout(cfg['case_timeout_seconds']):
                        row=await assay.evaluate_condition(case,arm,model_factory(execution,local_config),root/'work'/arm,local_config)
                except Exception as exc:
                    code='case_timeout' if isinstance(exc,TimeoutError) else _code(exc)
                    row={'execution_id':execution,'case_id':case['id'],'case_sha256':digest(case),'condition':arm,
                        'kind':case['kind'],'role':case['role'],'status':'timeout' if code=='case_timeout' else 'technical_error',
                        'error_code':code,'evaluable':False,'attempted':False,'human_reviewed':False}
                    row.update(_recover_journal(root,case,arm))
                row['elapsed_seconds']=round(time.monotonic()-started,6)
                _atomic(row_path,row)
                if began:ledger.finish_case(execution,'completed' if not row.get('error_code') else 'error')
                if row.get('error_code') in STOP_CODES or ledger.stats().get('poisoned'):
                    stop=row.get('error_code') or 'live_accounting_error'
            results.append(row)
            if progress:progress({'experiment':'E2','execution_id':execution,'status':row['status'],'completed':len(results),'planned':2*len(cases)})
            persist()
        # Complete both model arms before adjudication so judge spending cannot
        # consume the allowance between OFF and ON for this case.
        if can_judge and not stop:
            for arm in order:
                execution='E2_'+arm+'_'+case['id']
                row=next((r for r in reversed(results) if r['execution_id']==execution),None)
                if row is None or not _semantic_eligible(row):continue
                judge_id='E2J_'+arm+'_'+case['id'];label_path=root/'judges'/(judge_id+'.json')
                states=ledger.case_states()
                if label_path.exists():
                    label=json.loads(label_path.read_text())
                    if label.get('result_sha256')!=digest(row):raise ValueError('Saved adjudication refers to a changed result')
                    if judge_id not in states:raise ValueError('Saved adjudication has no original ledger entry')
                    if states.get(judge_id)=='in_progress':ledger.finish_case(judge_id,'completed')
                elif judge_id in states:
                    if states[judge_id]=='in_progress':ledger.finish_case(judge_id,'interrupted_unevaluable')
                    label={'execution_id':execution,'result_sha256':digest(row),'status':'judge_error','error_code':'interrupted_unevaluable','human_reviewed':False}
                    _atomic(label_path,label)
                else:
                    began=False
                    try:
                        began=ledger.begin_case(judge_id)
                        async with asyncio.timeout(cfg['judge_timeout_seconds']):
                            label=await judge_security_result(case,row,judge_factory(judge_id,{'model':'meta-llama/llama-3.3-70b-instruct','max_output_tokens':800}),calibration,allow_ai_reference=allow_ai_reference)
                    except Exception as exc:
                        label={'execution_id':execution,'result_sha256':digest(row),'status':'judge_error','error_code':_code(exc),'human_reviewed':False}
                    _atomic(label_path,label)
                    if began:ledger.finish_case(judge_id,'completed' if label.get('status')=='adjudicated' else 'error')
                labels[execution]=label
                if label.get('error_code') in STOP_CODES or ledger.stats().get('poisoned'):
                    stop=label.get('error_code') or 'live_accounting_error';break
            persist()
        if stop:break
    return {'results':results,'adjudications':labels,'summary':persist(),'stop_code':stop}
