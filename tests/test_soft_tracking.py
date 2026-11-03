"""Explicit new RT soft tracking and tracking-free auxiliary LP, tiny only."""
import pyomo.environ as pyo
import pytest

from smm import scuc, rolling
from smm.da import run_da
from smm.auxiliary_pricing import run_auxiliary_pricing
from test_hydro_rolling import hydro_case
from test_storage_reserve import battery_case, pumped_case

POLICY = dict(mode='soft_deliverable_energy', penalty_yuan_per_discharge_mwh=1000.)


def solve(case):
    m = scuc.build_model(case)
    scuc.solve_scuc(m)
    scuc.fix_integers(m)
    scuc.solve_sced(m)
    return m


def test_unreachable_hydro_da_target_is_soft_not_clipped_and_aux_removes_it():
    case = hydro_case(T=1)
    case.stage = 'RT'
    case.terminal = dict(tracking_policy=dict(POLICY), hydro_tracking={'H1': 250.})
    case.cfg.mode['auxiliary_pricing']['rt_tracking_policy'] = 'remove'
    m = solve(case)
    assert not hasattr(m, 'c_hydro_window_end')
    assert m.A_lo[0].value == 250.  # exceeds physical vmax200; must not clip
    assert m._anchor_cost_coefficients[0] == pytest.approx(1000/.36)
    assert pyo.value(m.sA_dn[0]) > 0
    assert abs(pyo.value(m.c_water['H1',0].body)) < 1e-6
    original = pyo.value(m.obj)
    out = run_auxiliary_pricing(m)
    a = out['model']
    assert not a.c_anc_hi[0].active and not a.c_anc_lo[0].active
    assert a.sA_dn[0].fixed and pyo.value(a.sA_dn[0]) == 0
    assert a._anchor_cost_coefficients[0] == 0
    assert a.c_water['H1',0].active and a.c_vlim['H1',0].active
    assert out['removed_rt_tracking'][0]['auxiliary_target_deviation_native'] != 0
    assert out['removed_rt_tracking'][0]['slack_zero_means_target_met'] is False
    assert pyo.value(m.obj) == original and m.c_anc_lo[0].active
    diag = rolling.hydro_diagnostics(m)['H1']
    assert diag['terminal_kind'] == 'soft_da_window_end'
    assert diag['tracking_slack_down_1e4m3'] > 0


@pytest.mark.parametrize('factory,mode,kind', [(battery_case,'idle','E'), (pumped_case,'gen','Vu')])
def test_rt_energy_tracking_coefficients_and_aux_removal(factory,mode,kind):
    case = factory(mode=mode, enabled=False, power=40.)
    case.stage = 'RT'
    case.terminal['tracking_policy'] = dict(POLICY)
    if kind == 'E':
        unit='ES1'; target=100.
        case.terminal['storage_anchor']={unit:[(0,target,target)]}
    else:
        unit='PS1'; target=110.
        case.terminal['pumped_anchor']={unit:[(0,target,target)]}
    case.cfg.mode['auxiliary_pricing']['rt_tracking_policy']='remove'
    m=solve(case)
    conversion=m._md.storage[unit]['eta_d'] if kind=='E' else m._md.pumped[unit]['e_gen']
    assert m._anchor_cost_coefficients[0] == 1000*conversion
    out=run_auxiliary_pricing(m)
    assert out['anchor_costs']==[]
    assert out['removed_rt_tracking'][0]['kind']==kind
    assert not out['model'].c_anc_hi[0].active
    assert m.c_anc_hi[0].active


def test_real_rolling_builds_soft_target_at_same_da_endpoint():
    da=run_da(hydro_case(T=8), verbose=False)
    cfg=da.case.cfg
    cfg.mode['realtime']['tracking_policy']=dict(POLICY)
    plan=rolling.snapshot(da.model)
    result=rolling.run_rt_period(da.case,plan,plan,cfg,rolling.initial_state(cfg),0)
    m=result['model']
    assert m._case.terminal['hydro_tracking']['H1']==plan['Vh']['H1'][7]
    assert result['snap']['hydro_constraints']['H1']['terminal_kind']=='soft_da_window_end'
    assert all(not v.fixed for v in m.Ph.values())
    assert all(v.fixed for v in m.delta.values())


def test_aux_rt_removes_legacy_hard_tracking_but_da_terminal_remains():
    case=hydro_case(T=1)
    case.stage='RT'
    case.terminal={'hydro_window_end': {'H1':100.}}
    case.cfg.mode['auxiliary_pricing']['rt_tracking_policy']='remove'
    m=solve(case)
    a=run_auxiliary_pricing(m)['model']
    assert not a.c_hydro_window_end['H1'].active
    assert m.c_hydro_window_end['H1'].active
    da_case=hydro_case(T=1)
    da_case.cfg.mode['auxiliary_pricing']['rt_tracking_policy']='remove'
    da=run_da(da_case,verbose=False).model
    a=run_auxiliary_pricing(da)['model']
    assert a.c_vend['H1'].active and da.c_vend['H1'].active


@pytest.mark.parametrize('kind', ['E','Vu','V'])
def test_soft_preserve_aux_override_uses_actual_formal_coefficient(kind):
    from pyomo.repn import generate_standard_repn
    if kind == 'V':
        case=hydro_case(T=1)
        case.terminal=dict(hydro_tracking={'H1':250.})
    elif kind == 'E':
        case=battery_case(mode='idle',enabled=False)
        case.terminal['storage_anchor']={'ES1':[(0,100.,100.)]}
    else:
        case=pumped_case(mode='gen',enabled=False,power=40.)
        case.terminal['pumped_anchor']={'PS1':[(0,110.,110.)]}
    case.stage='RT'
    case.terminal['tracking_policy']=dict(POLICY)
    # Deliberately stale legacy Pa must not be used as the subtraction baseline.
    case.terminal['anchor_penalty']=1e8
    case.terminal['pumped_anchor_penalty']=2e8
    case.cfg.mode['auxiliary_pricing'].update(rt_tracking_policy='preserve',
                                             anchor_penalty_yuan_per_discharge_mwh=500.)
    m=solve(case)
    original=pyo.value(m.obj)
    old=m._anchor_cost_coefficients[0]
    result=run_auxiliary_pricing(m)
    a=result['model']
    assert a.c_anc_hi[0].active and a.c_anc_lo[0].active
    assert result['removed_rt_tracking']==[]
    row=result['anchor_costs'][0]
    assert row['old_yuan_per_native_unit']==old
    assert row['new_yuan_per_native_unit']==pytest.approx(old/2)
    repn=generate_standard_repn(a.obj.expr)
    coefficients={v.name:c for v,c in zip(repn.linear_vars,repn.linear_coefs)}
    assert coefficients['sA_up[0]']==pytest.approx(old/2)
    assert coefficients['sA_dn[0]']==pytest.approx(old/2)
    assert coefficients['sA_up[0]']>0
    assert m._anchor_cost_coefficients[0]==old
    assert pyo.value(m.obj)==original
