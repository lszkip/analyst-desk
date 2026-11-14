"""Offline historical-publication memory assay; never a production import API.

The archived portal receipt establishes only its recorded publication state.
It does not establish current source truth or modern sentence-level verification.
All writes use a newly created temporary AnalystMemory database, then delete it.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import re
import tempfile
import json

from research_portal import ResearchNote, note_hash
from workflow_contracts import WorkflowError, canonical, digest
from workflow_memory import AnalystMemory, MEMORY_VERSION, deterministic_memory_comparison

HISTORICAL_STATUS='historical_publication_confirmed_not_current_source_reverified'
_ID=re.compile(r'[A-Za-z0-9_-]{1,100}\Z')
SYNTHETIC_CANARY='SYNTHETIC_PRIVATE_WATCHLIST_MEMORY_ASSAY_ONLY'


def verify_historical_publication(binding, receipt):
    note=ResearchNote.model_validate(binding.get('note'))
    content_hash=note_hash(note)
    if binding.get('content_hash')!=content_hash or receipt.get('content_hash')!=content_hash:
        raise ValueError('Archived note, approval and publication content hashes differ')
    if (receipt.get('published') is not True or receipt.get('portal_state')!='published'
        or receipt.get('two_reads_identical') is not True):
        raise ValueError('A captured confirmed published receipt is required')
    for key in ('proposal_id','note_id'):
        if not isinstance(receipt.get(key),str) or not _ID.fullmatch(receipt[key]):raise ValueError('Invalid archived publication identity')
    if binding.get('proposal_id')!=receipt['proposal_id']:
        raise ValueError('Approval and publication refer to different proposals')
    if 'note_id' in binding and binding['note_id']!=receipt['note_id']:
        raise ValueError('Archived note identity differs')
    if (type(binding.get('revision')) is not int or binding['revision']<1
        or type(receipt.get('revision')) is not int or binding['revision']!=receipt['revision']):
        raise ValueError('Approval and publication revisions differ')
    published_at=receipt.get('published_at')
    if type(published_at) not in (int,float) or not math.isfinite(published_at) or published_at<=0:
        raise ValueError('Missing archived publication time')
    if not isinstance(receipt.get('reviewer'),str) or not receipt['reviewer'].strip():
        raise ValueError('The archived publication receipt lacks its reviewer field')
    run_id=binding.get('run_id')
    if not isinstance(run_id,str) or not _ID.fullmatch(run_id):raise ValueError('Invalid archived run identity')
    binding_hash=binding.get('binding_sha256')
    if binding_hash is not None and binding_hash!=digest({k:v for k,v in binding.items() if k!='binding_sha256'}):
        raise ValueError('The archived approval binding changed')
    return {'note':note.model_dump(),'content_hash':content_hash,'note_id':receipt['note_id'],
        'proposal_id':receipt['proposal_id'],'revision':receipt['revision'],'run_id':run_id,
        'binding_hash_verified':binding_hash is not None,'binding_sha256':binding_hash,
        'receipt_sha256':digest(receipt),'status':HISTORICAL_STATUS}


def _store_for_assay(memory, verified):
    """Private evaluator adapter, deliberately bypassing no production receipt check.

    It stores a distinctly marked historical snapshot using the low-level owner
    store. Production record_published remains unchanged and is never invoked.
    """
    note=verified['note'];key=verified['note_id']
    value={'version':MEMORY_VERSION,'note_id':key,'run_id':verified['run_id'],
        'content_hash':verified['content_hash'],'note':deepcopy(note),
        'request':{'tickers':note['tickers'],'years':note['fiscal_years']},
        'memory_status':HISTORICAL_STATUS,'historical_publication_receipt_sha256':verified['receipt_sha256'],
        'historical_approval_binding_sha256':verified['binding_sha256'],
        'current_source_reverified':False,'modern_sentence_receipts_verified':False}
    memory.storage.put('published_notes',key,value)
    memory.storage.put('coverage',key,{'note_id':key,'tickers':note['tickers'],'years':note['fiscal_years'],
        'content_hash':verified['content_hash'],'scope':'historical_note_request_only_not_complete_filing_coverage',
        'current_source_reverified':False})
    return value


def build_memory_comparison_report(binding, receipt, *, workspace=None):
    verified=verify_historical_publication(binding,receipt)
    note=verified['note']
    request={'question':'Identify the prior published note for these companies and fiscal periods and compare the supplied source inventory.',
             'tickers':deepcopy(note['tickers']),'years':deepcopy(note['fiscal_years']),'include_risks':True}
    # These are an exact historical inventory replay, not newly fetched sources.
    sources=deepcopy(note['citations'])
    before_binding=digest(binding);before_receipt=digest(receipt)
    with tempfile.TemporaryDirectory(prefix='historical_memory_assay_',dir=workspace) as directory:
        memory=AnalystMemory(Path(directory)/'historical_memory_assay.sqlite','offline_historical_note_assay',enabled=True)
        stored=_store_for_assay(memory,verified)
        memory.set_watchlist('synthetic_privacy_check',SYNTHETIC_CANARY,'Synthetic fixture only; never real user data.')
        comparison=deterministic_memory_comparison(memory,request,sources)
        privacy=[]
        for enabled in (False,True):
            memory.enabled=enabled
            try:
                memory.guard_external({'text':SYNTHETIC_CANARY},request['tickers'])
                blocked=False
            except WorkflowError as exc:
                if exc.code!='private_memory_exfiltration':raise
                blocked=True
            privacy.append({'memory_enabled':enabled,'synthetic_watchlist_literal_blocked':blocked,'external_dispatches':0})
        off,on=comparison['runs']
        history=on['context']['past_notes']
        context_visible=any(row['note_id']==verified['note_id'] for row in history)
        followup_visible=on['followup'].get('previous_note',{}).get('note_id')==verified['note_id']
        status_preserved=all(row.get('status')==HISTORICAL_STATUS for row in history)
        no_private_context=all(SYNTHETIC_CANARY not in canonical(run) for run in comparison['runs'])
        no_modern_claim='support_version' not in stored and 'verification' not in stored
    assert digest(binding)==before_binding and digest(receipt)==before_receipt
    return {'title':'Historical Google and Meta note — deterministic memory OFF/ON comparison',
        'comparison_type':'actual_archived_note_deterministic_memory_comparison','model_calls':0,'network_calls':0,
        'live_model_quality_comparison':False,'historical_verification':{k:v for k,v in verified.items() if k!='note'},
        'historical_tickers':note['tickers'],'historical_fiscal_years':note['fiscal_years'],
        'historical_citation_count':len(sources),'request':request,
        'source_inventory_mode':'exact_historical_citation_replay_not_current_retrieval',
        'same_request_and_source_inventory_both_arms':True,'comparison':comparison,
        'memory_off_prior_note_absent':not off['context']['past_notes'] and off['followup']['status']=='no_prior_note',
        'memory_on_prior_note_visible_in_context':context_visible,'memory_on_prior_note_visible_in_inventory_followup':followup_visible,
        'context_limit_characters':6000,'context_truncated':on['context']['truncated'],
        'context_size_limitation':'Full original citation inventory did not fit in the default context; no compacted substitute was invented.' if not context_visible else None,
        'historical_status_preserved':status_preserved,'modern_sentence_verification_not_invented':no_modern_claim,
        'synthetic_privacy_checks':privacy,'synthetic_watchlist_not_exposed':no_private_context,
        'temporary_database_deleted':True,'original_binding_and_receipt_unchanged':True,
        'original_databases_opened_or_modified':False,'human_review_performed_by_this_assay':False,
        'limitations':['Archived approval/publication receipts are checked locally; the portal is not queried again.',
            'The old note and its original receipt are not rewritten or upgraded to current sentence-verification rules.',
            'Memory restores prior note identity, scope and source inventory. It supplies no new financial evidence or risk-change conclusion.',
            'Identical replayed citation lists produce no inventory delta; this does not prove current filings or business risks are unchanged.',
            'The watchlist entry is artificial and tests a literal local privacy guard only. No task-quality or LLM improvement is measured.']}


def report_from_files(binding_path,receipt_path,*,workspace=None):
    paths=[Path(binding_path),Path(receipt_path)]
    original=[p.read_bytes() for p in paths]
    report=build_memory_comparison_report(json.loads(original[0]),json.loads(original[1]),workspace=workspace)
    if any(p.read_bytes()!=data for p,data in zip(paths,original)):raise ValueError('Historical evidence file changed during the assay')
    report['input_files_sha256']={p.name:hashlib.sha256(data).hexdigest() for p,data in zip(paths,original)}
    return report
