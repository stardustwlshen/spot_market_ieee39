"""RT-only finite terminal preferences and a priced, integer asset suffix.

Small real solver models only. They do not replace full-day market validation.
"""
from copy import deepcopy

import numpy as np
import pyomo.environ as pyo
import pytest
from pyomo.repn import generate_standard_repn

from smm import scuc
from smm.asset_reachability import diagnostics
from smm.auxiliary_pricing import run_auxiliary_pricing
from test_asset_reachability import raw_status, reachability_case, solve
from test_hydro_rolling import hydro_case, assert_water_and_bounds
from test_storage_reserve import battery_case, pumped_case
from test_value_asset_reachability import trapped_case


POLICY = dict(mode='soft', penalty_yuan_per_discharge_mwh=1000.)


def terminal_case(kind='E', delta=10., *, policy=POLICY, emergency=False):
    if kind == 'E':
        c=battery_case('idle',enabled=False)
        unit,stock,upper='ES1',100.,100.+delta
    elif kind == 'Vu':
        c=pumped_case('idle',enabled=False)
        unit,stock,upper='PS1',110.,110.+delta
    else:
        c=hydro_case(T=1)
        c.terminal={'fix_power':{'Ph':{('H1',0):50.}}}
        unit,stock,upper='H1',100.,200.
    c.stage='RT';c.R_up=np.zeros(1);c.R_dn=np.zeros(1)
    c.terminal['state_band']=[(kind,unit,0,stock+delta,upper,'day_end')]
    if policy is not None:
        c.terminal['state_band_terminal_policy']=deepcopy(policy)
    if emergency:c.terminal.update(state_band_soft=True,state_band_penalty=1e5)
    return c


def build_terminal(*args, **kwargs):
    m=scuc.build_model(terminal_case(*args,**kwargs))
    for h in m.HS:m.Qspill[h,0].fix(0.)
    return m


@pytest.mark.parametrize('kind,factor',[('E',.9),('Vu',10.),('V',1/.36)])
@pytest.mark.parametrize('delta',[-10.,10.])
def test_native_cost_conversion_battery_pumped_two_sided_hydro_one_sided(kind,factor,delta):
    m=solve(build_terminal(kind,delta))
    assert m._state_band_terminal_policy==POLICY
    assert m._state_band_soft is False  # planned soft target is not an emergency retry
    assert m._state_band_soft_indices=={0}
    assert m._state_band_energy_factors[0]==pytest.approx(factor)
    coefficient=m._state_band_cost_coefficients[0]
    assert coefficient==pytest.approx(1000.*factor)
    lower=pyo.value(m.sB_dn[0]);upper=pyo.value(m.sB_up[0])
    assert lower==pytest.approx(max(delta,0.))
    assert upper==pytest.approx(max(-delta,0.) if kind!='V' else 0.)
    assert coefficient*(upper+lower)==pytest.approx(1000.*factor*(abs(delta) if kind!='V' else max(delta,0.)))
    if kind=='V':
        assert m.sB_up[0].fixed
        assert_water_and_bounds(m)
    repn=generate_standard_repn(m.obj.expr)
    coefficients={id(v):c for v,c in zip(repn.linear_vars,repn.linear_coefs)}
    assert coefficients[id(m.sB_dn[0])]==pytest.approx(1000.*factor)  # no extra .25
    before=pyo.value(m.obj)
    scuc.set_penalties(m,dict(m._pen))
    assert pyo.value(m.obj)==pytest.approx(before)


@pytest.mark.parametrize('policy',[None,dict(mode='hard')])
def test_missing_or_hard_policy_preserves_exact_target(policy):
    assert raw_status(build_terminal('E',10.,policy=policy))==('infeasible',False)


@pytest.mark.parametrize('stage',['DA','ID'])
def test_soft_terminal_policy_cannot_leak_into_day_ahead_or_intraday(stage):
    c=terminal_case();c.stage=stage
    with pytest.raises(ValueError,match='仅可用于RT'):scuc.build_model(c)


@pytest.mark.parametrize('bad',[None,True,0.,-1.,float('nan'),float('inf'),'1000'])
def test_invalid_soft_terminal_penalty_fails_closed(bad):
    with pytest.raises(ValueError,match='罚价'):
        build_terminal(policy=dict(mode='soft',penalty_yuan_per_discharge_mwh=bad))


def test_normal_softening_only_applies_to_day_end_rows_and_checks_absolute_time():
    c=terminal_case();c.cfg.base['periods']=2;c.T=1
    with pytest.raises(ValueError,match='运行日最后'):scuc.build_model(c)
    c=terminal_case();row=c.terminal['state_band'][0]
    c.terminal['state_band']=[(*row[:-1],'band')]
    m=scuc.build_model(c)
    assert m._state_band_soft_indices==set()
    assert m.sB_up[0].fixed and m.sB_dn[0].fixed
    assert m._state_band_energy_factors[0] is None


