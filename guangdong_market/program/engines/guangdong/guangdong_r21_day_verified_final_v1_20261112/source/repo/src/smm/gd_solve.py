"""Explicit HiGHS-only dispatch solve and non-mutating fixed-state LP polish."""
from __future__ import annotations
from dataclasses import dataclass
import math
from numbers import Real
import time
from types import SimpleNamespace

import pyomo.environ as pyo
from pyomo.core.base.componentuid import ComponentUID
from pyomo.repn import generate_standard_repn
from .highs_solver import HighsSolver


@dataclass
class DispatchSolution:
    model: object
    native_result: object
    metrics: dict


def _finite(value,name):
    if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(float(value)):
        raise ValueError(f"{name}: finite numeric value required")
    return float(value)


def _linear_model(model):
    supported={pyo.Block,pyo.Set,pyo.RangeSet,pyo.Param,pyo.Var,pyo.Constraint,pyo.Expression,pyo.Objective,pyo.Suffix}
    for component in model.component_objects(active=True,descend_into=True):
        if component.ctype not in supported:
            raise ValueError(f"unsupported active algebraic structure: {component.name}")
    objectives=list(model.component_data_objects(pyo.Objective,active=True,descend_into=True))
    if len(objectives)!=1 or objectives[0].sense!=pyo.minimize:
        raise ValueError("exactly one minimization objective required")
    expressions=[objectives[0].expr]+[c.body for c in model.component_data_objects(pyo.Constraint,active=True,descend_into=True)]
    for expr in expressions:
        repn=generate_standard_repn(expr,compute_values=True)
        if not repn.is_linear():raise ValueError("only linear/MILP dispatch models supported")
        for x in [repn.constant or 0.,*repn.linear_coefs]:_finite(x,'algebraic coefficient')
    return objectives[0]


def solve_dispatch(model, *, stage, time_limit_s, threads=1, mip_rel_gap=.001,
                   objective_offset_yuan=0., offset_source=None, tee=False):
    """Keep actual HiGHS termination separate from requested gap compliance.

    Metrics use original objective as canonical, and report the actual centered
    denominator separately. There is no algorithm/solver fallback.
    """
    if not isinstance(stage,str) or not stage.strip():raise ValueError('stage label required')
    limit=_finite(time_limit_s,'time limit')
    if limit<=0 or type(threads) is not int or threads<1:raise ValueError('positive time limit and integer threads required')
    gap=_finite(mip_rel_gap,'MIP gap')
    if not 0<=gap<=.001:raise ValueError('MIP gap cannot exceed requested 0.1%')
    offset=_finite(objective_offset_yuan,'objective offset')
    if offset and (not isinstance(offset_source,str) or not offset_source.strip()):raise ValueError('nonzero objective offset requires source')
    if type(tee) is not bool:raise ValueError('tee must be Boolean')
    objective=_linear_model(model)
    integer_refs=[str(ComponentUID(v)) for v in model.component_data_objects(pyo.Var,descend_into=True) if not v.fixed and (v.is_integer() or v.is_binary())]
    expression=objective.expr
    opt=HighsSolver(dict(threads=threads,time_limit=limit,mip_rel_gap=gap,output_flag=tee))
    started=time.perf_counter()
    try:
        objective.set_value(expression+offset)
        result=opt.solve(model,tee=tee)
        for variable in model.component_data_objects(pyo.Var,descend_into=True):
            _finite(variable.value,'HiGHS incumbent '+variable.name)
        shifted_objective=_finite(pyo.value(objective),'shifted incumbent objective')
        shifted_bound=_finite(result.problem.lower_bound,'HiGHS lower bound')
    finally:
        objective.set_value(expression)
    canonical=_finite(pyo.value(objective),'canonical incumbent objective')
    bound=shifted_bound-offset
    difference=canonical-bound
    tolerance=1e-5+1e-7*max(abs(canonical),abs(bound),1)
    if difference < -tolerance:raise RuntimeError('HiGHS bound above incumbent after objective restoration')
    absolute=max(0.,difference)
    centered_gap=absolute/max(1.,abs(shifted_objective))
    canonical_gap=absolute/max(1.,abs(canonical))
    status=str(result.solver.termination_condition)
    if status not in ('optimal','maxTimeLimit'):raise RuntimeError('no accepted dispatch solution')
    if not integer_refs and status!='optimal':raise RuntimeError('continuous dispatch LP must reach optimality')
    gap_met=centered_gap<=gap+1e-9
    original_result=SimpleNamespace(solver=SimpleNamespace(termination_condition=status),
        problem=SimpleNamespace(lower_bound=bound,upper_bound=canonical))
    metrics=dict(stage=stage,backend='highs',termination=status,time_s=time.perf_counter()-started,
        objective_yuan=canonical,best_bound_yuan=bound,absolute_gap_yuan=absolute,
        requested_mip_rel_gap=gap,requested_gap_met=gap_met,
        gap=centered_gap,gap_basis='max(1,abs(objective_plus_explicit_offset))',
        canonical_relative_gap=canonical_gap,objective_offset_yuan=offset,offset_source=offset_source,
        centered_objective_yuan=shifted_objective,centered_best_bound_yuan=shifted_bound,
        integer_variables=len(integer_refs),integer_refs=integer_refs,
        status_label=('optimal_lp' if not integer_refs else 'feasible_gap_not_met' if not gap_met else 'gap_target_met'),
        solver_version=getattr(opt,"solver_version",None),native_status=getattr(opt,"native_status",None),solver_settings=getattr(opt,"native_settings",None),
        gap_settings=getattr(opt,"gap_settings",None),native_mip_gap=getattr(opt,"native_mip_gap",None),dual_available=getattr(opt,"dual_available",None),
        exact_global_optimality_proved=False,time_settings=opt.time_settings)
    return DispatchSolution(model,original_result,metrics)


