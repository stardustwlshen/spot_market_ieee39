"""Linear Pyomo adapter; HiGHS native libraries live exclusively in a child process."""
from __future__ import annotations

import json
from copy import deepcopy
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
from .highs_gap import gap_settings


class HighsNoIncumbentError(RuntimeError):
    """Validated worker failure protocol, not a primal or execution receipt.

    native_path_intact distinguishes genuine adapter calls from pure protocol
    mocks. The record deliberately excludes arbitrary worker payload content.
    """
    def __init__(self, message, record):
        super().__init__(message)
        self._record = deepcopy(record)

    @property
    def failure_record(self):
        return deepcopy(self._record)


def _finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('HiGHS model/result contains a non-finite number')
    return value


def validate_options(options):
    if type(options) is not dict:
        raise ValueError('HiGHS options must be an explicit dictionary')
    allowed={'threads','time_limit','mip_rel_gap','mip_abs_gap','output_flag','presolve','solve_relaxation'}
    if set(options)-allowed:
        raise ValueError('Unsupported HiGHS options: '+', '.join(sorted(set(options)-allowed)))
    values=dict(threads=1,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False,presolve='choose',solve_relaxation=False)
    values.update(options)
    if type(values['threads']) is not int or values['threads']<1:
        raise ValueError('HiGHS threads must be a positive integer')
    for name in ('time_limit','mip_rel_gap','mip_abs_gap'):
        value=values.get(name)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('HiGHS '+name+' must be finite numeric')
    if values['time_limit']<=0 or not 0<=values['mip_rel_gap']<=.001 or values['mip_abs_gap']!=0:
        raise ValueError('HiGHS positive time limit, relative gap <= 0.001 and absolute gap 0 required')
    if values['presolve'] not in ('choose','on','off'):
        raise ValueError('HiGHS presolve must be choose/on/off')
    if any(type(values[k]) is not bool for k in ('output_flag','solve_relaxation')):
        raise ValueError('HiGHS Boolean flags required')
    if values['solve_relaxation']:
        raise ValueError('HiGHS market duals require continuous or explicitly fixed integers; implicit relaxation is unsupported')
    return values


def serialize_model(model, options, warmstart=False):
    allowed={pyo.Block,pyo.Set,pyo.RangeSet,pyo.Param,pyo.Var,pyo.Constraint,pyo.Expression,pyo.Objective,pyo.Suffix}
    for component in model.component_objects(active=True,descend_into=True):
        if component.ctype not in allowed:
            raise ValueError('HiGHS unsupported active algebraic structure: '+component.name)
    variables = list(model.component_data_objects(pyo.Var, descend_into=True))
    constraints = list(model.component_data_objects(pyo.Constraint, active=True, descend_into=True))
    indices = {id(v): i for i, v in enumerate(variables)}
    def expression(expr):
        repn = generate_standard_repn(expr, compute_values=True)
        if not repn.is_linear():
            raise ValueError('HiGHS adapter supports linear models only')
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
        raise ValueError('HiGHS requires exactly one active objective')
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
                process.kill()  # Official HiGHS worker uses threads, not descendant processes.
            process.wait()
            raise
        log.seek(0, os.SEEK_END)
        size = log.tell()
        log.seek(max(0, size-65536))
        tail = log.read().decode('utf-8', errors='replace')
        return SimpleNamespace(returncode=process.returncode, stdout=tail if tee else '', stderr=tail)


def child_environment():
    # Use the current interpreter's official highspy package. Never discover,
    # import, initialize, or fall back to a licensed alternative solver.
    return os.environ.copy()


def validate_solution(payload, result):
    """Check the entire response before touching any Pyomo variable or suffix."""
    values = result.get('primal')
    if not isinstance(values, list) or len(values) != len(payload['variables']):
        raise RuntimeError('HiGHS returned incomplete primal vector')
    values = [_finite(v) for v in values]
    def tolerance(a, b):
        return 1e-5 + 1e-7 * max(abs(a), abs(b))
    def bounds(value, lo, hi):
        if lo is not None and value < lo - tolerance(value, lo):
            raise RuntimeError('HiGHS primal violates a lower bound or constraint')
        if hi is not None and value > hi + tolerance(value, hi):
            raise RuntimeError('HiGHS primal violates an upper bound or constraint')
    for value, record in zip(values, payload['variables']):
        bounds(value, record['lb'], record['ub'])
        if record['kind'] != 'C' and abs(value-round(value)) > 1e-5:
            raise RuntimeError('HiGHS primal violates integrality')
    for row in payload['rows']:
        bounds(math.fsum(values[i]*c for i,c in row['terms']), row['lb'], row['ub'])
    obj = payload['objective']
    objective = math.fsum(values[i]*c for i,c in obj['terms']) + obj['constant']
    reported = _finite(result['objective'])
    bound = _finite(result['bound'])
    if abs(objective-reported) > tolerance(objective, reported):
        raise RuntimeError('HiGHS reported objective does not match primal')
    if ((obj['minimize'] and bound > objective+tolerance(bound,objective)) or
            (not obj['minimize'] and bound < objective-tolerance(bound,objective))):
        raise RuntimeError('HiGHS reported bound contradicts primal objective')
    return values