def test_planned_terminal_cost_unchanged_when_other_band_uses_emergency_fallback():
    c=terminal_case(emergency=True)
    c.terminal['state_band'].append(('E','ES1',0,120.,120.,'band'))
    m=solve(scuc.build_model(c))
    assert m._state_band_soft and m._state_band_soft_indices=={0,1}
    assert m._state_band_cost_coefficients=={0:900.,1:1e5}
    assert pyo.value(m.sB_dn[0])==pytest.approx(10.)
    assert pyo.value(m.sB_dn[1])==pytest.approx(20.)


def test_soft_terminal_cannot_relax_physical_soc_upper_bound():
    m=build_terminal();m.E['ES1',0].fix(181.)
    assert raw_status(m)==('infeasible',False)


def soft_trapped_case(**kwargs):
    c=trapped_case(**kwargs);c.R_up=np.zeros(1);c.R_dn=np.zeros(1)
    c.terminal['asset_reachability'].update(target_policy='day_end_soft',penalty_yuan_per_discharge_mwh=1000.)
    return c


def test_inexact_ps_end_state_remains_feasible_without_fractional_pumps_or_extra_starts():
    assert raw_status(scuc.build_model(trapped_case()))==('infeasible',False)
    m=solve(scuc.build_model(soft_trapped_case()))
    b=m.asset_reachability;d=diagnostics(m);r=d['future_targets'][0]
    expected_error=200.11390371625168-200.
    assert r['error']==pytest.approx(expected_error)
    assert r['discharge_mwh_per_native_unit']==pytest.approx(9.6)
    assert r['terminal_penalty_cost']==pytest.approx(expected_error*9.6*1000.)
    assert r['penalty_yuan_per_native_unit']==pytest.approx(9600.)
    assert d['terminal_penalty_cost']==pytest.approx(r['terminal_penalty_cost'])
    assert d['soft_target'] and not d['zero_objective_weight'] and not d['quantities_execute']
    assert 'finite_cost' in d['scope'] and d['cost_scope']=='terminal_deviation_only_no_future_energy_bid_cost'
    assert len(m.T)==1 and len(b.F)==15
    assert d['pumped_start_budget']['PS1']['pump']['remaining']==0.
    assert all(pyo.value(b.Pp['PS1',t])==0 for t in b.F)
    assert all(v.fixed and pyo.value(v)==round(pyo.value(v)) for _,var in scuc.integer_components(m) for v in var.values())
    repn=generate_standard_repn(m.obj.expr)
    coefficients={id(v):c for v,c in zip(repn.linear_vars,repn.linear_coefs)}
    assert coefficients[id(b.target_up[0])]==pytest.approx(9600.)
    assert coefficients[id(b.target_dn[0])]==pytest.approx(9600.)


def test_suffix_respects_bid_charge_capacity_and_reports_unavoidable_shortfall():
    c=battery_case('idle',enabled=False);c.R_up=np.zeros(1);c.R_dn=np.zeros(1)
    c.cfg.base['periods']=8;c.T=1;c.stage='RT'
    c.bids['units']['ES1']['ch_segments']=[dict(p_start=0.,p_end=1.,price=0.)]
    c.terminal['asset_reachability']=dict(target_policy='day_end_soft',penalty_yuan_per_discharge_mwh=1000.,
        start_period=1,end_period_exclusive=8,storage_targets={'ES1':[(6,180.,180.)]},pumped_targets={})
    m=solve(scuc.build_model(c));b=m.asset_reachability;r=diagnostics(m)['future_targets'][0]
    assert pyo.value(b.E['ES1',7])==pytest.approx(101.4)
    assert r['deviation_dn_native']==pytest.approx(78.6)
    assert r['terminal_penalty_cost']==pytest.approx(78.6*.9*1000.)
    for t in b.F:
        assert pyo.value(b.Pc['ES1',t])<=1.+1e-8
        assert pyo.value(b.u_ch['ES1',t]+b.u_dis['ES1',t])<=1.


def test_soft_suffix_retains_cross_window_mode_switch_gap():
    c=reachability_case();c.cfg.base['periods']=8;c.t0=6
    c.fixed={n:{('PS1',0):v} for n,v in [('xg',1),('xp',0),('ngen',1),('npump',0)]}
    c.terminal.update(fix_power={'Pg':{('PS1',0):2.},'Pp':{('PS1',0):0.}},
        asset_reachability=dict(target_policy='day_end_soft',penalty_yuan_per_discharge_mwh=1000.,
          start_period=7,end_period_exclusive=8,storage_targets={},pumped_targets={'PS1':[(0,110.5,110.5)]}))
    c.init=dict(thermal={u['id']:deepcopy(u['init']) for u in c.cfg.units['thermal']},hydro={},
        pumped={'PS1':dict(V=110.,ng=1,np=0,xg=1,xp=0)},pumped_used={'PS1':dict(gen=1.,pump=0.)},
        pumped_hist={'PS1':dict(xg=np.ones(6),xp=np.zeros(6))})
    m=solve(scuc.build_model(c));b=m.asset_reachability
    assert pyo.value(b.xp['PS1',1])==0.
    assert diagnostics(m)['future_targets'][0]['error']==pytest.approx(-1.)


