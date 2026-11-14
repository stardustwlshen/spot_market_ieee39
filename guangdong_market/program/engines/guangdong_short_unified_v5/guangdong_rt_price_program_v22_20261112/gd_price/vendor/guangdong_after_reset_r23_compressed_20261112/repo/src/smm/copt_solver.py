"""Linear Pyomo adapter; COPT native libraries live exclusively in a child process."""
from __future__ import annotations

import json
import math
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pyomo.environ as pyo
from pyomo.repn import generate_standard_repn
from .copt_gap import gap_settings


def _finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('COPT model/result contains a non-finite number')
    return value


def serialize_model(model, options, warmstart=False):
    variables = list(model.component_data_objects(pyo.Var, descend_into=True))
    constraints = list(model.component_data_objects(pyo.Constraint, active=True, descend_into=True))
    indices = {id(v): i for i, v in enumerate(variables)}
    def expression(expr):
        repn = generate_standard_repn(expr, compute_values=True)
        if not repn.is_linear():
            raise ValueError('COPT adapter supports linear models only')
        return [[indices[id(v)], _finite(c)] for v, c in zip(repn.linear_vars, repn.linear_coefs)], _finite(repn.constant or 0.)
    records = []
    for v in variables:
        lo = None if v.lb is None else _finite(pyo.value(v.lb))
        hi = None if v.ub is None else _finite(pyo.value(v.ub))
        if v.fixed:
            lo = hi = _finite(pyo.value(v))
        records.append(dict(lb=lo, ub=hi, kind='C' if v.fixed or options.get('solve_relaxation') else
                            'B' if v.is_binary() else 'I' if v.is_integer() else 'C',
                            start=_finite(v.value) if warmstart and v.value is not None else None))
    rows = []
    for c in constraints:
        terms, constant = expression(c.body)
        rows.append(dict(terms=terms, lb=None if c.lower is None else _finite(pyo.value(c.lower))-constant,
                         ub=None if c.upper is None else _finite(pyo.value(c.upper))-constant))
    objectives = list(model.component_data_objects(pyo.Objective, active=True, descend_into=True))
    if len(objectives) != 1:
        raise ValueError('COPT requires exactly one active objective')
    terms, constant = expression(objectives[0].expr)
    payload = dict(version=1, variables=records, rows=rows, objective=dict(terms=terms, constant=constant,
                   minimize=objectives[0].sense == pyo.minimize), options=dict(options), warmstart=warmstart,
                   dual=hasattr(model, 'dual'),
                   gap_settings=gap_settings(options['mip_rel_gap']) if 'mip_rel_gap' in options else None)
    return payload, variables, constraints


def run_child(args, *, env, timeout, tee=False):
    # File-backed logs bound application memory even for verbose native output.
    with tempfile.TemporaryFile(mode='w+b') as log:
        process = subprocess.Popen(args, env=env, stdout=log, stderr=log,
                                   start_new_session=(os.name == 'posix'))
        try:
            process.wait(timeout=timeout)
        except BaseException:
            if os.name == 'posix':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()  # Official COPT worker uses threads, not descendant processes.
            process.wait()
            raise
        log.seek(0, os.SEEK_END)
        size = log.tell()
        log.seek(max(0, size-65536))
        tail = log.read().decode('utf-8', errors='replace')
        return SimpleNamespace(returncode=process.returncode, stdout=tail if tee else '', stderr=tail)


def child_environment():
    env = os.environ.copy()
    home = env.get('COPT_HOME')
    if home:
        root = Path(home) / 'lib' / 'python'
        binding = root / f'{sys.version_info.major}{sys.version_info.minor}'
        if (binding / 'coptpy').is_dir():
            env['PYTHONPATH'] = str(binding) + os.pathsep + env.get('PYTHONPATH', '')
        libkey = 'PATH' if os.name == 'nt' else 'DYLD_LIBRARY_PATH' if sys.platform == 'darwin' else 'LD_LIBRARY_PATH'
        env[libkey] = os.pathsep.join([str(root / 'deps'), str(Path(home) / 'lib'), env.get(libkey, '')])
    return env


def validate_solution(payload, result):
    """Check the entire response before touching any Pyomo variable or suffix."""
    values = result.get('primal')
    if not isinstance(values, list) or len(values) != len(payload['variables']):
        raise RuntimeError('COPT returned incomplete primal vector')
    values = [_finite(v) for v in values]
    def tolerance(a, b):
        return 1e-5 + 1e-7 * max(abs(a), abs(b))
    def bounds(value, lo, hi):
        if lo is not None and value < lo - tolerance(value, lo):
            raise RuntimeError('COPT primal violates a lower bound or constraint')
        if hi is not None and value > hi + tolerance(value, hi):
            raise RuntimeError('COPT primal violates an upper bound or constraint')
    for value, record in zip(values, payload['variables']):
        bounds(value, record['lb'], record['ub'])
        if record['kind'] != 'C' and abs(value-round(value)) > 1e-5:
            raise RuntimeError('COPT primal violates integrality')
    for row in payload['rows']:
        bounds(math.fsum(values[i]*c for i,c in row['terms']), row['lb'], row['ub'])
    obj = payload['objective']
    objective = math.fsum(values[i]*c for i,c in obj['terms']) + obj['constant']
    reported = _finite(result['objective'])
    bound = _finite(result['bound'])
    if abs(objective-reported) > tolerance(objective, reported):
        raise RuntimeError('COPT reported objective does not match primal')
    if ((obj['minimize'] and bound > objective+tolerance(bound,objective)) or
            (not obj['minimize'] and bound < objective-tolerance(bound,objective))):
        raise RuntimeError('COPT reported bound contradicts primal objective')
    return values