class HighsSolver:
    backend = 'highs'

    def __init__(self, options):
        self.options = validate_options(options)
        # Compatibility for downstream integrations which inspect canonical legacy option names.
        self.highs_options = self.options
        self.config = SimpleNamespace(stream_solver=False)
        self._solver_model = None
        self.status = None
        self.primal_available = False
        self.time_settings = None
        self.solver_version = None
        self.native_settings = None
        self.native_status = None
        self.dual_available = False
        self.native_mip_gap = None

    @property
    def gap_settings(self):
        return gap_settings(self.options['mip_rel_gap']) if 'mip_rel_gap' in self.options else None

    def solve(self, model, tee=False, warmstart=False):
        started = time.perf_counter()
        self._solver_model = object()  # Per-call identity prevents stale infeasibility classification.
        self.status = None
        self.primal_available = False
        self.time_settings = None
        self.solver_version = None
        self.native_settings = None
        self.native_status = None
        self.dual_available = False
        self.native_mip_gap = None
        if hasattr(model, 'dual'):
            model.dual.clear()
        try:
            if type(tee) is not bool or type(warmstart) is not bool:
                raise ValueError('HiGHS tee and warmstart flags must be Boolean')
            self.options = validate_options(self.options)
            self.highs_options = self.options
            payload, variables, constraints = serialize_model(model, self.options, warmstart)
            # Starts are serialized before clearing old primal values. No failed call leaves a stale solution.
            for v in variables:
                if not v.fixed:
                    v.set_value(None)
            limit = _finite(self.options['time_limit'])
            payload['deadline'] = started + limit
            payload['preparation_elapsed_s'] = time.perf_counter() - started
            with tempfile.TemporaryDirectory(prefix='smm-highs-') as tmp:
                request, response = Path(tmp)/'request.json', Path(tmp)/'response.json'
                request.write_text(json.dumps(payload, allow_nan=False), encoding='utf-8')
                remaining = limit - (time.perf_counter() - started)
                if remaining <= 0:
                    raise RuntimeError('HiGHS wall-clock limit exhausted during serialization; no incumbent loaded')
                try:
                    completed = run_child([sys.executable, str(Path(__file__).with_name('highs_worker.py')),
                                                str(request), str(response)], env=child_environment(),
                                               timeout=remaining, tee=tee)
                except subprocess.TimeoutExpired as exc:
                    self.status = 'maxTimeLimit'
                    raise RuntimeError('HiGHS subprocess wall-clock limit exceeded; no incumbent loaded') from exc
                if tee:
                    print(completed.stdout, end='')
                if completed.returncode or not response.is_file():
                    raise RuntimeError('HiGHS subprocess failed; check official highspy installation. '
                                       + completed.stderr[-1500:])
                result = json.loads(response.read_text(encoding='utf-8'))
            if result.get('version') != 1 or result.get('error'):
                raise RuntimeError('HiGHS worker failed: ' + str(result.get('error', 'invalid response version')))
            timing = result.get('time_settings')
            if timing is None and result.get('has_primal') is True:
                raise RuntimeError('HiGHS successful response is missing time settings')
            if timing is not None:
                keys = {'wall_time_limit_s', 'native_time_limit_s', 'transport_reserve_s', 'preparation_elapsed_s'}
                if not isinstance(timing, dict) or set(timing) != keys:
                    raise RuntimeError('HiGHS returned malformed time settings')
                timing = {key: _finite(timing[key]) for key in keys}
                if (any(v < 0 for v in timing.values()) or timing['wall_time_limit_s'] != limit or
                        timing['native_time_limit_s'] + timing['transport_reserve_s'] > limit + 1e-9 * max(1., limit) or
                        timing['preparation_elapsed_s'] > limit or
                        timing['preparation_elapsed_s'] != payload['preparation_elapsed_s']):
                    raise RuntimeError('HiGHS returned inconsistent time settings')
            if result.get('backend') != 'highs' or not isinstance(result.get('solver_version'), str) or not result['solver_version']:
                raise RuntimeError('HiGHS result is missing verified backend/version')
            native_settings = result.get('native_settings')
            if not isinstance(native_settings, dict) or any(type(native_settings.get(k)) is not type(self.options[k]) or native_settings.get(k) != self.options[k]
                    for k in ('threads','mip_rel_gap','mip_abs_gap','presolve','output_flag')):
                raise RuntimeError('HiGHS applied solver settings differ from requested settings')
            self.solver_version = result['solver_version']
            self.native_settings = native_settings
            self.native_status = result.get('native_status')
            self.native_mip_gap = result.get('native_mip_gap')
            status = result.get('status')
            available = result.get('has_primal')
            if (type(available) is not bool or status not in
                    ('optimal', 'maxTimeLimit', 'infeasible', 'unbounded', 'infeasibleOrUnbounded', 'unknown') or
                    (status == 'optimal' and not available) or
                    (status in ('infeasible', 'unbounded', 'infeasibleOrUnbounded') and available)):
                raise RuntimeError('HiGHS returned contradictory or malformed status')
            self.status = status
            if self.status not in ('optimal', 'maxTimeLimit') or not result.get('has_primal'):
                if not isinstance(self.native_status, str) or not self.native_status or len(self.native_status)>256:
                    raise RuntimeError('HiGHS failure response has malformed native status')
                expected_native = {'infeasible': {'Infeasible'}, 'unbounded': {'Unbounded'},
                    'infeasibleOrUnbounded': {'Primal infeasible or unbounded', 'Unbounded or infeasible'},
                    'maxTimeLimit': {'Time limit reached', 'deadline_before_native_run'}}
                if self.status in expected_native and self.native_status not in expected_native[self.status]:
                    raise RuntimeError('HiGHS failure normalized/native status mismatch')
                witness = result.get('primal_witness_available', False)
                if (type(witness) is not bool or (self.status=='infeasible' and witness) or
                        result.get('primal') is not None or result.get('dual_valid', False) is not False):
                    raise RuntimeError('HiGHS no-incumbent response contains contradictory primal/dual witness')
                raise HighsNoIncumbentError(f'HiGHS terminated {self.status}; no usable incumbent',
                    dict(schema='smm.highs.no_incumbent.v1',backend='highs',status=self.status,
                        native_status=self.native_status,solver_version=self.solver_version,
                        native_settings={k:native_settings[k] for k in ('threads','mip_rel_gap','mip_abs_gap','presolve','output_flag')},
                        time_settings=timing,has_primal=False,native_path_intact=native_solver_path_intact()))
            values = validate_solution(payload, result)
            dual = result.get('dual')
            is_lp = all(v['kind'] == 'C' for v in payload['variables'])
            if is_lp and self.status != 'optimal':
                raise RuntimeError('HiGHS LP did not reach optimality; no primal or dual loaded')
            if payload['dual'] and is_lp and self.status == 'optimal':
                if result.get('dual_valid') is not True or not isinstance(dual, list) or len(dual) != len(constraints):
                    raise RuntimeError('HiGHS returned incomplete dual vector')
                dual = [_finite(v) for v in dual]
            bound = _finite(result['bound'])
            _finite(result['objective'])
            if time.perf_counter() - started > limit:
                raise RuntimeError('HiGHS wall-clock limit exceeded during result transfer; incumbent not loaded')
            for v, val in zip(variables, values):
                if not v.fixed:
                    v.set_value(val, skip_validation=True)
            if payload['dual'] and is_lp and self.status == 'optimal':
                model.dual.update(zip(constraints, dual))
                self.dual_available = True
            self.primal_available = True
            self.time_settings = timing
            return SimpleNamespace(solver=SimpleNamespace(termination_condition=self.status),
                                   problem=SimpleNamespace(lower_bound=bound if payload['objective']['minimize'] else result['objective'],
                                                           upper_bound=result['objective'] if payload['objective']['minimize'] else bound))
        except Exception as exc:
            self.primal_available = False
            self.dual_available = False
            for v in model.component_data_objects(pyo.Var, descend_into=True):
                if not v.fixed:
                    v.set_value(None)
            if hasattr(model, 'dual'):
                model.dual.clear()
            if not isinstance(exc, RuntimeError):
                raise RuntimeError(f'HiGHS adapter failed: {type(exc).__name__}: {exc}') from exc
            raise


