"""Rebuilt run archive v1. Read partial runs without falling back to old evidence.

Hashes prove local consistency, not authenticity or official market approval.
This module uses only the standard library and never imports a solver.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

SCHEMA = 'gd_rebuilt_run/v1'
STAGES = ('inputs', 'market_da', 'market_rt', 'da_research_price',
          'rt_research_price', 'actual_hindsight')
STATES = frozenset({'not_started', 'running', 'completed', 'failed', 'interrupted',
                    'unavailable', 'blocked', 'not_requested'})
RUN_STATES = frozenset({'running', 'prepared', 'build_checked', 'completed', 'failed',
                        'interrupted', 'unavailable', 'contract_test_completed'})
REQUIRED_INPUT_FILES = ('config/base.yaml', 'config/units.yaml', 'config/modes.yaml',
    'data/case39/buses.csv', 'data/case39/branches.csv', 'resolved_bids.json', 'effective_profiles.csv')


class RunArchiveError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RunArchiveError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    def invalid(value):
        raise RunArchiveError('nonfinite JSON value: ' + value)
    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def no_symlinks(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink():
            raise RunArchiveError('symlink not allowed: ' + str(item))
    return path


def within(root, relative):
    root = no_symlinks(root)
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or any(p in {'..', '.'} for p in rel.parts):
        raise RunArchiveError('unsafe relative path: ' + str(relative))
    target = no_symlinks(root / rel)
    if not target.is_relative_to(root):
        raise RunArchiveError('path escapes run directory')
    return target


def atomic_json(path, value):
    """A reader sees the previous complete JSON or the new complete JSON."""
    path = no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(canonical(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class RunArchive:
    """One writer, exclusive new directory, durable stage transitions."""
    def __init__(self, root, *, config, requested, run_id=None):
        if config.get('execution_mode') == 'native_copt' or config.get('backend') != 'highs':
            raise RunArchiveError('new archives require HiGHS; historical COPT archives are read-only')
        root = no_symlinks(root)
        root.mkdir(parents=True, exist_ok=True)
        if run_id is None:
            run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ_') + uuid4().hex
        if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', run_id):
            raise RunArchiveError('run_id must be a safe 1..100 character identifier')
        self.path = within(root, run_id)
        self.path.mkdir(exist_ok=False)
        self.manifest = dict(schema=SCHEMA, reconstruction=True, run_id=run_id,
            created_at=now(), updated_at=now(), status='running', completed=False,
            native_numerical_acceptance=False, requested_stage_completion=False,
            full_day_market_rt=False, full_day_numerical_acceptance=False,
            config=deepcopy(config), config_sha256=digest(config),
            input_sha256=None, source_sha256=None, inputs=[], sources=[], outputs=[],
            selected_dispatch=None, stop_reason=None, events=[],
            stages={s: dict(status='not_started' if s in requested else 'not_requested',
                requested=s in requested, started_at=None, finished_at=None,
                reason=None, selected_dispatch_id=None, outputs=[]) for s in STAGES},
            limitations=[
                'Rebuilt from phase 4; not recovered original phase 5/6 code.',
                'Phase-4 research model retained; October 2026 rule adaptation is not certified.',
                'RT uses offline future-truth-conditioned curves, not causal online forecasts.',
                'Actual curves are synthetic and tie is fixed schedule, not telemetry.',
                'No automatic SCUC 5 MW reservation or infeasibility repair.',
                'Official LMP algorithm, real AGC delivery and full settlement are unavailable.',
                'Requested HiGHS gap .001 is not proof of exact optimum or strict lower bound.'])
        self.persist()

    def persist(self):
        self.manifest['updated_at'] = now()
        payload = deepcopy(self.manifest)
        payload['manifest_sha256'] = digest(payload)
        atomic_json(self.path / 'manifest.json', payload)

    def stage(self, name, status, *, reason=None):
        if name not in STAGES or status not in STATES:
            raise RunArchiveError('unknown stage or state')
        old = self.manifest['stages'][name]['status']
        if old in {'completed', 'failed', 'interrupted', 'unavailable'}:
            raise RunArchiveError('terminal stage cannot be rerun or overwritten')
        row = self.manifest['stages'][name]
        if status == 'running':
            if old != 'not_started':
                raise RunArchiveError('stage must begin from not_started')
            row['started_at'] = now()
        elif status == 'completed' and old != 'running':
            raise RunArchiveError('only a running stage can complete')
        row.update(status=status, reason=reason)
        if status not in {'not_started', 'running'}:
            row['finished_at'] = now()
        self.persist()

    def event(self, stage, event):
        self.manifest['events'].append(dict(stage=stage, at=now(), detail=deepcopy(event)))
        self.persist()

    def write(self, stage, name, value, *, role='evidence'):
        path = within(self.path, str(Path('stages') / stage / name))
        atomic_json(path, value)
        self.register(stage, path, role=role)
        return path

    def register(self, stage, path, *, role='evidence'):
        path = no_symlinks(path)
        relative = path.relative_to(self.path).as_posix()
        if stage not in STAGES or not relative.startswith('stages/' + stage + '/'):
            raise RunArchiveError('stage artifact outside its own directory')
        if not path.is_file():
            raise RunArchiveError('artifact is not a file')
        previous = next((x for x in self.manifest['outputs'] if x['path'] == relative), None)
        record = dict(path=relative, stage=stage, sha256=file_sha256(path), size=path.stat().st_size,
            role=role if role != 'evidence' or previous is None else previous['role'],
            run_id=self.manifest['run_id'], selected_dispatch_id=self.manifest['stages'][stage]['selected_dispatch_id'])
        self.manifest['outputs'] = [x for x in self.manifest['outputs'] if x['path'] != relative] + [record]
        self.manifest['stages'][stage]['outputs'] = sorted(x['path'] for x in self.manifest['outputs'] if x['stage'] == stage)
        self.persist()
        return record

    def collect(self, stage):
        target = within(self.path, 'stages/' + stage)
        if not target.exists():
            return
        for path in sorted(target.rglob('*')):
            no_symlinks(path)
            if path.is_file() and not path.name.endswith('.tmp'):
                self.register(stage, path)

    def verify_frozen(self):
        for kind in ('inputs', 'sources'):
            for record in self.manifest[kind]:
                path = no_symlinks(record['source_path'])
                if not path.is_file() or file_sha256(path) != record['sha256']:
                    raise RunArchiveError(kind + ' changed or missing: ' + str(path))
        for record in self.manifest['outputs']:
            path = within(self.path, record['path'])
            if not path.is_file() or file_sha256(path) != record['sha256']:
                raise RunArchiveError('registered output changed or missing: ' + record['path'])

    def finish(self, status, *, reason=None):
        complete = status == 'completed'
        full = complete and self.manifest['config']['execution_count'] == 96
        self.manifest.update(status=status, stop_reason=reason,
            completed=complete, requested_stage_completion=complete,
            native_numerical_acceptance=full, full_day_market_rt=full,
            full_day_numerical_acceptance=full)
        self.persist()


def read_run(run_dir):
    """Return a safe view; damage disables results, while valid partial runs remain readable.

    Never opens original source paths, searches other directories, or recovers
    values from another run. `verified` means archive integrity, not feasibility.
    """
    issues = []
    manifest = None
    root = Path(run_dir)
    try:
        root = no_symlinks(root)
        manifest = strict_json(no_symlinks(root / 'manifest.json').read_text(encoding='utf-8'))
        if not isinstance(manifest, dict):
            raise RunArchiveError('manifest must be an object')
        pending = [(manifest, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 64:
                raise RecursionError('manifest nesting exceeds safe reader limit')
            if isinstance(value, dict):
                pending.extend((child, depth + 1) for child in value.values())
            elif isinstance(value, list):
                pending.extend((child, depth + 1) for child in value)
        expected = manifest.pop('manifest_sha256')
        if expected != digest(manifest):
            raise RunArchiveError('manifest checksum mismatch')
        if manifest.get('schema') != SCHEMA or manifest.get('run_id') != root.name:
            raise RunArchiveError('schema or run directory identity mismatch')
        if not isinstance(manifest['config'], dict) or manifest['config_sha256'] != digest(manifest['config']):
            raise RunArchiveError('config checksum mismatch')
        config = manifest['config']
        if (config.get('execution_mode') not in {'prepare', 'build', 'native_copt', 'native_highs', 'contract_test'} or
                type(config.get('execution_count')) is not int or not 1 <= config['execution_count'] <= 96 or
                config.get('backend') not in {'copt', 'highs'} or config.get('threads') != 1 or
                config.get('requested_mip_rel_gap') != .001):
            raise RunArchiveError('invalid execution config')
        if config['execution_mode'].startswith('native_') and config['execution_mode'] != 'native_' + config['backend']:
            raise RunArchiveError('native execution mode/backend mismatch')
        if manifest.get('status') not in RUN_STATES:
            raise RunArchiveError('unknown run status')
        for field in ('completed', 'native_numerical_acceptance', 'requested_stage_completion',
                      'full_day_market_rt', 'full_day_numerical_acceptance'):
            if type(manifest.get(field)) is not bool:
                raise RunArchiveError('explicit Boolean required: ' + field)
        if (manifest['status'] == 'completed') != manifest['completed']:
            raise RunArchiveError('run status and completion flag disagree')
        if not isinstance(manifest['stages'], dict) or set(manifest['stages']) != set(STAGES):
            raise RunArchiveError('incomplete stage inventory')
        for stage in STAGES:
            if not isinstance(manifest['stages'][stage], dict) or manifest['stages'][stage]['status'] not in STATES:
                raise RunArchiveError('unknown stage status')
            if type(manifest['stages'][stage].get('requested')) is not bool:
                raise RunArchiveError('explicit stage requested Boolean required')
        for kind, hash_key in (('inputs', 'input_sha256'), ('sources', 'source_sha256')):
            records = manifest[kind]
            if not isinstance(records, list):
                raise RunArchiveError('invalid ' + kind + ' inventory')
            hashes = {}
            for record in records:
                if (not isinstance(record, dict) or not isinstance(record['name'], str) or not record['name'] or
                        record['name'] in hashes or not isinstance(record['sha256'], str) or
                        re.fullmatch(r'[0-9a-f]{64}', record['sha256']) is None):
                    raise RunArchiveError('invalid or duplicate ' + kind + ' identity')
                hashes[record['name']] = record['sha256']
            if manifest[hash_key] is not None and digest(hashes) != manifest[hash_key]:
                raise RunArchiveError(kind + ' aggregate checksum mismatch')
            if manifest['stages']['inputs']['status'] == 'completed' and manifest[hash_key] is None:
                raise RunArchiveError('completed inputs lack ' + hash_key)
        if not isinstance(manifest['outputs'], list):
            raise RunArchiveError('outputs must be an array')
        paths = set()
        for record in manifest['outputs']:
            if record['path'] in paths:
                raise RunArchiveError('duplicate output path')
            paths.add(record['path'])
            stage = record['stage']
            if stage not in STAGES or not record['path'].startswith('stages/' + stage + '/'):
                raise RunArchiveError('output stage path mismatch')
            if record['run_id'] != manifest['run_id']:
                raise RunArchiveError('cross-run output identity')
            path = within(root, record['path'])
            if not path.is_file() or path.stat().st_size != record['size'] or file_sha256(path) != record['sha256']:
                raise RunArchiveError('missing or damaged output: ' + record['path'])
        selected = manifest['selected_dispatch']
        if selected is not None:
            record = next((x for x in manifest['outputs'] if x['path'] == selected['path']), None)
            if (selected['run_id'] != manifest['run_id'] or record is None or
                    record['stage'] != 'market_da' or record['sha256'] != selected['sha256'] or
                    selected['input_sha256'] != manifest['input_sha256'] or
                    selected['source_sha256'] != manifest['source_sha256'] or
                    selected['dispatch_id'] != manifest['run_id'] + ':market_da:selected'):
                raise RunArchiveError('selected dispatch identity or checksum mismatch')
        if manifest['stages']['market_da']['status'] == 'completed' and (selected is None or
                manifest['stages']['market_da']['selected_dispatch_id'] != selected['dispatch_id']):
            raise RunArchiveError('completed DA requires this run selected dispatch binding')
        if manifest['stages']['inputs']['status'] == 'completed':
            if set(r['name'] for r in manifest['inputs']) != set(REQUIRED_INPUT_FILES) or not manifest['sources']:
                raise RunArchiveError('completed inputs require seven frozen files and implementation hashes')
            for source in manifest['inputs']:
                match = next((x for x in manifest['outputs'] if x['path'] == 'stages/inputs/frozen/' + source['name']), None)
                if match is None or match['sha256'] != source['sha256'] or match['role'] != 'frozen_input':
                    raise RunArchiveError('frozen input copy missing or hash mismatched')
        for stage in STAGES:
            row = manifest['stages'][stage]
            if sorted(row['outputs']) != sorted(x['path'] for x in manifest['outputs'] if x['stage'] == stage):
                raise RunArchiveError('stage output inventory mismatch')
            for record in (x for x in manifest['outputs'] if x['stage'] == stage):
                if record['selected_dispatch_id'] != row['selected_dispatch_id']:
                    raise RunArchiveError('output dispatch binding mismatch')
            summaries = [r for r in manifest['outputs'] if r['stage'] == stage and r['role'] == 'stage_summary']
            if row['status'] == 'completed' and (not row['requested'] or len(summaries) != 1):
                raise RunArchiveError('completed stage needs exactly one bound summary')
            for record in summaries:
                summary = strict_json(within(root, record['path']).read_text(encoding='utf-8'))
                if (not isinstance(summary, dict) or summary.get('schema') != 'gd_rebuilt_stage/v1' or
                        summary.get('run_id') != manifest['run_id'] or summary.get('stage') != stage or
                        summary.get('selected_dispatch_id') != row['selected_dispatch_id'] or
                        summary.get('input_sha256') != manifest['input_sha256'] or
                        summary.get('source_sha256') != manifest['source_sha256'] or
                        summary.get('execution_mode') != config['execution_mode'] or
                        not isinstance(summary.get('summary'), dict) or
                        summary.get('official_price', 'missing') is not None or
                        summary.get('settlement_cash', 'missing') is not None):
                    raise RunArchiveError('stage summary identity/scope mismatch')
        for stage in STAGES[2:]:
            row = manifest['stages'][stage]
            if row['status'] == 'completed' and (selected is None or row['selected_dispatch_id'] != selected['dispatch_id']
                    or manifest['stages']['market_da']['status'] != 'completed'):
                raise RunArchiveError('downstream selected dispatch identity mismatch')
        if manifest['completed']:
            if (manifest['status'] != 'completed' or manifest['config']['execution_mode'] not in {'native_copt', 'native_highs'} or
                    any(row['requested'] and row['status'] != 'completed' for row in manifest['stages'].values())):
                raise RunArchiveError('invalid completion claim')
        expected_full = manifest['completed'] and manifest['config']['execution_count'] == 96
        if (manifest['native_numerical_acceptance'] != expected_full or
                manifest['full_day_market_rt'] != expected_full or
                manifest['full_day_numerical_acceptance'] != expected_full or
                manifest['requested_stage_completion'] != manifest['completed']):
            raise RunArchiveError('inconsistent native acceptance claim')
    except RecursionError:
        issues.append('manifest nesting exceeds safe reader limit')
        manifest = None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
        issues.append(str(exc))
    verified = not issues
    if not isinstance(manifest, dict):
        manifest = None
    stages = {} if manifest is None or not isinstance(manifest.get('stages'), dict) else manifest['stages']
    native = verified and manifest['config']['execution_mode'] in {'native_copt', 'native_highs'}
    def available(stage):
        return bool(native and stages.get(stage, {}).get('status') == 'completed')
    capabilities = dict(market_da=available('market_da'), market_rt=available('market_rt'),
        da_research_price=available('da_research_price'), rt_research_price=available('rt_research_price'),
        actual_hindsight=available('actual_hindsight'), official_price=False, settlement_cash=False,
        real_agc_delivery=False, official_market=False)
    return dict(run_id=root.name, status=manifest.get('status') if verified else 'corrupt',
        verified=verified, completed=bool(verified and manifest['completed']),
        selected_dispatch=deepcopy(manifest.get('selected_dispatch')) if verified else None,
        stages=deepcopy(stages), outputs=deepcopy(manifest.get('outputs', [])) if verified else [],
        capabilities=capabilities, issues=issues, manifest=deepcopy(manifest))


def list_runs(root):
    root = no_symlinks(root)
    if not root.exists():
        return []
    return [read_run(path) for path in sorted(root.iterdir(), reverse=True)
            if path.is_dir() and not path.is_symlink() and (path / 'manifest.json').is_file()]


def read_artifact(run_dir, relative_path):
    """Read registered bytes only after all archive checks; no historical fallback."""
    view = read_run(run_dir)
    if not view['verified']:
        raise RunArchiveError('; '.join(view['issues']))
    if relative_path not in {x['path'] for x in view['outputs']}:
        raise RunArchiveError('artifact is not registered in this run')
    return within(run_dir, relative_path).read_bytes()
