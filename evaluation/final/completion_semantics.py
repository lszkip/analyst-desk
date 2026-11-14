"""Frozen post-run AI proposals; never overwrite frozen gold or task_success.

Only the actually delivered qualitative draft and its cited full passages enter
the judge. Factual-support calibration does not validate topic completeness or
whole-filing risk changes. Those dimensions remain explicitly AI diagnostics.
"""
import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path
import re

from workflow_contracts import ResearchRequest, WorkflowError, canonical, digest
from .budget_client import JUDGE_MODEL
from .judge_calibration import adjudication_ready
from .completion_calibration import (PROMPT_SHA256 as CALIBRATION_PROMPT_SHA256,
    SUPPORT_ONLY_PROMPT, judge_support_item)
from .experiment_runner import BUDGET_CODES

VERSION = 'completion-semantic-proposals-v1'
MAX_CONTEXT_BYTES = 90000
MAX_OUTPUT_TOKENS = 800
PROMPT = SUPPORT_ONLY_PROMPT
PROMPT_SHA256 = CALIBRATION_PROMPT_SHA256
RUBRIC = {
    'version': VERSION, 'prompt_sha256': PROMPT_SHA256,
    'calibration_prompt_sha256': CALIBRATION_PROMPT_SHA256,
    'judge_model': JUDGE_MODEL, 'max_output_tokens': MAX_OUTPUT_TOKENS,
    'max_context_bytes': MAX_CONTEXT_BYTES, 'max_calls_per_completed_risk_execution': 1,
    'frozen_gold_task_success': 'unchanged; risk task_success remains null',
    'code_and_support_proposal': 'True only when numerical code checks pass and the AI factual-support label is supported. False for attempted failed delivery or numeric/support failure. Null for uncertain/unjudged support or unrun executions.',
    'scope_and_topic_diagnostics': 'Unscored coverage context only. No independent topic-completeness or whole-filing risk-change labels are claimed.',
    'calibration_transfer_limit': 'The exact calibrated support-only prompt/model are reused. Calibration on short development fragments does not establish equal accuracy on longer held-out filings or validate task completeness and full-filing risk changes.',
    'source_scope': 'Full text of cited supplied passages only, not exhaustive filings.',
}
RUBRIC_SHA256 = digest(RUBRIC)
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z')


def _read(path, maximum=64*1024*1024):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('Unsafe or missing semantic input: '+path.name)
    return json.loads(path.read_text())


def _write_once(path, value):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Unsafe semantic output')
    raw = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode()
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('Frozen semantic result changed')
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('xb') as stream:
        stream.write(raw)
    path.chmod(0o600)


def calibration_ready(calibration):
    return bool(adjudication_ready(calibration)
                and calibration.get('judge_revision') == 'support-only-v2'
                and calibration.get('judge_prompt_sha256') == CALIBRATION_PROMPT_SHA256
                and calibration.get('actual_human_labels', 0) >= 20
                and calibration.get('binary_nonuncertain', {}).get('n', 0) >= 20
                and calibration.get('binary_nonuncertain', {}).get('cohen_kappa') is not None
                and calibration['binary_nonuncertain']['cohen_kappa'] >= 0.6)


