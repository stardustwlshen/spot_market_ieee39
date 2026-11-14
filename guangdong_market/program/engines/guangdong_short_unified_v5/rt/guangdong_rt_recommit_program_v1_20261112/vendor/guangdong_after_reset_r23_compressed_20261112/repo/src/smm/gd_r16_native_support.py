"""R16 pure evidence/storage/math support. No native entry or capabilities."""
from copy import deepcopy
from pathlib import Path
import math
import shutil
from .gd_r15_da_rt_bridge import canonical, digest, file_sha, require, finite, close, integrate_cell
EVIDENCE_CAP = 100 * 1024 ** 2
FILE_CAP = 16 * 1024 ** 2
DISK_FLOOR = 1024 ** 3

def source_manifest(root=None):
    root = Path(root or Path(__file__).resolve().parents[3]).resolve()
    records = {}
    for p in sorted(root.rglob('*')):
        require(not p.is_symlink(), 'source symlinks forbidden')
        if p.is_file():
            require('__pycache__' not in p.parts and '.pytest_cache' not in p.parts and p.suffix != '.pyc', 'bytecode/cache must stay outside frozen source')
            records[p.relative_to(root).as_posix()] = dict(bytes=p.stat().st_size, sha256=file_sha(p))
    return dict(files=records, tree_sha256=digest(records), file_count=len(records),
                total_bytes=sum(r['bytes'] for r in records.values()))


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


def _path_intact():
    return (EVIDENCE_CAP == 100*1024**2 and FILE_CAP == 16*1024**2 and DISK_FLOOR == 1024**3
        and all(globals()[name] is fn for name, fn in _ORIGINAL_FUNCTIONS.items()))

_ORIGINAL_FUNCTIONS = {name: fn for name, fn in list(globals().items()) if callable(fn)}
