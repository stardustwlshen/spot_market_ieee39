from copy import deepcopy
from types import SimpleNamespace as NS
import numpy as np
import pyomo.environ as pyo
import pytest
from smm import scuc, rolling
from smm.da import run_da
from smm.hydro_spill_tiebreak import select_hydro_water, HydroWaterSelectionError, _dual_digest, _primal_violation
from smm.auxiliary_pricing import run_auxiliary_pricing
from test_hydro_rolling import hydro_case


def economic_case(*, qin=0., vmax=30., qeco=0.):
    c=hydro_case(T=1);h=c.cfg.units['hydro'][0]
    h.update(zones=[[0.,100.]],ramp=100.,init_p_unit=40.,qout_max=None,e_day_min=None)
    h['seasons']['wet'].update(qin=qin,qeco=qeco,v0=20.,vmin=10.,vmax=vmax,v_end=10.)
    c.bids['units']['H1']['segments'][0]['price']=100.
    c.load_bus={2:np.array([90.])};c.R_up[:]=0.;c.R_dn[:]=0.
    c.cfg.mode['reserve'].update(providers=[],storage_reserve=False)
    c.cfg.mode['auxiliary_pricing']['enabled']=False
    return c


def original_solve(c):
    m=scuc.build_model(c);scuc.solve_scuc(m);scuc.fix_integers(m);info=scuc.solve_sced(m)
    return m,info


def degenerate_model():
    m,info=original_solve(economic_case())
    assert pyo.value(m.Ph['H1',0])==pytest.approx(40.)
    m.Qspill['H1',0].set_value(10./.09-40.);m.V['H1',0].set_value(10.)
    assert _primal_violation(m)<1e-8
    return m,info


def all_values(m):
    return {v.name:(pyo.value(v),v.fixed) for v in m.component_data_objects(pyo.Var)}


def test_water_only_selection_keeps_cost_economic_values_structure_and_primary_duals():
    m,primary=degenerate_model();before=all_values(m);dual=_dual_digest(m);prices=scuc.extract_prices(m)['lmp'].copy()
    constraints=[c.name for c in m.component_data_objects(pyo.Constraint,active=True)]
    info=select_hydro_water(m,primary)
    assert pyo.value(m.Qspill['H1',0])==pytest.approx(0.)
    assert pyo.value(m.V['H1',0])==pytest.approx(16.4)
    assert info['spill_before_1e4m3']==pytest.approx(6.4)
    assert info['primary_objective_difference_yuan']==0.
    assert info['objective_tolerance_yuan']==1e-5
    assert _dual_digest(m)==dual
    np.testing.assert_array_equal(scuc.extract_prices(m)['lmp'],prices)
    assert [c.name for c in m.component_data_objects(pyo.Constraint,active=True)]==constraints
    for name,value in all_values(m).items():
        if not name.startswith(('V[','Qspill[')):assert value==before[name]
    assert not hasattr(m,'primary_optimal_face')
    assert not info['secondary_duals_used_for_prices']


@pytest.mark.parametrize('cause',['capacity','ecology','hard_target'])
def test_physically_or_reference_required_spill_not_erased(cause):
    if cause=='capacity':c=economic_case(qin=100.,vmax=20.)
    elif cause=='ecology':c=economic_case(qin=100.,qeco=80.)
    else:
        c=economic_case();c.terminal={'hydro_window_end':{'H1':10.}}
    m,primary=original_solve(c);info=select_hydro_water(m,primary)
    assert info['spill_after_1e4m3']>0
    assert _primal_violation(m)<1e-6


def test_primary_state_value_cost_cannot_be_spent_as_epsilon_budget():
    c=economic_case();c.stage='RT';c.terminal={'state_value':[('V','H1',0,-1.)]}
    m,primary=original_solve(c)
    # Negative terminal value makes extra stock economically costly; preserve it.
    original_volume=pyo.value(m.V['H1',0]);original_objective=pyo.value(m.obj)
    info=select_hydro_water(m,primary)
    assert pyo.value(m.obj)==pytest.approx(original_objective,abs=1e-5)
    assert pyo.value(m.V['H1',0])==pytest.approx(original_volume)
    assert info['spill_after_1e4m3']>0


