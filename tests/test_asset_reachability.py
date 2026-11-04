"""An energy-only terminal band cannot protect discrete starts/mode continuation."""
from copy import deepcopy
import numpy as np
import pyomo.environ as pyo
import pytest
from smm import scuc,rolling
from smm.asset_reachability import diagnostics
from smm.auxiliary_pricing import run_auxiliary_pricing
from smm.stage_diagnostics import capture
from test_storage_reserve import pumped_case


def reachability_case(force_early_pump=False, certificate=True):
    c=pumped_case('idle',enabled=False);cfg=c.cfg
    cfg.base['periods']=16;c.T=1;c.stage='RT'
    cfg.units['pumped'][0].update(n_units=2,unit_gen_max=4.,unit_gen_min=2.,unit_pump=4.,unit_pump_min=4.,
        e_gen=1.,e_pump=1.,switch_gap=1,max_pump_starts=1,max_gen_starts=4,init_mode='idle')
    c.bids['units']['PS1'].update(gen_segments=[dict(p_start=0.,p_end=8.,price=50.)],
        pump_segments=[dict(p_start=0.,p_end=8.,price=0.)])
    c.fixed={};c.terminal={'storage_anchor_hard':True}
    if force_early_pump:
        c.fixed={n:{('PS1',0):v} for n,v in [('xg',0),('xp',1),('ngen',0),('npump',1)]}
        c.terminal['fix_power']={'Pg':{('PS1',0):0.},'Pp':{('PS1',0):4.}}
    if certificate:
        c.terminal['asset_reachability']=dict(start_period=1,end_period_exclusive=16,storage_targets={},
            pumped_targets={'PS1':[(6,112.,112.),(14,128.,128.)]})
    return c


def raw_status(m):
    opt=scuc._solver(m._case.cfg)
    r=opt.solve(m,load_solutions=False)
    return str(r.solver.termination_condition),bool(opt._solver_model.getSolution().value_valid)


def solve(m):
    scuc.solve_scuc(m);scuc.fix_integers(m);scuc.solve_sced(m)
    return m


def test_early_start_trap_feasible_without_suffix_but_strictly_infeasible_with_it():
    # First 2h needs only 2 unit-periods pumping; last 2h needs 2 units all 8 periods.
    # Using one start now requires stopping/restarting before the 2h target, leaving
    # too few aggregate starts for the last 2h. An energy envelope cannot detect it.
    old=scuc.build_model(reachability_case(force_early_pump=True,certificate=False))
    assert raw_status(old)==('optimal',True)
    protected=scuc.build_model(reachability_case(force_early_pump=True))
    assert raw_status(protected)==('infeasible',False)


def test_suffix_finds_integer_witness_and_combines_window_and_future_start_budget():
    m=solve(scuc.build_model(reachability_case()))
    b=m.asset_reachability
    assert pyo.value(m.npump['PS1',0])==0
    assert pyo.value(b.npump['PS1',7])==1
    assert all(pyo.value(b.npump['PS1',t])==2 for t in range(8,16))
    total=pyo.value(m.sp['PS1',0]+sum(b.sp['PS1',t] for t in b.F))
    assert total==pytest.approx(2.)
    d=diagnostics(m)
    assert d['max_target_error']<1e-6 and d['quantities_execute'] is False
    assert d['suffix_start']==1 and d['suffix_end_exclusive']==16
    assert len(d['future_anchor_targets'])==2
    assert len(m.T)==1  # no network/thermal clearing extension
    assert all(v.fixed for name,var in scuc.integer_components(m) for v in var.values())
    stage=capture(m,'energy_sced')
    assert {r['period'] for r in stage['units']}=={0}
    assert any(r['variable'].startswith('asset_reachability.') for r in stage['integer_values'])


def test_cross_boundary_mode_gap_is_not_replaced_by_energy_bound():
    c=reachability_case();cfg=c.cfg;cfg.base['periods']=8;c.t0=6
    c.fixed={n:{('PS1',0):v} for n,v in [('xg',1),('xp',0),('ngen',1),('npump',0)]}
    c.terminal.update(fix_power={'Pg':{('PS1',0):2.},'Pp':{('PS1',0):0.}},
        asset_reachability=dict(start_period=7,end_period_exclusive=8,storage_targets={},pumped_targets={'PS1':[(0,110.5,110.5)]}))
    st=rolling.initial_state(cfg)
    c.init=dict(thermal=st.thermal,hydro={},pumped={'PS1':dict(V=110.,ng=1,np=0,xg=1,xp=0)},
        pumped_used={'PS1':dict(gen=1.,pump=0.)},pumped_hist={'PS1':dict(xg=np.ones(6),xp=np.zeros(6))})
    assert raw_status(scuc.build_model(c))==('infeasible',False)
    cfg.units['pumped'][0]['switch_gap']=0
    assert raw_status(scuc.build_model(c))==('optimal',True)