@pytest.mark.parametrize('bad',[True,0.,-1.,float('nan'),None])
def test_suffix_rejects_invalid_penalty(bad):
    c=soft_trapped_case();c.terminal['asset_reachability']['penalty_yuan_per_discharge_mwh']=bad
    with pytest.raises(ValueError,match='罚价'):scuc.build_model(c)


def test_soft_suffix_requires_exactly_one_complete_day_end_reference():
    c=soft_trapped_case();c.terminal['asset_reachability']['pumped_targets']['PS1'].append((6,200.,200.))
    with pytest.raises(ValueError,match='仅完整覆盖日末'):scuc.build_model(c)


def test_auxiliary_preserve_keeps_soft_suffix_cost_without_mutating_dispatch():
    c=soft_trapped_case();c.cfg.mode['auxiliary_pricing']['rt_tracking_policy']='preserve'
    m=solve(scuc.build_model(c));before=pyo.value(m.obj)
    aux=run_auxiliary_pricing(m)['model']
    assert aux.asset_reachability.active
    assert pyo.value(aux.asset_reachability.terminal_penalty_cost)==pytest.approx(pyo.value(m.asset_reachability.terminal_penalty_cost))
    assert pyo.value(m.obj)==pytest.approx(before)


def economic_terminal_case(penalty, *, negative_prices=False):
    c=terminal_case('E',0.,policy=dict(mode='soft',penalty_yuan_per_discharge_mwh=penalty))
    c.fixed={};c.terminal.pop('fix_power')
    c.terminal['state_value']=[('E','ES1',0,0.)]
    if negative_prices:
        c.load_bus={2:np.zeros(1)}
        for uid in ('CHEAP','LOCAL'):
            c.bids['units'][uid]['segments'][0]['price']=-50.
    return c


def test_finite_terminal_preference_can_retain_or_release_energy_without_salvage():
    low=solve(scuc.build_model(economic_terminal_case(100.)))
    high=solve(scuc.build_model(economic_terminal_case(1000.)))
    assert pyo.value(low.E['ES1',0])<100.-1e-6
    assert pyo.value(high.E['ES1',0])==pytest.approx(100.)
    assert pyo.value(low.sB_dn[0])>pyo.value(high.sB_dn[0])+1e-6
    assert low._state_values==high._state_values==[('E','ES1',0,0.)]
    assert scuc.state_values(low)['E']['ES1']['value_native'].tolist()==[0.]
    assert pyo.value(low.u_ch['ES1',0]+low.u_dis['ES1',0])<=1.


def test_negative_price_charge_surplus_is_finite_cost_not_forced_exact_matching():
    low=solve(scuc.build_model(economic_terminal_case(100.,negative_prices=True)))
    high=solve(scuc.build_model(economic_terminal_case(1000.,negative_prices=True)))
    assert pyo.value(low.E['ES1',0])>100.+1e-6
    assert pyo.value(high.E['ES1',0])==pytest.approx(100.)
    assert pyo.value(low.sB_up[0])>pyo.value(high.sB_up[0])+1e-6
    assert 20.-1e-6<=pyo.value(low.E['ES1',0])<=180.+1e-6


def test_auxiliary_remove_drops_suffix_cost_only_in_clone_without_unbounded_slacks():
    c=soft_trapped_case();c.cfg.mode['auxiliary_pricing']['rt_tracking_policy']='remove'
    m=solve(scuc.build_model(c));before=pyo.value(m.obj)
    aux=run_auxiliary_pricing(m)['model']
    assert not aux.asset_reachability.active and m.asset_reachability.active
    repn=generate_standard_repn(aux.obj.expr)
    coef={id(v):c for v,c in zip(repn.linear_vars,repn.linear_coefs)}
    for var in (aux.asset_reachability.target_up,aux.asset_reachability.target_dn):
        assert all(coef.get(id(v),0.)==0. for v in var.values())
    assert pyo.value(m.obj)==pytest.approx(before)
    assert np.isfinite(pyo.value(aux.obj))
    scuc.set_penalties(aux,dict(aux._pen))
    rebuilt=generate_standard_repn(aux.obj.expr)
    rebuilt_coef={id(v):c for v,c in zip(rebuilt.linear_vars,rebuilt.linear_coefs)}
    assert all(rebuilt_coef.get(id(v),0.)==0. for v in aux.asset_reachability.target_up.values())


def test_auxiliary_remove_drops_planned_terminal_cost_but_not_physical_bounds():
    m=solve(build_terminal('E',10.));before=pyo.value(m.obj)
    m._case.cfg.mode['auxiliary_pricing']['rt_tracking_policy']='remove'
    aux=run_auxiliary_pricing(m)['model']
    assert not aux.c_band_lo[0].active and not aux.c_band_hi[0].active
    assert aux.c_elim.active and m.c_band_lo[0].active
    assert aux.sB_up[0].fixed and aux.sB_dn[0].fixed
    assert aux._state_band_cost_coefficients[0]==0.
    assert m._state_band_cost_coefficients[0]==900.
    assert pyo.value(m.obj)==pytest.approx(before)
