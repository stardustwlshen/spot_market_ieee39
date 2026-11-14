"""Reviewed R15 native research adapter, separate from the frozen pure entry.

Capabilities authenticate ordinary in-process use, not hostile Python execution.
Only run_window owns a fresh process occurrence; arithmetic audits cannot issue.
No import starts an optimizer. No old permit or archived RT is an acceptance.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4
from weakref import WeakKeyDictionary

from . import gd_r15_two_window as rt
from . import gd_r15_da_rt_bridge as bridge
from . import highs_solver as hs
from .gd_r15_native_identity import runtime_identity
from .gd_r15_da_rt_bridge import (canonical, digest, file_sha, read_json, require,
    finite, close, stamp, integrate_cell)

OPTIONS = dict(threads=1, time_limit=120., mip_rel_gap=.001, mip_abs_gap=0.,
               output_flag=False, presolve='choose', solve_relaxation=False)
EVIDENCE_CAP = 100 * 1024 ** 2
FILE_CAP = 16 * 1024 ** 2
DISK_FLOOR = 1024 ** 3
SCHEMA = 'gd.research.r15.native_two_window.v1'
_RUNS = WeakKeyDictionary()
_ORIGINAL_POPEN = subprocess.Popen
_ORIGINAL_WAIT = subprocess.Popen.wait
_ORIGINAL_SERIALIZE = hs.serialize_model
_ORIGINAL_BUILD = rt.build_rt_model
_ORIGINAL_PREPARE = rt.prepare_window


class NativeResearchRun:
    __slots__ = ('__weakref__',)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def source_manifest(root=None):
    root = Path(root or Path(__file__).resolve().parents[3]).resolve()
    records = {}
    for p in sorted(root.rglob('*')):
        require(not p.is_symlink(), 'source symlinks forbidden')
        if p.is_file():
            require('__pycache__' not in p.parts and p.suffix != '.pyc', 'bytecode/cache must stay outside frozen source')
            records[p.relative_to(root).as_posix()] = dict(bytes=p.stat().st_size, sha256=file_sha(p))
    return dict(files=records, tree_sha256=digest(records), file_count=len(records),
                total_bytes=sum(r['bytes'] for r in records.values()))


def _path_intact():
    return (OPTIONS == dict(threads=1, time_limit=120., mip_rel_gap=.001, mip_abs_gap=0.,
                output_flag=False, presolve='choose', solve_relaxation=False)
            and subprocess.Popen is _ORIGINAL_POPEN and subprocess.Popen.wait is _ORIGINAL_WAIT
            and hs.serialize_model is _ORIGINAL_SERIALIZE
            and rt.build_rt_model is _ORIGINAL_BUILD and rt.prepare_window is _ORIGINAL_PREPARE
            and all(globals()[name] is fn for name, fn in _ORIGINAL_FUNCTIONS.items()))


def _space(out, extra=0):
    used = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    free = shutil.disk_usage(out).free
    require(used + extra <= EVIDENCE_CAP, '100 MiB evidence budget exceeded; stop, preserve files')
    require(free - extra >= DISK_FLOOR, '1 GiB disk floor would be crossed; stop, preserve files')
    return dict(evidence_bytes=used, free_bytes=free, evidence_cap_bytes=EVIDENCE_CAP,
                minimum_free_bytes=DISK_FLOOR)


def _write(out, name, value):
    raw = canonical(value)
    require(len(raw) <= FILE_CAP, 'single evidence file exceeds 16 MiB')
    _space(out, len(raw))
    path = out / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as f:
        f.write(raw)
    return file_sha(path)


def validate_release(path, expected_sha256):
    """External independent review/root release, not a caller Boolean switch."""
    path = Path(path).resolve()
    require(file_sha(path) == expected_sha256, 'release file hash mismatch')
    release = read_json(path)
    require(release['schema'] == SCHEMA + '.release' and release['status'] == 'approved_after_independent_review', 'independent review release required')
    require(release['scope'] == 'exactly_two_fresh_RT_LPs_no_pricing_no_DA_no_preparation_rerun', 'release scope mismatch')
    require(type(release['max_native_calls']) is int and release['max_native_calls'] == 2, 'exact two-call release required')
    require(release['solver_options'] == OPTIONS and release['solver_version'] == '1.15.1', 'release solver parameters mismatch')
    require(release['evidence_cap_bytes'] == EVIDENCE_CAP and release['minimum_free_bytes'] == DISK_FLOOR, 'release storage limits mismatch')
    review_path = Path(release['independent_review_path']).resolve()
    require(file_sha(review_path) == release['independent_review_sha256'], 'review evidence changed')
    review = read_json(review_path)
    require(review['status'] == 'pass' and review['native_execution_performed'] is False, 'passing independent pre-native review required')
    seal = source_manifest()
    require(release['source_tree_sha256'] == review['source_tree_sha256'] == seal['tree_sha256'], 'reviewed source tree changed')
    require(release['runtime_identity'] == runtime_identity(), 'reviewed interpreter/package runtime changed')
    require(_path_intact(), 'native adapter path modified')
    return release, seal


def create_run(*, r14_core, preparation_root, output, release_path, release_sha256):
    release, seal = validate_release(release_path, release_sha256)
    out = Path(output).resolve()
    source = Path(__file__).resolve().parents[3]
    require(not out.exists() and not out.is_relative_to(source), 'new external evidence directory required')
    plan, permit = bridge.load_accepted_r14(r14_core, preparation_root=preparation_root)
    require(digest(plan['source_bindings']) == release['R14_bindings_sha256']
            and digest(plan['preparation_source_bindings']) == release['preparation_bindings_sha256'], 'released source witness bindings mismatch')
    out.mkdir(parents=True, exist_ok=False)
    _space(out, EVIDENCE_CAP)
    session = rt.initialize_two_windows(plan, permit)
    run = NativeResearchRun()
    _RUNS[run] = dict(session=session, output=out, seal=seal, release=release,
        release_path=Path(release_path).resolve(), release_sha256=release_sha256,
        calls=0, verified_solver_calls=0, stopped=False, accepted=[], receipts=[],
        executed_ids=set(), window_occurrences=[], created_at_utc=utc_now())
    _write(out, 'source_manifest.json', seal)
    _write(out, 'release.json', release)
    _write(out, 'publication.json', plan)
    _write(out, 'initial_session.json', report(run))
    return run


def _run(run):
    require(type(run) is NativeResearchRun and run in _RUNS, 'fresh native research run capability required')
    return _RUNS[run]


def audit_lp_certificate(request, response, names):
    """Independent exhaustive primal, reduced-cost and strong-duality arithmetic.

    Every variable and row is checked, including fixed columns and zero rows.
    This function creates no execution or acceptance capability.
    """
    require(response.get('status') == 'optimal' and response.get('has_primal') is True
            and response.get('dual_valid') is True and response.get('backend') == 'highs'
            and response.get('solver_version') == '1.15.1'
            and response.get('native_status') == 'Optimal', 'optimal fresh continuous HiGHS witness required')
    require(request['objective']['minimize'] is True and all(v['kind'] == 'C' for v in request['variables']), 'continuous minimization only')
    require(set(names) == {'variables', 'constraints'}, 'complete names schema required')
    x, y = response['primal'], response['dual']
    require(len(x) == len(request['variables']) == len(names['variables']) == len(set(names['variables'])), 'full unique variable witness required')
    require(len(y) == len(request['rows']) == len(names['constraints']) == len(set(names['constraints'])), 'full unique row dual witness required')
    for z in x + y + [response['objective'], response['bound']]:
        finite(z)
    obj = request['objective']
    costs = [0.] * len(x)
    for i, c in obj['terms']:
        costs[i] += c
    reduced = costs[:]
    dual_obj = obj['constant']
    max_primal = max_complementarity = max_dual_sign = 0.
    rows, columns = [], []
    def violation(value, lo, hi):
        return max(0., 0. if lo is None else lo - value, 0. if hi is None else value - hi)
    def dual_term(z, value, lo, hi):
        # Numerical zeros may be ignored only within absolute dual feasibility tolerance.
        bound = lo if z >= 0 else hi
        if bound is None:
            require(abs(z) <= 1e-8, 'dual sign points at absent bound')
            return 0., 0., abs(z)
        return z * bound, abs(z * (value - bound)), 0.
    for name, row, z in zip(names['constraints'], request['rows'], y):
        ax = math.fsum(c * x[i] for i, c in row['terms'])
        v = violation(ax, row['lb'], row['ub'])
        max_primal = max(max_primal, v)
        for i, c in row['terms']:
            reduced[i] -= z * c
        term, comp, sign = dual_term(z, ax, row['lb'], row['ub'])
        dual_obj += term
        max_complementarity = max(max_complementarity, comp)
        max_dual_sign = max(max_dual_sign, sign)
        rows.append(dict(name=name, activity=ax, lb=row['lb'], ub=row['ub'],
                         violation=v, dual=z, complementarity=comp))
    for name, record, value, z in zip(names['variables'], request['variables'], x, reduced):
        v = violation(value, record['lb'], record['ub'])
        max_primal = max(max_primal, v)
        term, comp, sign = dual_term(z, value, record['lb'], record['ub'])
        dual_obj += term
        max_complementarity = max(max_complementarity, comp)
        max_dual_sign = max(max_dual_sign, sign)
        columns.append(dict(name=name, value=value, lb=record['lb'], ub=record['ub'],
                            violation=v, reduced_cost=z, complementarity=comp))
    objective = obj['constant'] + math.fsum(c * x[i] for i, c in obj['terms'])
    objective_tol = 1e-5 + 1e-9 * max(1., abs(objective))
    gap = abs(objective - dual_obj)
    require(max_primal <= 1e-6, 'full primal residual failure')
    require(abs(objective - response['objective']) <= objective_tol
            and abs(objective - response['bound']) <= objective_tol, 'objective or reported bound mismatch')
    require(gap <= objective_tol and max_complementarity <= objective_tol
            and max_dual_sign <= 1e-8, 'independent LP dual optimality failure')
    return dict(passed=True, primal_objective=objective, independent_dual_objective=dual_obj,
        absolute_duality_gap=gap, objective_tolerance=objective_tol,
        max_primal_violation=max_primal, primal_tolerance=1e-6,
        max_complementarity=max_complementarity, max_dual_sign_violation=max_dual_sign,
        variables=len(x), constraints=len(y), integer_variables=0, rows=rows, columns=columns)


def reconstruct_solution_cells(inp, names, primal):
    """Pure 24-cell physical replay. Does not authenticate or issue dispatch."""
    require(len(names['variables']) == len(primal), 'primal names shape')
    values = dict(zip(names['variables'], primal))
    state = deepcopy(inp['projection']['state'])
    cells = []
    for j, da in enumerate(inp['da_cells']):
        cell = deepcopy(da)
        for g, row in cell['thermal'].items():
            p = finite(values[f'power[{g},{j}]'])
            if row['phase'] in ('startup', 'shutdown'):
                close(p, row['mean_mw'], 'native transition curve mean changed')
            else:
                row.update(begin_mw=state['thermal'][g]['power_mw'], mean_mw=p, end_mw=p)
            row['age_begin_h'] = state['thermal'][g]['state_age_h']
        for b, row in cell['batteries'].items():
            p = finite(values[f'power[{b},{j}]'])
            row.update(power_mw=p, charge_mw=-p if row['mode'] == 'charge' else 0.,
                discharge_mw=p if row['mode'] == 'discharge' else 0.,
                energy_begin_mwh=finite(values[f'energy[{b},{j}]']),
                energy_end_mwh=finite(values[f'energy[{b},{j+1}]']))
            close(state['batteries'][b]['energy_mwh'], row['energy_begin_mwh'], 'native initial energy continuity')
        state = integrate_cell(state, cell, inp['technical'])
        for g, row in cell['thermal'].items():
            row.update(age_end_h=state['thermal'][g]['state_age_h'], residence_remaining_h=state['thermal'][g]['residence_remaining_h'])
        for b, row in cell['batteries'].items():
            close(state['batteries'][b]['energy_mwh'], row['energy_end_mwh'], 'native SOC integration differs')
            row.update(mode_age_end_h=state['batteries'][b]['mode_age_h'],
                hour_direction=state['batteries'][b]['hour_direction'],
                daily_used_internal_mwh=deepcopy(state['batteries'][b]['daily_used_internal_mwh']))
        cell['reserve_awards_mw'] = {g: {k: values[f'reserve[{g},{j},{k}]']
            for k in ('positive', 'negative', 'primary', 'accident')} for g in cell['thermal']}
        cells.append(cell)
    return cells, state


def _validate_occurrence(raw, request_path, names_path, envelope, pid):
    binding = raw.get('occurrence', {})
    require(binding.get('id') == envelope['occurrence_id']
            and binding.get('worker_pid') == pid
            and binding.get('request_sha256') == file_sha(request_path)
            and binding.get('names_sha256') == file_sha(names_path)
            and binding.get('source_tree_sha256') == envelope['source_tree_sha256']
            and binding.get('input_sha256') == envelope['input_sha256']
            and binding.get('worker_file_sha256') == envelope['worker_file_sha256']
            and binding.get('native_run_count') == 1
            and binding.get('runtime_identity') == envelope['runtime_identity'], 'fresh process occurrence binding mismatch')
    require(raw.get('native_settings') == {k: OPTIONS[k] for k in ('output_flag', 'threads', 'presolve', 'mip_rel_gap', 'mip_abs_gap')}, 'applied native options mismatch')
    timing = raw['time_settings']
    require(timing['wall_time_limit_s'] == OPTIONS['time_limit']
            and timing['preparation_elapsed_s'] == envelope['payload']['preparation_elapsed_s']
            and 0 < timing['native_time_limit_s'] <= OPTIONS['time_limit']
            and 0 <= timing['transport_reserve_s']
            and timing['native_time_limit_s'] + timing['transport_reserve_s'] <= OPTIONS['time_limit'], 'native timing evidence mismatch')
    start = datetime.fromisoformat(binding['started_at_utc'])
    end = datetime.fromisoformat(binding['finished_at_utc'])
    require(start.utcoffset().total_seconds() == end.utcoffset().total_seconds() == 0
            and datetime.fromisoformat(envelope['created_at_utc']) <= start <= end
            <= datetime.now(timezone.utc), 'actual occurrence UTC chronology mismatch')


def run_window(run, index):
    """One fresh native call, full audit, then exactly three issued commands.

    No response/model callback or external acceptance path exists. Any failure
    permanently stops this run. W0 failure cannot be retried or skip to W1.
    """
    e = _run(run)
    require(not e['stopped'], 'failed run is permanently stopped; no retry')
    require(type(index) is int and index == len(e['accepted']) and index in (0, 1), 'exact next two-window index required')
    require(e['calls'] == index, 'one attempt per window, no duplicate native call')
    out = e['output']
    directory = out / ('window' + str(index))
    directory.mkdir(exist_ok=False)
    try:
        validate_release(e['release_path'], e['release_sha256'])
        require(source_manifest() == e['seal'], 'source changed during native session')
        _space(out, 4 * FILE_CAP)
        s = rt._session(e['session'])
        inp, ticket = rt.prepare_window(e['session'], index)
        m, model_audit = rt.build_rt_model(inp, ticket)
        payload, vs, cs = hs.serialize_model(m, OPTIONS)
        names = dict(variables=[v.name for v in vs], constraints=[c.name for c in cs])
        require(all(v['kind'] == 'C' for v in payload['variables']), 'RT LP cannot contain integers')
        _write(out, f'window{index}/input.json', inp)
        names_hash = _write(out, f'window{index}/names.json', names)
        _write(out, f'window{index}/model_audit.json', model_audit)
        started = time.perf_counter()
        payload.update(deadline=started + OPTIONS['time_limit'], preparation_elapsed_s=0.)
        worker = Path(__file__).with_name('gd_r15_native_worker.py')
        envelope = dict(schema=SCHEMA + '.request', occurrence_id='R15-NATIVE-' + uuid4().hex,
            input_sha256=digest(inp), source_tree_sha256=e['seal']['tree_sha256'],
            names_sha256=names_hash, worker_file_sha256=file_sha(worker),
            created_at_utc=utc_now(), runtime_identity=deepcopy(e['release']['runtime_identity']), payload=payload)
        request_path, response_path = directory/'request.json', directory/'response.json'
        _write(out, f'window{index}/request.json', envelope)
        require(_path_intact(), 'native process path replaced')
        env = os.environ.copy()
        env.update(PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
            MKL_NUM_THREADS='1', TMPDIR=str(out))
        with (directory/'worker.log').open('xb') as log:
            process = subprocess.Popen([sys.executable, '-B', str(worker), str(request_path),
                str(response_path), str(directory/'names.json')], env=env, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True)
            e['calls'] += 1
            try:
                returncode = process.wait(timeout=max(.001, payload['deadline'] - time.perf_counter()))
            except BaseException:
                process.kill()
                process.wait()
                raise
        occurrence = dict(id=envelope['occurrence_id'], worker_pid=process.pid,
            returncode=returncode, request_sha256=file_sha(request_path),
            names_sha256=file_sha(directory/'names.json'), actual_finished_at_utc=utc_now())
        e['window_occurrences'].append(occurrence)
        _write(out, f'window{index}/process_occurrence.json', occurrence)
        require(returncode == 0 and response_path.is_file(), 'native worker failed or response absent')
        require(response_path.stat().st_size <= FILE_CAP, 'oversized native response')
        raw = read_json(response_path)
        require(not raw.get('error'), 'native worker error: ' + str(raw.get('error')))
        _validate_occurrence(raw, request_path, directory/'names.json', envelope, process.pid)
        e['verified_solver_calls'] += 1
        require(_path_intact() and source_manifest() == e['seal'], 'source/path changed during process')
        bridge.validate_bridge(s['plan'], s['permit'], cutoff=inp['window']['cutoff'], recheck_files=True)
        rt._ticket(inp, ticket)
        rebuilt, rv, rc = hs.serialize_model(m, OPTIONS)
        require(all(rebuilt[k] == payload[k] for k in ('variables', 'rows', 'objective', 'options'))
                and names == dict(variables=[v.name for v in rv], constraints=[c.name for c in rc]), 'live model/request/names changed')
        certificate = audit_lp_certificate(payload, raw, names)
        _write(out, f'window{index}/full_certificate.json', certificate)
        cells, terminal = reconstruct_solution_cells(inp, names, raw['primal'])
        _write(out, f'window{index}/solution_cells.json', cells)
        acceptance = dict(schema=SCHEMA + '.accepted', occurrence=occurrence,
            input_sha256=digest(inp), request_sha256=file_sha(request_path),
            response_sha256=file_sha(response_path), names_sha256=names_hash,
            source_tree_sha256=e['seal']['tree_sha256'], solver_options=deepcopy(OPTIONS),
            certificate_sha256=file_sha(directory/'full_certificate.json'), window=inp['window'],
            objective_yuan=certificate['primal_objective'], full_horizon_cells=24, issued_cells=3,
            terminal_state_is_not_execution_receipt=True, official_market=False,
            execution_authority=False, actual_telemetry=False)
        commands = [dict(id=s['id'] + f'-W{index}-RT-{j}', kind='accepted_fresh_RT_research_command',
            issued_at=inp['window']['cutoff'], available_at=inp['window']['cutoff'],
            occurrence_id=envelope['occurrence_id'], acceptance_sha256=digest(acceptance),
            cell=deepcopy(cell)) for j, cell in enumerate(cells[:3])]
        _write(out, f'window{index}/acceptance.json', acceptance)
        _write(out, f'window{index}/issued_commands.json', commands)
        # Commit state only after all certificates and evidence writes succeed.
        s['commands'].extend(commands)
        s['accepted_windows'].append(acceptance)
        s['next_window'] += 1
        s['prepared'] = None
        rt._TICKETS[ticket]['consumed'] = True
        e['accepted'].append(acceptance)
        return deepcopy(acceptance)
    except BaseException as exc:
        e['stopped'] = True
        failure = dict(schema=SCHEMA + '.failure', window=index, error=f'{type(exc).__name__}: {exc}',
            actual_created_at_utc=utc_now(), calls_attempted=e['calls'],
            verified_solver_calls=e['verified_solver_calls'], accepted_windows=len(e['accepted']),
            retry_allowed=False, evidence_preserved=True)
        # Do not discard the original failure if the floor/cap itself prevents writing.
        try:
            _write(out, f'window{index}/failure.json', failure)
        except Exception:
            pass
        raise


def advance_to(run, target):
    """Simulated zero-tracking receipts only for commands whose end has arrived."""
    e = _run(run)
    require(not e['stopped'], 'failed run cannot advance execution')
    s = rt._session(e['session'])
    target = stamp(target)
    allowed = {stamp(w['start']) for w in s['specs']} | {stamp(s['specs'][-1]['delivery_end'])}
    require(target in allowed and target >= stamp(s['clock']), 'only declared boundary pauses/drain allowed')
    state = deepcopy(s['receipt']['state'])
    receipts = []
    while stamp(state['at']) < target:
        matches = [c for c in s['commands'] if c['cell']['start'] == state['at']
            and stamp(c['issued_at']) <= stamp(c['available_at']) <= stamp(c['cell']['start'])]
        require(len(matches) == 1, 'missing/duplicate accepted command at execution boundary')
        c = matches[0]
        require(c['id'] not in e['executed_ids'] and stamp(c['cell']['end']) <= target, 'duplicate or future receipt forbidden')
        state = integrate_cell(state, c['cell'], s['plan']['technical'])
        receipt = dict(id=c['id'] + '-SIMULATED-RECEIPT', at=state['at'], available_at=state['at'],
            state=deepcopy(state), command_id=c['id'], command_sha256=digest(c),
            is_measurement=False, kind='simulated_zero_tracking_execution_not_telemetry',
            RT=c['kind'] == 'accepted_fresh_RT_research_command')
        receipts.append(receipt)
    require(stamp(state['at']) == target, 'receipt target mismatch')
    _write(e['output'], f'ledger_{target.strftime("%H%M")}.json', dict(target=target.isoformat(), receipts=receipts))
    for receipt in receipts:
        e['executed_ids'].add(receipt['command_id'])
    e['receipts'].extend(receipts)
    if receipts:
        s['receipt'] = deepcopy(receipts[-1])
    s['clock'] = target.isoformat()
    return report(run)


def report(run):
    e = _run(run)
    s = rt._session(e['session'])
    return dict(schema=SCHEMA + '.report', status='stopped' if e['stopped'] else 'research_session',
        actual_created_at_utc=e['created_at_utc'], logical_clock=s['clock'],
        accepted_RT_windows=len(e['accepted']), issued_RT_cells=3 * len(e['accepted']),
        executed_RT_cells=sum(r['RT'] for r in e['receipts']),
        preparation_commands=3, executed_preparation_cells=sum(not r['RT'] for r in e['receipts']),
        native_process_calls=e['calls'], verified_native_solver_calls=e['verified_solver_calls'],
        new_preparation_calls=0, new_DA_calls=0, independent_RT_price_calls=0,
        formal_bill=None, actual_telemetry=False, official_market=False, execution_authority=False,
        native_solver_call_count_uncertain=e['calls'] != e['verified_solver_calls'],
        scopes=dict(bridge.SCOPES, new_native_calls=e['verified_solver_calls']
            if e['calls'] == e['verified_solver_calls'] else None), storage=_space(e['output']),
        accepted=deepcopy(e['accepted']), receipt_count=len(e['receipts']))


_ORIGINAL_FUNCTIONS = {name: globals()[name] for name in ('validate_release', 'create_run',
    'audit_lp_certificate', 'reconstruct_solution_cells', '_validate_occurrence',
    'run_window', 'advance_to', 'source_manifest', '_write', '_run', '_space', 'runtime_identity', '_path_intact')}
