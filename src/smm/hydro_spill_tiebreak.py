"""Select water-only primal values on the original fixed-integer LP optimum.

The secondary LP never supplies market duals. It cannot alter economic dispatch,
soften constraints, expand the solver budget, or silently return a fallback.
"""
from __future__ import annotations
import hashlib
import json
import math
import time

import pyomo.environ as pyo
from pyomo.core.expr.visitor import identify_variables
from pyomo.repn.standard_repn import generate_standard_repn

RULE = 'fixed_economic_dispatch_primary_face_minimum_window_hydro_spill_v1'
PHYSICAL_TOL = 1e-6
DUAL_TOL = 1e-6
WATER_COMPONENTS = ('V', 'Qspill', 'hydro_daily_energy_suffix.V', 'hydro_daily_energy_suffix.Qspill')


class HydroWaterSelectionError(RuntimeError):
    """Selection failed; original model is intact and must not execute as success."""
    phase = 'hydro_water_tiebreak'


def _number(value):
    value = float(pyo.value(value))
    if not math.isfinite(value):
        raise HydroWaterSelectionError('Non-finite primary/secondary water-selection evidence')
    return value


def _dual_digest(m):
    rows = sorted((con.name, _number(value)) for con, value in m.dual.items())
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()


def _primal_violation(m, ignore=()):
    worst = 0.
    for v in m.component_data_objects(pyo.Var, active=True):
        value = _number(v)
        if v.has_lb(): worst = max(worst, _number(v.lb)-value)
        if v.has_ub(): worst = max(worst, value-_number(v.ub))
    for con in m.component_data_objects(pyo.Constraint, active=True):
        if con.name in ignore: continue
        value = _number(con.body)
        if con.has_lb(): worst = max(worst, _number(con.lower)-value)
        if con.has_ub(): worst = max(worst, value-_number(con.upper))
    return max(0., worst)


def _water_dual_certificate(m, variables):
    """Changed primal coordinates only; all original duals/coefficients survive.

    Other primal coordinates do not change, so their original optimality
    certificate is inherited, not claimed to have been independently recomputed.
    """
    wanted = {id(v): v for v in variables}
    rc = {key: 0. for key in wanted}
    rep = generate_standard_repn(m.obj.expr)
    if not rep.is_linear(): raise HydroWaterSelectionError('Primary objective is not linear')
    for v, coef in zip(rep.linear_vars, rep.linear_coefs):
        if id(v) in wanted: rc[id(v)] += _number(coef)
    comp = 0.; dual_error = 0.; affected = 0
    for con in m.component_data_objects(pyo.Constraint, active=True):
        if not any(id(v) in wanted for v in identify_variables(con.body, include_fixed=False)):
            continue
        affected += 1
        if con not in m.dual:
            raise HydroWaterSelectionError(f'Missing original economic dual: {con.name}')
        dual = _number(m.dual[con]);body = _number(con.body)
        rep = generate_standard_repn(con.body)
        if not rep.is_linear(): raise HydroWaterSelectionError(f'Nonlinear water row: {con.name}')
        for v, coef in zip(rep.linear_vars, rep.linear_coefs):
            if id(v) in wanted: rc[id(v)] -= dual*_number(coef)
        if not con.equality:
            if dual > 0:
                if not con.has_lb(): dual_error = max(dual_error, dual)
                else: comp += abs(dual*(body-_number(con.lower)))
            elif dual < 0:
                if not con.has_ub(): dual_error = max(dual_error, -dual)
                else: comp += abs(dual*(_number(con.upper)-body))
    for key, reduced_cost in rc.items():
        var = wanted[key];value = _number(var)
        if reduced_cost > 0:
            if not var.has_lb(): dual_error = max(dual_error,reduced_cost)
            else: comp += abs(reduced_cost*(value-_number(var.lb)))
        elif reduced_cost < 0:
            if not var.has_ub(): dual_error = max(dual_error,-reduced_cost)
            else: comp += abs(reduced_cost*(_number(var.ub)-value))
    return dict(affected_constraint_count=affected,water_dual_feasibility_max=dual_error,
                water_complementarity_yuan=comp,
                scope='water_coordinates_rechecked;other_coordinates_and_all_primary_duals_unchanged')