class CoptSolver:
    backend = 'copt'

    def __init__(self, options):
        self.options = dict(options)
        # Compatibility for downstream integrations which inspect canonical legacy option names.
        self.highs_options = self.options
        self.config = SimpleNamespace(stream_solver=False)
        self._solver_model = None
        self.status = None
        self.primal_available = False
        self.time_settings = None

    @property
    def gap_settings(self):
        return gap_settings(self.options['mip_rel_gap']) if 'mip_rel_gap' in self.options else None

    def solve(self, model, tee=False, warmstart=False):
        started = time.perf_counter()
        self._solver_model = object()  # Per-call identity prevents stale infeasibility classification.
        self.status = None
        self.primal_available = False
        self.time_settings = None
        if hasattr(model, 'dual'):
            model.dual.clear()
        try:
            payload, variables, constraints = serialize_model(model, self.options, warmstart)
            # Starts are serialized before clearing old primal values. No failed call leaves a stale solution.
            for v in variables:
                if not v.fixed:
                    v.set_value(None)
            limit = _finite(self.options['time_limit'])
            payload['deadline'] = started + limit
            payload['preparation_elapsed_s'] = time.perf_counter() - started
            with tempfile.TemporaryDirectory(prefix='smm-copt-') as tmp:
                request, response = Path(tmp)/'request.json', Path(tmp)/'response.json'
                request.write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
                remaining = limit - (time.perf_counter() - started)
                if remaining <= 0:
                    raise RuntimeError('COPT wall-clock limit exhausted during serialization; no incumbent loaded')
                try:
                    completed = run_child([sys.executable, str(Path(__file__).with_name('copt_worker.py')),
                                                str(request), str(response)], env=child_environment(),
                                               timeout=remaining, tee=tee)
                except subprocess.TimeoutExpired as exc:
                    self.status = 'maxTimeLimit'
                    raise RuntimeError('COPT subprocess wall-clock limit exceeded; no incumbent loaded') from exc
                if tee:
                    print(completed.stdout, end='')
                if completed.returncode or not response.is_file():
                    raise RuntimeError('COPT subprocess failed; check official coptpy installation and license. '
                                       + completed.stderr[-1500:])
                result = json.loads(response.read_text(encoding='utf-8'))
            if result.get('version') != 1 or result.get('error'):
                raise RuntimeError('COPT worker failed: ' + str(result.get('error', 'invalid response version')))
            timing = result.get('time_settings')
            if timing is None and result.get('has_primal') is True:
                raise RuntimeError('COPT successful response is missing time settings')
            if timing is not None:
                keys = {'wall_time_limit_s', 'native_time_limit_s', 'transport_reserve_s', 'preparation_elapsed_s'}
                if not isinstance(timing, dict) or set(timing) != keys:
                    raise RuntimeError('COPT returned malformed time settings')
                timing = {key: _finite(timing[key]) for key in keys}
                if (any(v < 0 for v in timing.values()) or timing['wall_time_limit_s'] != limit or
                        timing['native_time_limit_s'] + timing['transport_reserve_s'] > limit + 1e-9 * max(1., limit) or
                        timing['preparation_elapsed_s'] > limit or
                        timing['preparation_elapsed_s'] != payload['preparation_elapsed_s']):
                    raise RuntimeError('COPT returned inconsistent time settings')
            status = result.get('status')
            available = result.get('has_primal')
            if (type(available) is not bool or status not in
                    ('optimal', 'maxTimeLimit', 'infeasible', 'unbounded', 'infeasibleOrUnbounded', 'unknown') or
                    (status == 'optimal' and not available) or
                    (status in ('infeasible', 'unbounded', 'infeasibleOrUnbounded') and available)):
                raise RuntimeError('COPT returned contradictory or malformed status')
            self.status = status
            if self.status not in ('optimal', 'maxTimeLimit') or not result.get('has_primal'):
                raise RuntimeError(f'COPT terminated {self.status}; no usable incumbent')
            values = validate_solution(payload, result)
            dual = result.get('dual')
            is_lp = all(v['kind'] == 'C' for v in payload['variables'])
            if is_lp and self.status != 'optimal':
                raise RuntimeError('COPT LP did not reach optimality; no primal or dual loaded')
            if payload['dual'] and is_lp and self.status == 'optimal':
                if not isinstance(dual, list) or len(dual) != len(constraints):
                    raise RuntimeError('COPT returned incomplete dual vector')
                dual = [_finite(v) for v in dual]
            bound = _finite(result['bound'])
            _finite(result['objective'])
            if time.perf_counter() - started > limit:
                raise RuntimeError('COPT wall-clock limit exceeded during result transfer; incumbent not loaded')
            for v, val in zip(variables, values):
                if not v.fixed:
                    v.set_value(val, skip_validation=True)
            if payload['dual'] and is_lp and self.status == 'optimal':
                model.dual.update(zip(constraints, dual))
            self.primal_available = True
            self.time_settings = timing
            return SimpleNamespace(solver=SimpleNamespace(termination_condition=self.status),
                                   problem=SimpleNamespace(lower_bound=bound if payload['objective']['minimize'] else result['objective'],
                                                           upper_bound=result['objective'] if payload['objective']['minimize'] else bound))
        except Exception as exc:
            for v in model.component_data_objects(pyo.Var, descend_into=True):
                if not v.fixed:
                    v.set_value(None)
            if hasattr(model, 'dual'):
                model.dual.clear()
            if not isinstance(exc, RuntimeError):
                raise RuntimeError(f'COPT adapter failed: {type(exc).__name__}: {exc}') from exc
            raise
