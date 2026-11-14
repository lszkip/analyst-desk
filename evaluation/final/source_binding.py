"""Bind only independently reviewed cache byte variants to frozen pilot targets.

This is an exact allowlist, not a general semantic-cache compatibility policy.
Neither the original gold nor any user's SEC cache file is modified.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re

from workflow_contracts import WorkflowError, digest

VERIFIED_EQUIVALENCE_SHA256 = 'dcf3cf73161b1631a71a5e868d86483e5d4bade921d5b3158c74fb7ce149b1db'
HASH_FIELDS = {'snapshot_sha256': 'companyfacts', 'filings_snapshot_sha256': 'filings'}


def _fail(code, message):
    raise WorkflowError(code, message)


def _read(path, limit=128*1024*1024):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file() or path.stat().st_size > limit:
        _fail('source_binding_input', 'A required source-binding input is missing, unsafe or oversized: '+path.name+'.')
    return path.read_bytes()


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON keys are not permitted')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Non-finite JSON values are not permitted')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)


def _canonical_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _answer_projection(gold):
    result = deepcopy(gold)
    for case in result:
        for target in case['numeric_targets']:
            for metric in ('revenue', 'operating_income'):
                for field in HASH_FIELDS:
                    target[metric].pop(field, None)
    return result


def bind_sources(repo, manifest, cases, gold, certificate_path, binding_path, *,
                 frozen_bundle_sha256, original_freeze_manifest_sha256,
                 expected_binding_sha256=None, require_existing=False,
                 expected_certificate_sha256=VERIFIED_EQUIVALENCE_SHA256):
    certificate_raw = _read(certificate_path, 1024*1024)
    certificate_sha = hashlib.sha256(certificate_raw).hexdigest()
    if certificate_sha != expected_certificate_sha256:
        _fail('source_equivalence_proof', 'The reviewed source-equivalence certificate failed its exact hash check.')
    certificate = _json(certificate_raw)
    if (certificate.get('schema_version') != 'verified-source-equivalence-v1'
            or certificate.get('frozen_bundle_sha256') != frozen_bundle_sha256
            or certificate.get('original_freeze_manifest_sha256') != original_freeze_manifest_sha256
            or certificate.get('frozen_authoring_code_sha256') != manifest.get('authoring_code_sha256')
            or set(certificate.get('pilot_case_ids', [])) != {c['case_id'] for c in cases}
            or certificate.get('outcome_equivalence', {}).get('all_equal_except_snapshot_fields') is not True
            or set(certificate.get('outcome_equivalence', {}).get('ignored_fact_fields', [])) != set(HASH_FIELDS)):
        _fail('source_equivalence_proof', 'The reviewed equivalence proof belongs to a different frozen pilot or comparison scope.')
    tickers = sorted({t for case in cases for t in case['request']['tickers']})
    required = {f'data/raw/{ticker}_{kind}.json' for ticker in tickers for kind in HASH_FIELDS.values()}
    rows = certificate.get('files', [])
    if len(rows) != len(required) or {r.get('relative_path') for r in rows} != required:
        _fail('source_equivalence_proof', 'The reviewed equivalence proof does not contain exactly the required pilot source files.')
    actual, source_rows, histories = {}, [], []
    for row in sorted(rows, key=lambda r: r['relative_path']):
        name, ticker, kind = row['relative_path'], row['ticker'], row['kind']
        if name != f'data/raw/{ticker}_{kind}.json' or ticker not in tickers or kind not in HASH_FIELDS.values():
            _fail('source_equivalence_proof', 'Invalid reviewed source identity.')
        frozen = manifest.get('source_hashes', {}).get('sec_source_snapshot/'+Path(name).name)
        if not re.fullmatch(r'[a-f0-9]{64}', str(frozen)) or row.get('frozen_raw_sha256') != frozen:
            _fail('source_equivalence_proof', 'A reviewed original source hash differs from the frozen manifest.')
        raw = _read(Path(repo)/name)
        current = hashlib.sha256(raw).hexdigest()
        if current not in {frozen, row.get('current_raw_sha256')}:
            _fail('frozen_source_mismatch', 'SEC cache bytes are outside the two reviewed variants: '+name+'. Nothing was downloaded or overwritten.')
        value = _json(raw)
        ignored = [] if kind == 'companyfacts' else ['prepared_at']
        if row.get('ignored_root_fields') != ignored:
            _fail('source_equivalence_proof', 'The source comparison would ignore an unapproved field.')
        if kind == 'filings':
            histories.append(value)
        projected = dict(value)
        for field in ignored:
            projected.pop(field, None)
        if _canonical_hash(projected) != row.get('canonical_sha256'):
            _fail('source_equivalence_proof', 'Reviewed canonical source content failed verification: '+name+'.')
        actual[(ticker, kind)] = current
        source_rows.append({'relative_path': name, 'frozen_sha256': frozen, 'actual_sha256': current,
                            'canonical_sha256': row['canonical_sha256'], 'ignored_root_fields': ignored})
    # Preserve exact original-filing byte checks for every cached year correction.
    evidence_hashes = {}
    for history in histories:
        for filing in history.get('annual_filings', []):
            evidence = filing.get('fiscal_year_evidence')
            if evidence:
                source_hash = evidence.get('source_sha256', '')
                if not re.fullmatch(r'[a-f0-9]{64}', source_hash):
                    _fail('source_binding_input', 'Invalid cached original-filing fingerprint.')
                name = 'sec_fiscal_years/'+source_hash+'.html'
                expected = manifest.get('source_hashes', {}).get('sec_source_snapshot/'+name)
                current = hashlib.sha256(_read(Path(repo)/'data/raw'/name)).hexdigest()
                if current != source_hash or current != expected:
                    _fail('frozen_source_mismatch', 'An original-filing year-evidence source differs from the frozen source.')
                evidence_hashes['data/raw/'+name] = current
    for case in gold:
        for target in case['numeric_targets']:
            ticker = target['ticker']
            for metric in ('revenue', 'operating_income'):
                for field, kind in HASH_FIELDS.items():
                    frozen = manifest['source_hashes'].get('sec_source_snapshot/'+ticker+'_'+kind+'.json')
                    if target[metric].get(field) != frozen or (ticker, kind) not in actual:
                        _fail('source_binding_gold', 'A frozen target source identity does not match the original manifest.')
    bound_gold = deepcopy(gold)
    for case in bound_gold:
        for target in case['numeric_targets']:
            for metric in ('revenue', 'operating_income'):
                for field, kind in HASH_FIELDS.items():
                    target[metric][field] = actual[(target['ticker'], kind)]
    if _answer_projection(bound_gold) != _answer_projection(gold):
        _fail('source_binding_gold', 'Binding attempted to change a frozen answer or provenance field.')
    binding = {'schema_version': 'pilot-source-binding-v1', 'frozen_bundle_sha256': frozen_bundle_sha256,
        'original_freeze_manifest_sha256': original_freeze_manifest_sha256,
        'certificate_sha256': certificate_sha, 'cases_sha256': digest(cases),
        'original_gold_sha256': digest(gold), 'bound_gold_sha256': digest(bound_gold),
        'expected_answers_unchanged': True, 'only_rebound_fields': sorted(HASH_FIELDS),
        'files': source_rows, 'year_evidence_sha256': evidence_hashes}
    binding['binding_sha256'] = digest(binding)
    binding_path = Path(binding_path).absolute()
    if any(p.is_symlink() for p in (binding_path, *binding_path.parents)):
        _fail('source_binding_identity', 'The saved source-binding path is unsafe.')
    if expected_binding_sha256 is not None and expected_binding_sha256 != binding['binding_sha256']:
        _fail('source_binding_identity', 'The registered source binding changed; existing allowance and results were preserved.')
    if binding_path.exists():
        if _json(_read(binding_path, 1024*1024)) != binding:
            _fail('source_binding_identity', 'The saved source binding differs; no automatic rebinding is permitted.')
    else:
        if require_existing or expected_binding_sha256 is not None:
            _fail('source_binding_identity', 'The existing pilot source binding is missing; no binding or allowance was reset.')
        binding_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw = (json.dumps(binding, indent=2, sort_keys=True, allow_nan=False)+'\n').encode()
        fd = os.open(binding_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return bound_gold, {'status': 'verified', **binding,
        'scope': 'Exact independently reviewed bytes only; all frozen answers, selected facts, filing rows and non-hash provenance remain unchanged. Actual snapshot hashes are recorded explicitly.'}