def build_context(case, row, records):
    request = ResearchRequest.model_validate(case['request']).model_dump()
    state = row.get('result') or {}
    if state.get('request') != request:
        raise ValueError('Saved answer request differs from its frozen case')
    note = state.get('note') or {}
    draft = state.get('risk_draft')
    if not draft:
        # Some single-worker states retain only the complete delivered draft.
        complete = state.get('draft') or {}
        draft = {'title': complete.get('title'), 'paragraphs': [p for p in complete.get('paragraphs', [])
                 if any(records.get(key, {}).get('kind') == 'passage' for key in p.get('evidence_ids', []))]}
    if not isinstance(draft.get('title'), str) or draft['title'] != note.get('title') or not draft.get('paragraphs'):
        raise ValueError('Delivered qualitative draft is missing or differs from note')
    body = note.get('body_markdown', '')
    citations = {c['evidence_id']: c for c in note.get('citations', [])}
    selected = {}
    for paragraph in draft['paragraphs']:
        if not isinstance(paragraph.get('text'), str) or not paragraph['text'] or not paragraph.get('evidence_ids'):
            raise ValueError('Malformed qualitative paragraph')
        rendered = paragraph['text']+' '+' '.join('['+key+']' for key in paragraph['evidence_ids'])
        if rendered not in body:
            raise ValueError('Qualitative draft was not delivered in the final note')
        for key in paragraph['evidence_ids']:
            record = records.get(key) or {}; passage = record.get('passage') or {}; citation = citations.get(key) or {}
            if (record.get('kind') != 'passage' or passage.get('passage_id') != key
                    or not isinstance(passage.get('text'), str) or not passage['text']
                    or record.get('ticker') not in request['tickers'] or record.get('year') not in request['years']
                    or passage.get('ticker') != record.get('ticker') or passage.get('fiscal_year') != record.get('year')
                    or passage.get('source_sha256') != record.get('source_sha256')
                    or passage.get('source_url') != record.get('source_url')
                    or citation.get('source_sha256') != record.get('source_sha256')
                    or citation.get('source_url') != record.get('source_url')):
                raise ValueError('A cited full passage or its source metadata is missing/inconsistent')
            selected[key] = {k: passage.get(k) for k in ('passage_id','text','ticker','fiscal_year','item',
                            'period_start','period_end','source_sha256','source_url')}
    context = {'actual_request': request, 'delivered_qualitative_draft': draft,
               'cited_full_passages': [selected[k] for k in sorted(selected)],
               'financial_table': 'Independently checked by code, not included in this qualitative judgment.'}
    if len(canonical(context).encode()) > MAX_CONTEXT_BYTES:
        raise WorkflowError('semantic_context_oversized', 'Full cited evidence exceeds the frozen judge input limit; nothing was truncated.')
    return context


def validate_proposal(value):
    if (not isinstance(value, dict) or set(value) != {'label','reason'}
            or value['label'] not in {'supported','unsupported','uncertain'}
            or not isinstance(value['reason'], str) or not 1 <= len(value['reason']) <= 1200):
        raise ValueError('Invalid factual-support proposal schema')
    return value


def support_item(context, key):
    request = context['actual_request']
    source_text = canonical(context['cited_full_passages'])
    output = context['delivered_qualitative_draft']
    return {'item_id': key, 'question': request['question'],
            'question_context': {'request': request,
                'assignment': 'Qualitative risk contribution; financial answers are supplied and checked separately by code.'},
            'source_text': source_text, 'output': output,
            'question_sha256': hashlib.sha256(request['question'].encode()).hexdigest(),
            'source_sha256': hashlib.sha256(source_text.encode()).hexdigest(), 'output_sha256': digest(output)}


def coverage_context(context):
    request = context['actual_request']
    requested = {(t,y) for t in request['tickers'] for y in request['years']}
    cited = {(p['ticker'],p['fiscal_year']) for p in context['cited_full_passages']}
    return {'requested_company_periods': [list(x) for x in sorted(requested)],
            'cited_company_periods': [list(x) for x in sorted(cited)],
            'requested_pairs_without_cited_passage': [list(x) for x in sorted(requested-cited)],
            'topic_coverage': 'not_independently_scored', 'whole_filing_change_truth': 'not_established',
            'interpretation': 'Missing citations flag coverage to inspect; they are not proof of a missing answer or an unsupported claim.'}


def _proposal_success(row, support=None):
    if row.get('status') not in {'completed','unrun','skipped'}:
        return False
    if row.get('status') != 'completed':
        return None
    if row.get('evaluation', {}).get('numeric_success') is False or support == 'unsupported':
        return False
    if row.get('evaluation', {}).get('numeric_success') is True and support == 'supported':
        return True
    return None