def test_missing_future_clock_target_fails_closed():
    c=reachability_case();c.terminal['asset_reachability']['pumped_targets']['PS1'].pop(0)
    with pytest.raises(ValueError,match='完整覆盖'):scuc.build_model(c)


@pytest.mark.parametrize('policy',['remove','preserve'])
def test_auxiliary_clone_removes_or_preserves_certificate_without_mutating_dispatch(policy):
    c=reachability_case();c.cfg.mode['auxiliary_pricing']['rt_tracking_policy']=policy
    m=solve(scuc.build_model(c));before=pyo.value(m.obj)
    out=run_auxiliary_pricing(m)
    assert m.asset_reachability.active
    assert out['model'].asset_reachability.active==(policy=='preserve')
    assert out['removed_asset_reachability']==(policy=='remove')
    assert pyo.value(m.obj)==before


def test_battery_suffix_keeps_efficiency_inventory_and_bid_capacity():
    from test_storage_reserve import battery_case
    c=battery_case('idle',enabled=False);c.cfg.base['periods']=8;c.T=1;c.stage='RT'
    c.terminal.update(storage_anchor_hard=True,asset_reachability=dict(start_period=1,end_period_exclusive=8,
        storage_targets={'ES1':[(6,180.,180.)]},pumped_targets={}))
    m=solve(scuc.build_model(c));b=m.asset_reachability
    assert pyo.value(b.E['ES1',7])==pytest.approx(180.)
    for t in b.F:
        prev=pyo.value(m.E['ES1',0] if t==1 else b.E['ES1',t-1])
        assert pyo.value(b.E['ES1',t])==pytest.approx(prev+(.8*pyo.value(b.Pc['ES1',t])-pyo.value(b.Pd['ES1',t])/.9)*.25)
        assert pyo.value(b.Pc['ES1',t])*pyo.value(b.Pd['ES1',t])<1e-6
    c.bids['units']['ES1']['ch_segments']=[dict(p_start=0.,p_end=1.,price=0.)]
    assert raw_status(scuc.build_model(c))==('infeasible',False)
    c.cfg.base['solver']['storage_binary']=False
    with pytest.raises(ValueError,match='storage_binary=true'):scuc.build_model(c)


def test_scuc_failure_reports_native_termination_and_solution_availability():
    with pytest.raises(RuntimeError,match='kInfeasible.*primal_solution_available=False'):
        scuc.solve_scuc(scuc.build_model(reachability_case(force_early_pump=True)))


def test_nonzero_window_origin_uses_absolute_battery_self_schedule():
    from test_storage_reserve import battery_case
    c=battery_case('idle',enabled=False);c.cfg.base['periods']=8;c.T=1;c.t0=4;c.stage='RT'
    c.bids['units']['ES1'].update(mode='self_schedule',self_schedule=[-100.]*4+[0.,-10.,-20.,-30.])
    c.terminal.update(storage_anchor_hard=True,asset_reachability=dict(start_period=5,end_period_exclusive=8,
        storage_targets={'ES1':[(2,112.,112.)]},pumped_targets={}))
    m=solve(scuc.build_model(c));b=m.asset_reachability
    assert [pyo.value(b.Pc['ES1',t]) for t in b.F]==pytest.approx([10.,20.,30.])
    assert pyo.value(b.E['ES1',3])==pytest.approx(112.)


def test_variable_speed_suffix_respects_minimum_and_discrete_fixed_speed():
    c=reachability_case();cfg=c.cfg;cfg.base['periods']=8;c.t0=6
    c.fixed={n:{('PS1',0):0} for n in ('xg','xp','ngen','npump')}
    st=rolling.initial_state(cfg)
    c.init=dict(thermal=st.thermal,hydro={},pumped=st.pumped,pumped_used=st.pumped_used,
                pumped_hist={'PS1':dict(xg=np.zeros(6),xp=np.zeros(6))})
    c.terminal['asset_reachability']=dict(start_period=7,end_period_exclusive=8,
        storage_targets={},pumped_targets={'PS1':[(0,110.75,110.75)]})
    cfg.units['pumped'][0].update(variable_speed=True,unit_pump_min=2.)
    m=solve(scuc.build_model(c));assert pyo.value(m.asset_reachability.Pp['PS1',1])==pytest.approx(3.)
    cfg.units['pumped'][0]['variable_speed']=False
    assert raw_status(scuc.build_model(c))==('infeasible',False)
    cfg.units['pumped'][0]['variable_speed']=True
    c.terminal['asset_reachability']['pumped_targets']['PS1']=[(0,110.25,110.25)]
    assert raw_status(scuc.build_model(c))==('infeasible',False)
