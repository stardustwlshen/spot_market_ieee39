"""Value-mode exact end-of-day feasibility, including a discrete PS dead-end."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pyomo.environ as pyo
import pytest

from smm import rolling, scuc
from smm.asset_reachability import diagnostics
from smm.auxiliary_pricing import run_auxiliary_pricing
from test_asset_reachability import raw_status, solve
from test_storage_reserve import pumped_case, battery_case


def trapped_case(*, certificate=True, volume=200.11390371625168, used_pump=4):
    c = pumped_case('idle', enabled=False)
    c.cfg.base['periods'] = 96
    c.T, c.t0, c.stage = 1, 80, 'RT'
    c.cfg.units['pumped'][0].update(n_units=2, unit_gen_min=150., unit_gen_max=300., unit_pump=300.,
        e_gen=9.6, e_pump=12.6, v_total=520., vup_min=60., vup_max=450., vdn_min=60., vdn_max=470.,
        max_gen_starts=4, max_pump_starts=2, switch_gap=1)
    c.bids['units']['PS1'].update(gen_segments=[dict(p_start=0.,p_end=600.,price=50.)],
        pump_segments=[dict(p_start=0.,p_end=600.,price=0.)])
    c.init = dict(thermal={u['id']:deepcopy(u['init']) for u in c.cfg.units['thermal']}, hydro={},
        pumped={'PS1':dict(V=volume,ng=0,np=0,xg=0,xp=0)},
        pumped_used={'PS1':dict(gen=2,pump=used_pump)},
        pumped_hist={'PS1':dict(xg=np.zeros(96),xp=np.zeros(96))})
    c.terminal.update(state_band=[('Vu','PS1',0,60.,450.,'band')])
    if certificate:
        c.terminal['asset_reachability'] = dict(target_policy='day_end',start_period=81,end_period_exclusive=96,
            storage_targets={},pumped_targets={'PS1':[(14,200.,200.)]})
    return c


def test_exact_ps_dead_end_not_hidden_inside_continuous_inventory_interval():
    assert 60 < 200 < 200.11390371625168
    assert raw_status(scuc.build_model(trapped_case(certificate=False))) == ('optimal',True)
    assert raw_status(scuc.build_model(trapped_case())) == ('infeasible',False)
    # One hypothetical extra remaining start restores feasibility, identifying
    # the mechanism, without changing the actual model's configured allowance.
    assert raw_status(scuc.build_model(trapped_case(used_pump=3))) == ('optimal',True)


def test_value_certificate_has_only_day_end_and_zero_future_dispatch():
    m = solve(scuc.build_model(trapped_case(volume=204.)))
    d = diagnostics(m)
    assert d['target_policy'] == 'day_end'
    assert d['quantities_execute'] is False and d['zero_objective_weight'] is True
    assert d['max_target_error'] < 1e-6
    assert [r['period'] for r in d['future_targets']] == [95]
    assert len(m.T) == 1 and len(m.asset_reachability.F) == 15
    assert pyo.value(m.Pg['PS1',0]) == 0  # the witness is not current dispatch
    assert all(v.fixed for _,var in scuc.integer_components(m) for v in var.values())
    b = d['pumped_start_budget']['PS1']
    assert b['pump']['remaining'] == 0 and b['pump']['window']+b['pump']['suffix'] == 0
    assert b['gen']['window']+b['gen']['suffix'] <= 6 + 1e-6
    # Pyomo's standard expression visitor checks the actual objective, not a label.
    from pyomo.core.expr.visitor import identify_variables
    assert not any(v.name.startswith('asset_reachability.') for v in identify_variables(m.obj.expr))


@pytest.mark.parametrize('policy',['preserve','remove'])
def test_value_auxiliary_policy_changes_only_clone(policy):
    c = trapped_case(volume=204.)
    c.cfg.mode['auxiliary_pricing']['rt_tracking_policy'] = policy
    m=solve(scuc.build_model(c)); before=pyo.value(m.obj)
    aux=run_auxiliary_pricing(m)['model']
    assert m.asset_reachability.active
    assert aux.asset_reachability.active == (policy == 'preserve')
    assert pyo.value(m.obj) == before


def test_day_end_policy_rejects_extra_clock_anchors():
    c=trapped_case(volume=204.)
    c.terminal['asset_reachability']['pumped_targets']['PS1'].insert(0,(6,204.,204.))
    with pytest.raises(ValueError,match='仅完整覆盖日末'):scuc.build_model(c)


def test_day_end_policy_rejects_missing_target():
    c=trapped_case(volume=204.)
    c.terminal['asset_reachability']['pumped_targets']['PS1']=[]
    with pytest.raises(ValueError,match='仅完整覆盖日末'):scuc.build_model(c)


def test_battery_day_end_witness_keeps_bid_capacity_and_state_dynamics():
    c=battery_case('idle',enabled=False)
    c.cfg.base['periods']=16;c.T=1;c.stage='RT'
    c.terminal['asset_reachability']=dict(target_policy='day_end',start_period=1,end_period_exclusive=16,
        storage_targets={'ES1':[(14,180.,180.)]},pumped_targets={})
    m=solve(scuc.build_model(c));b=m.asset_reachability
    assert len(b.anchors)==1 and pyo.value(b.E['ES1',15])==pytest.approx(180.)
    for t in b.F:
        prev=pyo.value(m.E['ES1',0] if t==1 else b.E['ES1',t-1])
        assert pyo.value(b.E['ES1',t])==pytest.approx(prev+(.8*pyo.value(b.Pc['ES1',t])-pyo.value(b.Pd['ES1',t])/.9)*.25)
    c.bids['units']['ES1']['ch_segments']=[dict(p_start=0.,p_end=1.,price=0.)]
    assert raw_status(scuc.build_model(c))==('infeasible',False)


@pytest.mark.parametrize("policy",["hard","soft"])
def test_value_spec_never_adds_clock_targets_or_wraps_day_end(policy):
    cfg=trapped_case().cfg
    cfg.mode["realtime"].setdefault("value_method", {})["terminal_policy"]={"mode":policy,"penalty_yuan_per_discharge_mwh":1000}
    plan={'E':{},'Vu':{'PS1':np.full(cfg.T,200.)}}
    spec=rolling.value_asset_reachability(cfg,plan,8)
    assert spec['target_policy']==('day_end_soft' if policy=='soft' else 'day_end')
    assert spec['pumped_targets']=={'PS1':[(87,200.,200.)]}
    assert rolling.value_asset_reachability(cfg,plan,96) is None


def test_unknown_target_policy_rejected():
    c=trapped_case();c.terminal['asset_reachability']['target_policy']='relax_integer'
    with pytest.raises(ValueError,match='目标策略'):scuc.build_model(c)