def test_future_hydraulic_white_list_unblocks_current_spill_without_changing_future_power():
    from test_hydro_daily_energy import suffix_case
    c=suffix_case();c.cfg.units['hydro'][0]['seasons']['wet']['v0']=40.
    m,primary=original_solve(c);b=m.hydro_daily_energy_suffix
    # Shift a feasible 1e4m3 disposal from future/current slack into current
    # water while leaving every future power and integer coordinate unchanged.
    old_current=pyo.value(m.Qspill['H1',0]);old_V=pyo.value(m.V['H1',0])
    room=min([old_V-10.,*[pyo.value(b.V['H1',t])-10. for t in b.F]])
    # More robust witness: reoptimize with a positive current spill forced,
    # then remove only that temporary constraint before selecting the same
    # objective optimum (all hydropower in current window is fixed zero).
    m.force_spill=pyo.Constraint(expr=m.Qspill['H1',0]>=20.)
    primary=scuc.solve_sced(m);m.force_spill.deactivate()
    before={v.name:pyo.value(v) for comp in (b.Ph,b.delta,b.pz) for v in comp.values()}
    constrained=m.clone()
    for comp in (constrained.hydro_daily_energy_suffix.V,constrained.hydro_daily_energy_suffix.Qspill):
        for var in comp.values():var.fix(pyo.value(var))
    locked=select_hydro_water(constrained,primary)
    unlocked=select_hydro_water(m,primary)
    assert unlocked['spill_after_1e4m3']<locked['spill_after_1e4m3']-1e-6
    assert unlocked['spill_after_1e4m3']==pytest.approx(0.,abs=1e-6)
    assert {v.name:pyo.value(v) for comp in (b.Ph,b.delta,b.pz) for v in comp.values()}==before
    assert unlocked['suffix_scope'].startswith('water_feasibility_only')


def test_auxiliary_prices_use_selected_spill_but_not_secondary_duals():
    m,primary=degenerate_model();old=run_auxiliary_pricing(m)
    select_hydro_water(m,primary);new=run_auxiliary_pricing(m)
    assert new['hydro_spill_fixation'][0]['dispatch_spill_m3s']==pytest.approx(0.)
    assert not m.Qspill['H1',0].fixed
    assert all(pyo.value(new['model'].Qspill[h,t])==pytest.approx(pyo.value(m.Qspill[h,t])) for h in m.HS for t in m.T)
    slopes=[]
    for aux in (old,new):
        a=aux['model'];shift=a.clone();c=shift.c_inj[2,0];shift.c_inj[2,0].set_value(c.body==pyo.value(c.upper)+.001)
        scuc.solve_sced(shift);slopes.append((pyo.value(shift.obj)-pyo.value(a.obj))/.001/.25)
    assert slopes==pytest.approx([700.,100.],abs=1e-4)


@pytest.mark.parametrize('failure',['time','infeasible','bad_primal','economic_mutation'])
def test_selection_failures_are_transactional_and_not_physical_fallback(monkeypatch,failure):
    m,primary=degenerate_model();before=all_values(m);dual=_dual_digest(m);real=scuc._solve_with_budget
    def broken(model,opt,**kwargs):
        if failure=='time':return NS(solver=NS(termination_condition='maxTimeLimit'))
        if failure=='infeasible':raise RuntimeError('secondary infeasible')
        result=real(model,opt,**kwargs)
        if failure=='bad_primal':model.V['H1',0].set_value(1000.)
        else:model.P['LOCAL',0].set_value(1.)
        return result
    monkeypatch.setattr(scuc,'_solve_with_budget',broken)
    with pytest.raises(HydroWaterSelectionError) as e:select_hydro_water(m,primary)
    assert e.value.phase=='hydro_water_tiebreak'
    assert all_values(m)==before and _dual_digest(m)==dual
    assert not hasattr(m,'_hydro_water_selection')


def test_exhausted_budget_stops_without_model_change():
    m,primary=degenerate_model();before=all_values(m);m._solver_budget=scuc.SolverBudget(total_s=0.)
    with pytest.raises(scuc.SolverBudgetExceededError):select_hydro_water(m,primary)
    assert all_values(m)==before


def test_secondary_call_shares_real_budget_and_reserves_auxiliary_allowance():
    m,primary=degenerate_model();m._solver_budget=scuc.SolverBudget(total_s=20.,reserve_lp_s=3.)
    m._case.cfg.mode['auxiliary_pricing']['enabled']=True
    info=select_hydro_water(m,primary);ledger=m._solver_budget.report()
    assert ledger['calls'][0]['phase']=='hydro_water_tiebreak'
    assert ledger['calls'][0]['time_limit_s']==pytest.approx(min(17.,m._case.cfg.base['solver']['time_limit_s']))
    assert ledger['solver_elapsed_s']>0 and info['solver_time_s']>0


def test_missing_or_nonoptimal_original_economic_certificate_rejected():
    m,info=degenerate_model()
    for bad in (None,dict(info,termination='maxTimeLimit'),dict(info,objective=info['objective']+1.)):
        with pytest.raises(HydroWaterSelectionError):select_hydro_water(m,bad)