# Trace ordinary test monkeypatches through every application adapter boundary.
# This is in-process provenance, not a sandbox against hostile Python code.
_ORIGINAL_HIGHS_CLASS = HighsSolver
_ORIGINAL_POPEN = subprocess.Popen
_NATIVE_CALLABLES = ((HighsSolver,'__init__','smm.highs_solver'),
    (HighsSolver,'solve','smm.highs_solver'),
    (subprocess.Popen,'__init__','subprocess'),(subprocess.Popen,'wait','subprocess'))
_ORIGINAL_CALLABLES = tuple(getattr(owner,name) for owner,name,_ in _NATIVE_CALLABLES)
_ORIGINAL_HELPERS = {name:globals()[name] for name in
    ('run_child','child_environment','serialize_model','validate_solution','validate_options','gap_settings')}


def native_solver_path_intact():
    from . import gd_solve
    def sourced(fn,module):
        path=getattr(getattr(fn,'__code__',None),'co_filename','').replace('\\','/').rsplit('/',1)[-1]
        return getattr(fn,'__module__',None)==module and path==module.rsplit('.',1)[-1]+'.py'
    return (HighsSolver is _ORIGINAL_HIGHS_CLASS is gd_solve.HighsSolver
        and subprocess.Popen is _ORIGINAL_POPEN
        and gd_solve.solve_dispatch is gd_solve._ORIGINAL_SOLVE_DISPATCH
        and sourced(gd_solve.solve_dispatch,'smm.gd_solve')
        and all(getattr(owner,name) is original and sourced(original,module)
            for (owner,name,module),original in zip(_NATIVE_CALLABLES,_ORIGINAL_CALLABLES))
        and all(globals()[name] is original and sourced(original,
            'smm.highs_gap' if name=='gap_settings' else 'smm.highs_solver')
            for name,original in _ORIGINAL_HELPERS.items()))