def _counts(rows):
    values = [r['code_and_support_proposal'] for r in rows if r.get('code_and_support_proposal') is not None]
    return {'records': len(rows), 'status_counts': dict(Counter(r['status'] for r in rows)),
            'support_label_counts': dict(Counter(r.get('proposal', {}).get('label','unjudged') for r in rows)),
            'code_and_support_proposal': {'true': sum(v is True for v in values), 'false': sum(v is False for v in values),
                'decidable_denominator': len(values), 'unknown': len(rows)-len(values),
                'rate_among_decidable': sum(v is True for v in values)/len(values) if values else None}}


def _summary(rows, calibration, planned):
    return {'schema_version': VERSION, 'rubric': RUBRIC, 'rubric_sha256': RUBRIC_SHA256,
            'calibration_eligible': calibration_ready(calibration), 'calibration_report_sha256': digest(calibration),
            'planned_risk_executions': planned, **_counts(rows),
            'by_variant': {str(v): _counts([r for r in rows if r.get('variant') == v]) for v in sorted({r.get('variant') for r in rows}, key=str)},
            'by_variant_repeat': {str(v)+':r'+str(n): _counts([r for r in rows if r.get('variant') == v and r.get('repeat') == n])
                for v,n in sorted({(r.get('variant'),r.get('repeat')) for r in rows}, key=str)},
            'human_reviewed': False, 'frozen_gold_task_success_modified': False,
            'qualitative_ground_truth_complete': False, 'rows': rows}