def test_copyback_exception_restores_already_copied_water(monkeypatch):
    m,primary=degenerate_model();before=all_values(m);dual=_dual_digest(m)
    target=m.V['H1',0];kind=type(target);real=kind.set_value;failed=[]
    def fail_once(self,value,skip_validation=False):
        if self is target and not skip_validation and not failed:
            failed.append(True);raise RuntimeError('simulated late copy-back failure')
        return real(self,value,skip_validation=skip_validation)
    monkeypatch.setattr(kind,'set_value',fail_once)
    with pytest.raises(HydroWaterSelectionError,match='restored'):select_hydro_water(m,primary)
    assert failed and all_values(m)==before and _dual_digest(m)==dual
    assert not hasattr(m,'_hydro_water_selection')


@pytest.fixture(scope='module')
def selection_export(tmp_path_factory):
    import json
    from smm import full
    c=hydro_case(T=8);c.cfg.mode['intraday']['enabled']=True
    c.cfg.mode['auxiliary_pricing']['enabled']=True
    result=full.run_full(c.cfg,c,periods=2,log=lambda *a:None)
    path=tmp_path_factory.mktemp('water_selection');summary=full.write_full_outputs(result,path,make_plots=False)
    return (c,result,summary,json.loads((path/'hydro_water_selection_evidence.json').read_text()),
            json.loads((path/'hydro_window_evidence.json').read_text()),
            json.loads((path/'stage_diagnostics.json').read_text()),path)


def test_full_formal_reference_state_and_auxiliary_export_use_selected_water(selection_export):
    from smm.hydro_spill_tiebreak import audit_selection_evidence
    c,result,summary,ev,water,stages,path=selection_export
    checked=audit_selection_evidence(ev,water,stages,c.cfg.units['hydro'],2,1)
    assert checked==summary['hydro_water_selection'] and checked['verified']
    assert ev['id_windows'][0]['certificate']['stage']=='ID'
    first=ev['rt_windows'][0]['certificate']['rows'][0]
    assert result['rr'].states[1].hydro[first['station']]==pytest.approx(first['volume_after_1e4m3'])
    da_plan=rolling._da_plan_arrays(result['da'])
    assert da_plan['Vh']['H1'][0]==pytest.approx(ev['da']['rows'][0]['volume_after_1e4m3'])
    for r in result['rr'].rt:
        values={(x['station'],x['relative_period']):x['spill_after_m3s'] for x in r['sced']['hydro_water_selection']['rows']}
        for x in r['pricing_info']['hydro_spill_fixation']:
            assert x['dispatch_spill_m3s']==pytest.approx(values[x['station'],x['relative_period']])


@pytest.mark.parametrize('damage',['rule','missing_id','secondary_dual','primary_cost','tolerance','volume','spill','dual_hash','dual_residual','economic_change'])
def test_selection_evidence_rejects_tampering_and_missing_windows(selection_export,damage):
    from smm.hydro_spill_tiebreak import audit_selection_evidence
    c,result,summary,original,water,stages,path=selection_export;ev=deepcopy(original);rec=ev['da']
    if damage=='rule':ev.pop('rule')
    elif damage=='missing_id':ev['id_windows']=[]
    elif damage=='secondary_dual':rec['secondary_duals_used_for_prices']=True
    elif damage=='primary_cost':rec['primary_objective_after_yuan']+=1.
    elif damage=='tolerance':rec['objective_tolerance_yuan']=1000.
    elif damage=='volume':rec['rows'][0]['volume_after_1e4m3']+=1.
    elif damage=='spill':rec['rows'][0]['spill_after_m3s']+=1.
    elif damage=='dual_hash':rec['primary_duals_sha256']='wrong'
    elif damage=='dual_residual':rec['selected_water_dual_certificate']['water_complementarity_yuan']=1.
    else:rec['frozen_economic_max_difference']=1.
    with pytest.raises((ValueError,KeyError)):
        audit_selection_evidence(ev,water,stages,c.cfg.units['hydro'],2,1)


def test_exact_zero_spill_is_certified_without_spending_solver_budget(monkeypatch):
    m,info=original_solve(economic_case());m.Qspill['H1',0].set_value(0.);m.V['H1',0].set_value(16.4)
    def forbidden(*a,**k):raise AssertionError('No solver needed at known zero lower bound')
    monkeypatch.setattr(scuc,'_solve_with_budget',forbidden)
    m._solver_budget=scuc.SolverBudget(total_s=0.)
    result=select_hydro_water(m,info)
    assert result['secondary_method']=='zero_lower_bound_certificate'
    assert result['solver_time_s']==0. and not m._solver_budget.calls
