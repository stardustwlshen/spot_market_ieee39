"""Read-only, stdlib-only day archives. No typed codec or native receipt loading.

Integrity means byte consistency, never restored solver authority or authenticity.
Nothing is cached and nothing executes, writes, or falls back to another run.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path, PurePosixPath

SCHEMA = 'gd.october.day.archive.v1'
MAX_BYTES = 128 * 1024 * 1024

class DayArchiveError(ValueError):
    pass


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DayArchiveError('duplicate JSON key: ' + key)
        value[key] = item
    return value


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise DayArchiveError('nonfinite JSON value')
    if isinstance(value, dict):
        for item in value.values(): _finite(item)
    if isinstance(value, list):
        for item in value: _finite(item)


def decode_data(raw):
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
    _finite(value)
    return value


def _safe(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise DayArchiveError('symlink forbidden: ' + str(path))
    return path


def _relative(name):
    if not isinstance(name, str) or not name or '\\' in name:
        raise DayArchiveError('invalid archive path')
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or str(p) != name or ':' in name:
        raise DayArchiveError('unsafe archive path: ' + name)
    return p


def _bytes(path):
    path = _safe(path)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise DayArchiveError('missing or oversized regular file: ' + str(path))
    return path.read_bytes()


def list_day_runs(root):
    root = _safe(root)
    if not root.exists(): return []
    if not root.is_dir(): raise DayArchiveError('run root is not a directory')
    # Never load one run as another run's replacement, including corrupt entries.
    return sorted(p.name for p in root.iterdir() if p.is_dir() or p.is_symlink())


def _read_day_run(directory):
    """Read one exact directory; corrupt finished archives expose no payloads.

    Missing manifests retain report-only partial context, explicitly unverified.
    """
    view = dict(verified=False, integrity='unverified_partial', issues=[], report={},
                artifacts={}, full_day_completed=False, full_day_native_accepted=False)
    try:
        root = _safe(directory)
        if not root.is_dir(): raise DayArchiveError('run directory unavailable')
        files = {}
        for path in root.rglob('*'):
            _safe(path)
            if path.is_file(): files[path.relative_to(root).as_posix()] = path
        if 'report.json' not in files:
            view['issues'].append('No report yet; no completion or numerical result available.')
            return view
        report_bytes = _bytes(files['report.json'])
        report = decode_data(report_bytes)
        if not isinstance(report, dict) or report.get('schema') != SCHEMA:
            raise DayArchiveError('unknown report schema')
        view['report'] = report
        if 'manifest.json' not in files:
            view['issues'].append('Manifest missing: partial snapshot only; integrity and completion unverified.')
            return view
        manifest_bytes = _bytes(files['manifest.json'])
        manifest = decode_data(manifest_bytes)
        if not isinstance(manifest, dict) or set(manifest) != {'schema', 'files'} or manifest['schema'] != SCHEMA:
            raise DayArchiveError('unknown manifest schema')
        inventory = manifest['files']
        if not isinstance(inventory, dict) or 'report.json' not in inventory:
            raise DayArchiveError('manifest lacks report binding')
        for name in inventory: _relative(name)
        if set(files) != set(inventory) | {'manifest.json'}:
            raise DayArchiveError('archive file inventory mismatch')
        artifacts = {}
        for name, expected in inventory.items():
            raw = _bytes(root / name)
            if (not isinstance(expected, dict) or type(expected.get('size')) is not int
                    or expected != dict(size=len(raw), sha256=sha256(raw).hexdigest())):
                raise DayArchiveError('archive hash mismatch: ' + name)
            if name.endswith('.json'):
                artifacts[name] = decode_data(raw)
        # Refresh race guard: never certify a mixture of checkpoints.
        if _bytes(root / 'manifest.json') != manifest_bytes or _bytes(root / 'report.json') != report_bytes:
            raise DayArchiveError('archive changed during read; refresh required')
        if any(not isinstance(value, dict) for name, value in artifacts.items() if name.startswith(('windows/', 'events/'))):
            raise DayArchiveError('window/event JSON object required')
        view['artifacts'] = artifacts
        if report.get('status') in ('running', 'interrupted'):
            view['issues'].append('Running/interrupted snapshot is partial, not a finished integrity certificate.')
            return view
        full = _validate_binding(report, artifacts, inventory)
        view['full_day_completed'] = full
        view['full_day_native_accepted'] = full and report.get('full_day_native_accepted') is True
        view['verified'] = True
        view['integrity'] = 'hashes_verified_only'
        return view
    except (ValueError, OSError, TypeError, KeyError, UnicodeError, AttributeError, OverflowError, RecursionError, IndexError) as exc:
        view.update(verified=False, integrity='corrupt', artifacts={}, full_day_completed=False,
                    full_day_native_accepted=False)
        view['issues'].append(str(exc))
        return view


def _time(value):
    d = datetime.fromisoformat(value)
    if d.tzinfo is None: raise DayArchiveError('aware timestamp required')
    return d


def _validate_binding(report, artifacts, inventory):
    """Data consistency, not receipt validation or physical feasibility."""
    if report.get('status') not in {'completed', 'failed', 'running', 'interrupted'}:
        raise DayArchiveError('unknown status')
    def research(value):
        if isinstance(value,str):return value=='gd.october.research_thermal_trajectories.v1'
        if isinstance(value,dict):return any(research(v) for v in value.values())
        if isinstance(value,list):return any(research(v) for v in value)
        return False
    if research(artifacts.get('job.original.json')) and report.get('research_thermal_assumptions') is not True:
        raise DayArchiveError('research thermal provenance removed from report')
    if report.get('operational_source_verified') is True or report.get('official_market_accepted') is True:
        raise DayArchiveError('numerical archive cannot certify operational/official source authority')
    counts = ['requested_window_count', 'prepared_window_count', 'built_window_count',
              'solved_window_count', 'completed_window_count', 'confirmed_step_count']
    for k in counts:
        if type(report.get(k)) is not int or report[k] < 0: raise DayArchiveError('invalid count: ' + k)
    requested, prepared, built, solved, completed, confirmed = (report[k] for k in counts)
    if not 0 <= completed <= solved <= built <= prepared <= requested <= 96 or confirmed > 3 * solved:
        raise DayArchiveError('inconsistent progress counts')
    if 'job_sha256' in report and inventory.get('job.original.json', {}).get('sha256') != report['job_sha256']:
        raise DayArchiveError('job hash binding mismatch')
    sources = artifacts.get('source_inventory.json', [])
    seen = set()
    for item in sources:
        name = str(_relative(item['path']))
        if name in seen: raise DayArchiveError('duplicate source inventory path')
        seen.add(name)
        if inventory.get('source/' + name) != {k:item[k] for k in ('sha256','size')}:
            raise DayArchiveError('source inventory binding mismatch: ' + name)
    if {p for p in inventory if p.startswith('source/')} != {'source/'+p for p in seen}:
        raise DayArchiveError('source inventory coverage mismatch')
    records = report.get('window_records', [])
    if not isinstance(records, list) or len(records) != prepared:
        raise DayArchiveError('window record count mismatch')
    starts = []
    for i, record in enumerate(records):
        prefix = f'windows/{i:03d}/'
        if record.get('window_number') != i or record.get('path') != prefix:
            raise DayArchiveError('window path/number mismatch')
        starts.append(_time(record['delivery_start']))
        if i and starts[-1] - starts[-2] != timedelta(minutes=15):
            raise DayArchiveError('noncontiguous window starts')
        selected = artifacts.get(prefix+'selected.json')
        if selected is not None:
            identity = artifacts.get(prefix+'selected_identity.json', {})
            if (selected.get('run_id') != report.get('run_id') or selected.get('window_id') != record['window_id']
                or selected.get('prepared_sha256') != record['prepared_sha256']
                or _time(selected['delivery_start']) != starts[-1]
                or identity.get('selected_dispatch_sha256') != inventory[prefix+'selected.json']['sha256']
                or identity.get('selected_dispatch_id') != selected.get('selected_dispatch_id')):
                raise DayArchiveError('selected RT identity/source binding mismatch')
            provenance = artifacts.get(prefix+'provenance.json', {})
            if report.get('native_numerical_acceptance') is True and (
                provenance.get('native_numerical_acceptance') is not True
                or provenance.get('mock_flow_only') is not False or provenance.get('solver_called') is not True):
                raise DayArchiveError('native acceptance contradicts selected provenance')
            if selected.get('solver_call_provenance') != artifacts.get(prefix+'provenance.json'):
                raise DayArchiveError('selected provenance mismatch')
    selected_paths = [p for p in artifacts if p.startswith('windows/') and p.endswith('/selected.json')]
    if len(selected_paths) != solved: raise DayArchiveError('solved count mismatch')
    events = [artifacts[p] for p in sorted(artifacts) if p.startswith('events/')]
    confirmed_events = [e for e in events if e.get('event') == 'step_confirmed']
    completed_events = [e for e in events if e.get('event') == 'window_completed']
    if len(confirmed_events) != confirmed or len(completed_events) != completed:
        raise DayArchiveError('event count mismatch')
    pending = {(e.get('window_number'), e.get('step')): e for e in events if e.get('event') == 'execution_pending'}
    keys, receipts = set(), set()
    for event in confirmed_events:
        key = (event.get('window_number'), event.get('step'))
        if key in keys or type(key[0]) is not int or not 0 <= key[0] < solved or key[1] not in (1,2,3):
            raise DayArchiveError('duplicate or invalid confirmed interval')
        receipt = event['receipt']
        if receipt['receipt_id'] in receipts or receipt['receipt_id'] != event['receipt_id']:
            raise DayArchiveError('duplicate or mismatched receipt identity')
        command = pending.get(key, {}).get('command', {})
        selected_path = f'windows/{key[0]:03d}/selected.json'
        command_raw = json.dumps(command, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        selected = artifacts[selected_path]
        step = selected['steps'][key[1]-1]
        previous = pending[key]['previous_state']
        previous_hash = sha256(json.dumps(previous, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if (command.get('run_id') != report.get('run_id')
            or command.get('window_id') != selected.get('window_id')
            or command.get('selected_dispatch_id') != selected.get('selected_dispatch_id')
            or command.get('powers_mw') != step.get('powers_mw') or command.get('states') != step.get('states')
            or receipt.get('previous_state_sha256') != previous_hash
            or _time(previous['as_of']) != _time(command['delivery_start'])
            or _time(receipt['end_state']['as_of']) != _time(command['delivery_end'])
            or command.get('selected_dispatch_sha256') != inventory[selected_path]['sha256']
            or receipt.get('command_id') != command.get('command_id')
            or receipt.get('command_sha256') != sha256(command_raw).hexdigest()
            or _time(command['delivery_start']) != starts[key[0]] + timedelta(minutes=5*(key[1]-1))
            or _time(command['delivery_end']) - _time(command['delivery_start']) != timedelta(minutes=5)):
            raise DayArchiveError('confirmed command/source/interval binding mismatch')
        keys.add(key); receipts.add(receipt['receipt_id'])
    if len({e.get('window_number') for e in completed_events}) != completed:
        raise DayArchiveError('duplicate completed window')
    # Never promote test harness/mock traces or report booleans into full-day proof.
    full = False
    if report.get('full_day_completed') is True:
        start, end = _time(report['expected_day_start']), _time(report['expected_day_end'])
        local = start.astimezone(timezone(timedelta(hours=8)))
        full = (report['status'] == 'completed' and requested == prepared == built == solved == completed == 96
                and confirmed == 288 and len(starts) == 96 and starts[0] == start
                and starts[-1]+timedelta(minutes=15) == end and end-start == timedelta(days=1)
                and local.hour == local.minute == local.second == local.microsecond == 0
                and report['day_id'] == local.date().isoformat()
                and report.get('native_numerical_acceptance') is True
                and report.get('mock_flow_only') is False and report.get('test_harness_only') is False
                and report.get('source_inventory_consistent') is True)
        if not full: raise DayArchiveError('unsupported full-day completion claim')
    return full


def read_day_run(directory):
    """One bounded fresh retry for a report/manifest checkpoint race.

    A repeated failure stays corrupt and disables numbers. No prior snapshot or
    other run is ever reused, even when a previous refresh was successful.
    """
    view = _read_day_run(directory)
    if view['integrity'] == 'corrupt':
        view = _read_day_run(directory)
    return view