async def run_completion_semantics(cases, matrix_output, *, ledger, model_factory, calibration, output):
    """At most one versioned, non-retried request per delivered risk execution."""
    if ledger is None:
        raise ValueError('The existing approved shared ledger is required')
    root, folder = Path(matrix_output), Path(output)
    executions = _read(root/'Execution_Results.json')
    by_case = {c['case_id']: c for c in cases}
    if len(by_case) != len(cases):
        raise ValueError('Duplicate frozen case IDs')
    if len({r['execution_id'] for r in executions}) != len(executions):
        raise ValueError('Duplicate workflow execution IDs')
    targets = [r for r in executions if by_case[r['case_id']]['request']['include_risks']]
    identity = {'schema_version': VERSION, 'rubric_sha256': RUBRIC_SHA256,
                'calibration_sha256': digest(calibration), 'execution_results_sha256': digest(executions),
                'requests_sha256': digest({c['case_id']: c['request'] for c in cases})}
    _write_once(folder/'Semantic_Manifest.json', identity)
    rows = []; stopped = False
    for execution in targets:
        key = execution['execution_id']
        if not isinstance(key, str) or not _ID.fullmatch(key):
            raise ValueError('Unsafe workflow execution ID')
        row = {'execution_id': key, 'case_id': execution['case_id'], 'variant': execution.get('variant'),
               'repeat': execution.get('repeat'), 'workflow_result_sha256': digest(execution), 'rubric_sha256': RUBRIC_SHA256,
               'calibration_report_sha256': digest(calibration), 'judge_model': JUDGE_MODEL, 'human_reviewed': False,
               'status': 'unjudged', 'code_and_support_proposal': _proposal_success(execution),
               'frozen_gold_task_success': execution.get('evaluation', {}).get('task_success')}
        path = folder/'rows'/(key+'.json')
        judge_id = 'SEM_'+digest({'execution':key,'rubric':RUBRIC_SHA256})[:48]
        if path.exists():
            saved = _read(path)
            if any(saved.get(k) != row[k] for k in ('execution_id','case_id','variant','repeat','workflow_result_sha256','rubric_sha256','calibration_report_sha256','judge_model','human_reviewed','frozen_gold_task_success')):
                raise ValueError('Saved semantic proposal identity changed')
            if saved.get('status') == 'judged':
                if saved.get('attempted') is not True:
                    raise ValueError('Judged semantic output has no declared budget attempt')
                context = build_context(by_case[execution['case_id']], execution,
                    _read(root/'executions'/key/'Evidence.json'))
                if saved.get('input_context_sha256') != digest(context):
                    raise ValueError('Saved semantic evidence changed')
                response = saved.get('response')
                if not isinstance(response, dict) or digest(response) != saved.get('response_sha256') or response.get('model') != JUDGE_MODEL:
                    raise ValueError('Saved semantic response changed')
                parsed = validate_proposal(json.loads(response['content']))
                if parsed != saved.get('proposal') or saved.get('code_and_support_proposal') != _proposal_success(execution, parsed['label']):
                    raise ValueError('Saved semantic label differs from response')
                judgment = saved.get('support_judgment') or {}
                if (judgment.get('prompt_sha256') != PROMPT_SHA256 or judgment.get('response') != response
                        or judgment.get('label') != parsed['label'] or judgment.get('reason') != parsed['reason']
                        or judgment.get('status') != 'complete'):
                    raise ValueError('Saved support-only judgment metadata changed')
            if saved.get('attempted'):
                state = ledger.case_states().get(judge_id)
                if state is None:
                    raise ValueError('Saved semantic response has no original ledger attempt')
                if state == 'in_progress': ledger.finish_case(judge_id, saved['status'])
                elif state != saved['status']: raise ValueError('Semantic ledger attempt differs')
            rows.append(saved); continue
        if execution.get('status') != 'completed':
            row['status'] = 'unrun' if execution.get('status') in {'unrun','skipped'} else 'failed_delivery'
        elif not calibration_ready(calibration):
            row['status'] = 'pending_qualified_calibration'
        elif stopped:
            row['status'] = 'unrun_budget_stop'
        else:
            try:
                context = build_context(by_case[execution['case_id']], execution,
                    _read(root/'executions'/key/'Evidence.json'))
                row['input_context_sha256'] = digest(context)
                if judge_id in ledger.case_states():
                    row.update(status='interrupted_unevaluable', attempted=True, error_code='prior_attempt_has_no_saved_result')
                    if ledger.case_states()[judge_id] == 'in_progress': ledger.finish_case(judge_id, row['status'])
                    else: row['attempted'] = False  # Retain finalized attempt; never change or retry it.
                else:
                    if not ledger.begin_case(judge_id): raise WorkflowError('live_interrupted_case','A prior judge attempt will not be repeated')
                    row['attempted'] = True
                    item = support_item(context, key)
                    row['coverage_context'] = coverage_context(context)
                    async with asyncio.timeout(90):
                        judgment = await judge_support_item(item,
                            model_factory(judge_id, {'model':JUDGE_MODEL,'max_output_tokens':MAX_OUTPUT_TOKENS}))
                    row['support_judgment'] = judgment
                    if judgment.get('status') != 'complete':
                        raise WorkflowError(judgment.get('error_code','semantic_judge_error'), 'Factual-support judgment did not complete.')
                    response = judgment['response']
                    proposal = validate_proposal(json.loads(response['content']))
                    row.update(status='judged', proposal=proposal, response=response, response_sha256=digest(response),
                               code_and_support_proposal=_proposal_success(execution, proposal['label']))

            except Exception as exc:
                code = getattr(exc, 'code', 'semantic_input_or_judge_error')
                row.update(status='unevaluable', error_code=code if isinstance(code,str) and re.fullmatch('[a-zA-Z_]+',code) else 'semantic_error')
                stopped = code in BUDGET_CODES or str(code).startswith(('budget_','live_budget')) or code == 'reviewed_software_changed'
            _write_once(path, row)
            if row.get('attempted') and ledger.case_states().get(judge_id) == 'in_progress':
                ledger.finish_case(judge_id, row['status'])
        if not path.exists(): _write_once(path, row)
        rows.append(row)
    summary = _summary(rows, calibration, len(targets))
    _write_once(folder/'Semantic_Proposal_Report.json', summary)
    return summary