def select_hydro_water(m, primary_info):
    """Transactional second LP. Raise on every uncertified/unfinished selection."""
    start = time.perf_counter()
    base = dict(rule=RULE,version=1,stage=m._case.stage,window_start=m._case.t0,periods=len(m.T),
        failure_policy='stop_no_fallback_no_softening',primary_dual_source='original_economic_sced',
        secondary_duals_used_for_prices=False,objective_scope='main_dispatch_window_only',
        suffix_scope='water_feasibility_only_not_executed_or_spill_minimum_certified')
    if not len(m.HS):
        info=dict(base,status='not_applicable_no_hydro',time_s=0.,solver_time_s=0.)
        m._hydro_water_selection=info
        return info
    if (not isinstance(primary_info,dict) or str(primary_info.get('termination','')).lower()!='optimal'
            or not hasattr(m,'dual')):
        raise HydroWaterSelectionError('Water selection requires an optimal original economic LP with duals')
    primary = _number(m.obj);tol=max(1e-5,1e-10*max(abs(primary),1.))
    if abs(_number(primary_info['objective'])-primary)>tol:
        raise HydroWaterSelectionError('Original economic objective does not match its solver certificate')
    before={v.name:(_number(v),bool(v.fixed)) for v in m.component_data_objects(pyo.Var,active=True)}
    movable=[]
    for name in WATER_COMPONENTS:
        component=m.find_component(name)
        if component is not None: movable.extend(v for v in component.values() if not v.fixed)
    names={v.name for v in movable}
    if not names: raise HydroWaterSelectionError('Hydro model has no free water coordinates to select')
    dual_hash=_dual_digest(m)
    original_residual=_primal_violation(m)
    original_kkt=_water_dual_certificate(m,movable)
    if (original_residual>PHYSICAL_TOL or original_kkt['water_dual_feasibility_max']>DUAL_TOL
            or original_kkt['water_complementarity_yuan']>tol):
        raise HydroWaterSelectionError(f'Original water optimality certificate failed: residual={original_residual}, {original_kkt}')
    already_zero=all(_number(m.Qspill[h,t])==0. for h in m.HS for t in m.T)
    if already_zero:
        candidate=m;primary_after=primary;secondary_residual=original_residual
        final_kkt=original_kkt;solver_elapsed=0.
    else:
        secondary=m.clone()
        if hasattr(m,'_solver_budget'): secondary._solver_budget=m._solver_budget
        for v in secondary.component_data_objects(pyo.Var,active=True):
            if v.name not in names: v.fix(before[v.name][0])
        primary_expr=secondary.obj.expr
        secondary.primary_optimal_face=pyo.Constraint(expr=(primary,primary_expr,primary))
        secondary.obj.set_value(.36*m._md.dt*sum(secondary.Qspill[h,t] for h in m.HS for t in m.T))
        from . import scuc
        opt=scuc._solver(m._case.cfg,mip=False)
        solver_start=time.perf_counter()
        try:
            result=scuc._solve_with_budget(secondary,opt,phase='hydro_water_tiebreak',
                reserve_lp=bool(m._case.cfg.mode.get('auxiliary_pricing',{}).get('enabled',False)))
        except scuc.SolverBudgetExceededError as exc:
            exc.phase='hydro_water_tiebreak'
            exc.args=('hydro_water_tiebreak: '+str(exc),)
            raise
        except Exception as exc:
            raise HydroWaterSelectionError(f'Water selection failed without changing the primary model: {exc}') from exc
        solver_elapsed=time.perf_counter()-solver_start
        if str(result.solver.termination_condition).lower()!='optimal':
            raise HydroWaterSelectionError(f'Water selection not optimal: {result.solver.termination_condition}')
        primary_after=_number(primary_expr)
        if abs(primary_after-primary)>tol:
            raise HydroWaterSelectionError(f'Original objective moved by {primary_after-primary} yuan; tolerance={tol}')
        if any(_number(v)!=before[v.name][0] for v in secondary.component_data_objects(pyo.Var,active=True) if v.name not in names):
            raise HydroWaterSelectionError('Secondary solver changed a frozen economic coordinate')
        secondary_residual=_primal_violation(secondary,ignore={'primary_optimal_face'})
        if secondary_residual>PHYSICAL_TOL:
            raise HydroWaterSelectionError(f'Secondary physical/face residual {secondary_residual} exceeds {PHYSICAL_TOL}')
        # Certify candidate against an unchanged original economic model, never the
        # secondary objective or its fixed-variable / optimal-face duals.
        candidate=secondary
        candidate.primary_optimal_face.deactivate()
        candidate.obj.set_value(primary_expr)
        candidate.dual.clear()
        for con,value in m.dual.items(): candidate.dual[candidate.find_component(con.name)]=value
        selected=[candidate.find_component(v.name) for v in movable]
        final_kkt=_water_dual_certificate(candidate,selected)
        if final_kkt['water_dual_feasibility_max']>DUAL_TOL or final_kkt['water_complementarity_yuan']>tol:
            raise HydroWaterSelectionError(f'Candidate is not certified by original economic duals: {final_kkt}')
    rows=[]
    for h in m.HS:
        for t in m.T:
            rows.append(dict(station=h,relative_period=int(t),period=m._case.t0+int(t),
                spill_before_m3s=before[m.Qspill[h,t].name][0],spill_after_m3s=_number(candidate.Qspill[h,t]),
                volume_before_1e4m3=before[m.V[h,t].name][0],volume_after_1e4m3=_number(candidate.V[h,t])))
    spill_before=.36*m._md.dt*sum(row['spill_before_m3s'] for row in rows)
    spill_after=.36*m._md.dt*sum(row['spill_after_m3s'] for row in rows)
    if spill_after>spill_before+PHYSICAL_TOL:
        raise HydroWaterSelectionError('Secondary minimum increased main-window spill')
    if _dual_digest(candidate)!=dual_hash or _dual_digest(m)!=dual_hash:
        raise HydroWaterSelectionError('Original economic duals changed during selection')
    info=dict(base,status='optimal',secondary_method=('zero_lower_bound_certificate' if already_zero else 'linear_program'),
        primary_objective_before_yuan=primary,
        primary_objective_after_yuan=primary_after,objective_tolerance_yuan=tol,
        primary_objective_difference_yuan=primary_after-primary,primary_duals_sha256=dual_hash,
        original_constraint_violation_max=original_residual,selected_constraint_violation_max=secondary_residual,
        physical_tolerance=PHYSICAL_TOL,dual_tolerance=DUAL_TOL,
        original_water_dual_certificate=original_kkt,selected_water_dual_certificate=final_kkt,
        frozen_economic_coordinate_count=len(before)-len(names),frozen_economic_max_difference=0.,
        movable_water_coordinate_count=len(names),movable_components=list(WATER_COMPONENTS),
        spill_before_1e4m3=spill_before,spill_after_1e4m3=spill_after,
        solver_time_s=solver_elapsed,time_s=time.perf_counter()-start,rows=rows,
        auxiliary_policy='price_after_selection_with_selected_formal_spill;prices_may_change')
    # Only the validated water primal is transferred. Constraints, objective,
    # fixed flags and all economic dual suffix entries stay on the original LP.
    try:
        for name in sorted(names): m.find_component(name).set_value(_number(candidate.find_component(name)))
    except Exception as exc:
        for name in names: m.find_component(name).set_value(before[name][0],skip_validation=True)
        raise HydroWaterSelectionError('Water copy-back failed; original values restored') from exc
    m._hydro_water_selection=info
    return info


