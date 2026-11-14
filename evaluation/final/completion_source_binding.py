"""Verify full frozen SEC JSON content before binding current cache byte hashes.

No source is downloaded or rewritten. Only JSON presentation and a valid root
filings preparation timestamp may differ; every other value remains frozen.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

from workflow_contracts import WorkflowError, digest
from evaluation.final.source_binding import HASH_FIELDS, _answer_projection

CERTIFICATE_SHA256 = '2a429342887848bf1cf786df03d474ce18e6adad269aa1150dd65c247a756362'
HASH = re.compile(r'[a-f0-9]{64}\Z')
MAX_SOURCE_BYTES = 128 * 1024 * 1024

CANONICALIZATION = 'exact-decimal-typed-json-tree-v1'


def strict_json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON object key')
            value[key] = item
        return value
    def invalid_constant(value):
        raise ValueError('Non-finite JSON constant')
    value = json.loads(raw, object_pairs_hook=unique, parse_float=Decimal,
                       parse_constant=invalid_constant)
    # Also reject finite decimal tokens that overflow the deployed float parser.
    typed_tree(value)
    return value


def number_token(value):
    decimal = value if isinstance(value, Decimal) else Decimal(value)
    if not decimal.is_finite() or (isinstance(value, Decimal) and not math.isfinite(float(decimal))):
        raise ValueError('Non-finite or overflow JSON number')
    if decimal == 0:
        return '0'
    sign, digits, exponent = decimal.as_tuple()
    digits = list(digits)
    # No Decimal.normalize(): its ambient precision could round actual digits.
    while digits[-1] == 0:
        digits.pop(); exponent += 1
    return ('-' if sign else '') + ''.join(str(d) for d in digits) + 'e' + str(exponent)


def typed_tree(value):
    if value is None:
        return ['null']
    if type(value) is bool:
        return ['bool', value]
    if type(value) is str:
        return ['string', value]
    if type(value) is int or isinstance(value, Decimal):
        return ['number', number_token(value)]
    if type(value) is list:
        return ['array', [typed_tree(item) for item in value]]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError('Non-string JSON object key')
        return ['object', [[key, typed_tree(value[key])] for key in sorted(value)]]
    raise ValueError('Unsupported JSON value')


def validate_prepared_at(value):
    if not isinstance(value, str):
        raise ValueError('prepared_at must remain a present string timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('prepared_at must have a timezone')


def canonical_sha256(value, kind):
    if not isinstance(value, dict) or kind not in {'companyfacts','filings'}:
        raise ValueError('Expected a recognised root JSON object')
    projected = dict(value)
    if kind == 'filings':
        validate_prepared_at(projected['prepared_at'])
        del projected['prepared_at']
    raw = json.dumps(typed_tree(projected), ensure_ascii=False,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def fail(code, message):
    raise WorkflowError(code, message)


def safe_read(path, limit=MAX_SOURCE_BYTES):
    """Read one bounded regular file without following links or changed inodes."""
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('unsafe_path')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        raise ValueError('missing_file') from None
    except OSError:
        raise ValueError('unreadable_file') from None
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('unsafe_path')
        if before.st_size > limit:
            raise ValueError('oversized')
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        current = path.stat(follow_symlinks=False)
        if len(raw) > limit:
            raise ValueError('oversized')
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino)):
            raise ValueError('changed_during_read')
    return raw


def _write(path, value, *, immutable=False):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        fail('source_binding_identity', 'The completion source report path is unsafe.')
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n').encode()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if immutable:
        if path.exists():
            if strict_json(safe_read(path, 4*1024*1024)) != value:
                fail('source_binding_identity', 'The saved completion binding differs; no source binding or allowance was reset.')
            return
        fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        return
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix='.source-diagnostics-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _certificate(raw, manifest, cases, *, frozen_bundle_sha256, original_freeze_manifest_sha256,
                 expected_certificate_sha256):
    if hashlib.sha256(raw).hexdigest() != expected_certificate_sha256:
        fail('source_equivalence_proof', 'The full source-equivalence certificate failed its exact hash check.')
    try:
        proof = strict_json(raw)
        tickers = sorted({ticker for case in cases for ticker in case['request']['tickers']})
        required = {f'data/raw/{ticker}_{kind}.json' for ticker in tickers for kind in HASH_FIELDS.values()}
        rows = proof['files']
        if (proof['schema_version'] != 'completion-source-equivalence-v1'
                or proof['frozen_bundle_sha256'] != frozen_bundle_sha256
                or proof['original_freeze_manifest_sha256'] != original_freeze_manifest_sha256
                or proof['frozen_authoring_code_sha256'] != manifest['authoring_code_sha256']
                or proof['canonicalization']['name'] != CANONICALIZATION
                or sorted(proof['case_ids']) != sorted(c['case_id'] for c in cases)
                or set(proof['allowed_bound_gold_changes']) != set(HASH_FIELDS)
                or len(rows) != len(required) or {r['relative_path'] for r in rows} != required):
            raise ValueError()
        for row in rows:
            ticker, kind, name = row['ticker'], row['kind'], row['relative_path']
            if (ticker not in tickers or kind not in HASH_FIELDS.values()
                    or name != f'data/raw/{ticker}_{kind}.json'
                    or not HASH.fullmatch(row['canonical_sha256'])
                    or not HASH.fullmatch(row['frozen_raw_sha256'])
                    or row['frozen_raw_sha256'] != manifest['source_hashes']['sec_source_snapshot/'+Path(name).name]
                    or row['ignored_root_fields'] != (['prepared_at'] if kind == 'filings' else [])
                    or row['prepared_at_policy'] != ({'required':True, 'type':'string', 'format':'timezone_aware_iso8601'} if kind == 'filings' else None)):
                raise ValueError()
        for name, expected in proof['year_evidence_sha256'].items():
            if (not HASH.fullmatch(expected) or name != 'data/raw/sec_fiscal_years/'+expected+'.html'
                    or manifest['source_hashes'].get('sec_source_snapshot/'+name.removeprefix('data/raw/')) != expected):
                raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        fail('source_equivalence_proof', 'The full source-equivalence proof differs from the original frozen scope or manifest.')
    return proof


def bind_completion_sources(repo, manifest, cases, gold, verified_pilot_binding, *,
        certificate_path, binding_path, diagnostics_path, frozen_bundle_sha256,
        original_freeze_manifest_sha256, require_existing=False,
        expected_certificate_sha256=CERTIFICATE_SHA256):
    """Prove all complete current files equivalent, then bind only raw hashes.

    Pilot aliases already used by paid S40 results must retain their exact bytes.
    Completion resumes likewise require the exact prior complete actual-hash map.
    """
    try:
        proof = _certificate(safe_read(certificate_path, 1024*1024), manifest, cases,
            frozen_bundle_sha256=frozen_bundle_sha256,
            original_freeze_manifest_sha256=original_freeze_manifest_sha256,
            expected_certificate_sha256=expected_certificate_sha256)
    except (WorkflowError, ValueError, OSError):
        _write(diagnostics_path, {'schema_version':'source-cache-diagnostics-v1',
            'status':'certificate_rejected', 'certificate_sha256':expected_certificate_sha256,
            'sources_checked':0, 'files':[], 'source_files_modified':False})
        raise
    rows, actual, originals = [], {}, {}
    for reference in sorted(proof['files'], key=lambda row:row['relative_path']):
        name, kind = reference['relative_path'], reference['kind']
        row = {'relative_path':name, 'kind':kind,
            'frozen_raw_sha256':reference['frozen_raw_sha256'],
            'expected_canonical_sha256':reference['canonical_sha256']}
        originals[name] = reference['frozen_raw_sha256']
        try:
            raw = safe_read(Path(repo)/name)
            row['actual_raw_sha256'] = hashlib.sha256(raw).hexdigest()
            try:
                value = strict_json(raw)
            except (ValueError, TypeError, OverflowError, RecursionError):
                raise ValueError('invalid_json') from None
            if not isinstance(value, dict):
                raise ValueError('invalid_root_type')
            if kind == 'filings':
                try: validate_prepared_at(value['prepared_at'])
                except (KeyError, ValueError, TypeError, OverflowError):
                    raise ValueError('prepared_at_invalid') from None
            computed = canonical_sha256(value, kind)
            row['actual_canonical_sha256'] = computed
            if computed != reference['canonical_sha256']:
                raise ValueError('canonical_mismatch')
            row['status'] = 'exact' if row['actual_raw_sha256'] == reference['frozen_raw_sha256'] else 'format_or_timestamp_variant'
            actual[name] = row['actual_raw_sha256']
        except (ValueError, OSError) as exc:
            allowed = {'missing_file','unsafe_path','unreadable_file','oversized','changed_during_read',
                'invalid_json','invalid_root_type','prepared_at_invalid','canonical_mismatch'}
            row['status'] = str(exc) if str(exc) in allowed else 'unreadable_file'
        rows.append(row)
    year_rows = []
    for name, expected in sorted(proof['year_evidence_sha256'].items()):
        row = {'relative_path':name, 'frozen_raw_sha256':expected}
        try:
            current = hashlib.sha256(safe_read(Path(repo)/name)).hexdigest()
            row['actual_raw_sha256'] = current
            row['status'] = 'exact' if current == expected else 'raw_hash_mismatch'
            if current == expected: actual[name] = current
        except (ValueError, OSError) as exc:
            row['status'] = str(exc) if str(exc) in {'missing_file','unsafe_path','unreadable_file','oversized','changed_during_read'} else 'unreadable_file'
        year_rows.append(row)
    accepted = {'exact','format_or_timestamp_variant'}
    failed = [r for r in rows+year_rows if r['status'] not in accepted]
    diagnostics = {'schema_version':'source-cache-diagnostics-v1',
        'status':'rejected' if failed else 'verified', 'certificate_sha256':expected_certificate_sha256,
        'canonicalization':CANONICALIZATION, 'sources_checked':len(rows), 'year_html_checked':len(year_rows),
        'mismatch_count':len(failed), 'files':rows, 'year_evidence':year_rows,
        'source_files_modified':False,
        'scope':'Complete JSON equality for every companyfacts and filings file; only root filings prepared_at timestamp value may vary. Original year HTML remains byte exact.'}
    _write(diagnostics_path, diagnostics)
    if failed:
        names=', '.join(r['relative_path'] for r in failed)
        fail('frozen_source_mismatch', f'{len(failed)} source cache file(s) failed full frozen-content verification: '+names+'. See Source_Cache_Diagnostics.json. Nothing was downloaded or overwritten.')
    for row in verified_pilot_binding['files']:
        name = row['relative_path']
        if actual.get(name) != row['actual_sha256'] or originals.get(name) != row['frozen_sha256']:
            fail('source_binding_identity', 'An existing paid pilot source binding changed; the prior binding and allowance were preserved.')
    for case in gold:
        for target in case['numeric_targets']:
            for metric in ('revenue','operating_income'):
                for field, kind in HASH_FIELDS.items():
                    name = f"data/raw/{target['ticker']}_{kind}.json"
                    if name not in actual or target[metric].get(field) != originals.get(name):
                        fail('source_binding_gold', 'A frozen target source identity differs from the original manifest.')
    bound = deepcopy(gold)
    for case in bound:
        for target in case['numeric_targets']:
            for metric in ('revenue','operating_income'):
                for field, kind in HASH_FIELDS.items():
                    target[metric][field] = actual[f"data/raw/{target['ticker']}_{kind}.json"]
    if _answer_projection(bound) != _answer_projection(gold):
        fail('source_binding_gold', 'A frozen answer or non-hash provenance changed during binding.')
    binding = {'schema_version':'completion-source-binding-v2',
        'frozen_bundle_sha256':frozen_bundle_sha256,
        'original_freeze_manifest_sha256':original_freeze_manifest_sha256,
        'certificate_sha256':expected_certificate_sha256,
        'canonicalization':CANONICALIZATION,
        'pilot_binding_sha256':verified_pilot_binding['binding_sha256'],
        'cases_sha256':digest(cases), 'original_gold_sha256':digest(gold), 'bound_gold_sha256':digest(bound),
        'expected_answers_unchanged':True, 'only_rebound_fields':sorted(HASH_FIELDS),
        'source_hashes':actual, 'files':rows, 'year_evidence_sha256':proof['year_evidence_sha256'],
        'scope':'All frozen answers, complete SEC facts and filing rows, array order and non-hash provenance remain unchanged; actual byte hashes are bound after full canonical proof.'}
    binding['binding_sha256'] = digest(binding)
    if require_existing and not Path(binding_path).exists():
        fail('source_binding_identity', 'Completion has prior approved scope or results but its source binding is missing; no binding or allowance was reset.')
    _write(binding_path, binding, immutable=True)
    return bound, binding