def polish_fixed_state(solution, *, time_limit_s, threads=1):
    """Clone incumbent, fix only integer variables, solve the same objective LP.

    Original MIP gap remains in provenance; this LP does not replace it.
    """
    if not isinstance(solution,DispatchSolution):raise ValueError('typed solved dispatch required')
    if solution.metrics.get('backend')!='highs' or solution.metrics.get('termination') not in ('optimal','maxTimeLimit'):
        raise ValueError('successful HiGHS incumbent required')
    model=solution.model.clone()
    for suffix in model.component_objects(pyo.Suffix,descend_into=True):suffix.clear()
    fixed=[]
    for variable in model.component_data_objects(pyo.Var,descend_into=True):
        if variable.is_integer() or variable.is_binary():
            if variable.value is None or not math.isfinite(float(variable.value)) or abs(variable.value-round(variable.value))>1e-5:
                raise ValueError('finite integer incumbent required before fixed-state polish')
            variable.fix(round(variable.value));fixed.append(str(ComponentUID(variable)))
    result=solve_dispatch(model,stage=solution.metrics['stage']+'_fixed_state_continuous_polish',time_limit_s=time_limit_s,threads=threads)
    tolerance=1e-5+1e-7*max(1,abs(solution.metrics['objective_yuan']))
    if result.metrics['objective_yuan']>solution.metrics['objective_yuan']+tolerance:
        raise RuntimeError('same-state continuous polish increased canonical objective')
    result.metrics.update(upstream_dispatch_metrics=dict(solution.metrics),polished_integer_refs=fixed,
        pricing_source=False,scope='same_objective_same_constraints_selected_integer_state_continuous_dispatch')
    return result

# Retained identity for native traceability; callers cannot replace the solver
# with a test witness and obtain native acceptance merely via backend labels.
_ORIGINAL_SOLVE_DISPATCH = solve_dispatch