def audit_selection_evidence(payload, hydro_evidence, stages, hydro, expected_rt, expected_ids):
    """Arithmetic/coverage and formal-state linkage audit, not an LP re-solve."""
    def finite(value):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('Invalid water-selection numeric evidence')
        return float(value)
    if payload.get('version')!=1 or payload.get('rule')!=RULE:
        raise ValueError('Missing water-selection evidence version/rule')
    rt=payload.get('rt_windows');ids=payload.get('id_windows')
    if not isinstance(rt,list) or not isinstance(ids,list) or len(rt)!=expected_rt or len(ids)!=expected_ids:
        raise ValueError('Incomplete water-selection window coverage')
    evidence={('DA',0):hydro_evidence['da']}
    evidence.update({('RT',r['t']):r['snapshot'] for r in hydro_evidence['rt_windows']})
    evidence.update({('ID',r['tau']):r['snapshot'] for r in hydro_evidence['id_rounds']})
    stage_rows={('DA',0):stages['da']}
    stage_rows.update({('RT',r['t']):r['stages'] for r in stages['rt_windows']})
    stage_rows.update({('ID',r['tau']):r['stages'] for r in stages.get('id_rounds',[])})
    records=[('DA',0,payload['da'])]+[('ID',r['window_start'],r['certificate']) for r in ids]+[('RT',r['window_start'],r['certificate']) for r in rt]
    if len({(stage,start) for stage,start,_ in records})!=len(records):
        raise ValueError('Duplicate water-selection windows')
    if {r['window_start'] for r in rt}!=set(range(expected_rt)):
        raise ValueError('Water-selection RT execution indices incomplete')
    count=lp_count=zero_count=0;before_total=after_total=solver_time=wall_time=0.
    units={h['id']:h for h in hydro}
    for stage,start,c in records:
        if (not isinstance(c,dict) or c.get('rule')!=RULE or c.get('version')!=1
                or c.get('stage')!=stage or c.get('window_start')!=start
                or c.get('primary_dual_source')!='original_economic_sced'
                or c.get('secondary_duals_used_for_prices') is not False
                or c.get('failure_policy')!='stop_no_fallback_no_softening'):
            raise ValueError('Missing/inconsistent water-selection policy certificate')
        if not units:
            if c.get('status')!='not_applicable_no_hydro':raise ValueError('Unexpected hydro selection status')
            continue
        if c.get('status')!='optimal' or c.get('secondary_method') not in ('linear_program','zero_lower_bound_certificate'):
            raise ValueError('Water selection did not finish optimally')
        T=c['periods'];sn=evidence[(stage,start)];econ=stage_rows[(stage,start)]['energy_sced']['summary']
        a=finite(c['primary_objective_before_yuan']);b=finite(c['primary_objective_after_yuan'])
        tol=max(1e-5,1e-10*max(abs(a),1.))
        if (finite(c['objective_tolerance_yuan'])!=tol or abs(b-a)>tol
                or abs(finite(c['primary_objective_difference_yuan'])-(b-a))>1e-9
                or abs(finite(econ['objective'])-a)>tol):
            raise ValueError('Original economic objective equality not certified')
        if c.get('physical_tolerance')!=PHYSICAL_TOL or c.get('dual_tolerance')!=DUAL_TOL:
            raise ValueError('Water-selection tolerance altered')
        for key in ('original_constraint_violation_max','selected_constraint_violation_max','frozen_economic_max_difference'):
            if not 0<=finite(c[key])<=PHYSICAL_TOL:raise ValueError('Water-selection physical/economic invariant failed')
        for key in ('original_water_dual_certificate','selected_water_dual_certificate'):
            cert=c[key]
            if not 0<=finite(cert['water_dual_feasibility_max'])<=DUAL_TOL or not 0<=finite(cert['water_complementarity_yuan'])<=tol:
                raise ValueError('Water-coordinate original-dual certificate failed')
        digest=c['primary_duals_sha256']
        if not isinstance(digest,str) or len(digest)!=64 or any(x not in '0123456789abcdef' for x in digest):
            raise ValueError('Invalid original-dual digest')
        rows=c['rows'];pairs={(r['station'],r['relative_period']) for r in rows}
        if len(pairs)!=len(rows) or pairs!={(h,t) for h in units for t in range(T)}:
            raise ValueError('Incomplete water-selection station/period grid')
        vol_before=vol_after=0.
        for row in rows:
            h=row['station'];t=row['relative_period']
            if row['period']!=start+t:raise ValueError('Water-selection absolute period mismatch')
            vb=finite(row['volume_before_1e4m3']);va=finite(row['volume_after_1e4m3'])
            sb=finite(row['spill_before_m3s']);sa=finite(row['spill_after_m3s'])
            if min(sb,sa)<-PHYSICAL_TOL:raise ValueError('Negative water-selection spill')
            if abs(va-finite(sn['Vh'][h][t]))>PHYSICAL_TOL:
                raise ValueError('Selected volume differs from formal reference/execution')
            formal_spill=finite(sn['Qout'][h][t])-units[h]['water_rate']*finite(sn['Ph'][h][t])
            if abs(sa-formal_spill)>PHYSICAL_TOL:
                raise ValueError('Selected spill differs from formal reference/execution')
            vol_before+=sb*.36*hydro_evidence['dt_h'];vol_after+=sa*.36*hydro_evidence['dt_h']
        if (abs(vol_before-finite(c['spill_before_1e4m3']))>PHYSICAL_TOL
                or abs(vol_after-finite(c['spill_after_1e4m3']))>PHYSICAL_TOL or vol_after>vol_before+PHYSICAL_TOL):
            raise ValueError('Water-selection spill totals inconsistent/increased')
        if c['secondary_method']=='zero_lower_bound_certificate' and (vol_before!=0. or vol_after!=0. or finite(c['solver_time_s'])!=0.):
            raise ValueError('Zero-spill lower-bound certificate is inconsistent')
        count+=1
        lp_count+=int(c['secondary_method']=='linear_program');zero_count+=int(c['secondary_method']=='zero_lower_bound_certificate')
        before_total+=vol_before;after_total+=vol_after
        solve_elapsed=finite(c['solver_time_s']);elapsed=finite(c['time_s'])
        if min(solve_elapsed,elapsed)<0 or elapsed+1e-6<solve_elapsed:raise ValueError('Invalid water-selection timing evidence')
        solver_time+=solve_elapsed;wall_time+=elapsed
    return dict(version=1,rule=RULE,verified=True,windows=len(records),active_windows=count,
        lp_solved_windows=lp_count,zero_lower_bound_windows=zero_count,
        solver_time_s=solver_time,selection_wall_time_s=wall_time,
        planning_window_spill_before_1e4m3=before_total,planning_window_spill_after_1e4m3=after_total,
        scope='DA_ID_RT_window_evidence;overlapping_forecasts_not_executed_daily_totals;not_independent_optimality_resolve')
