"""Auxiliary water disposal must match formal dispatch, including cascade horizons."""
from copy import deepcopy
import pyomo.environ as pyo
import pytest

from smm import scuc
from smm.auxiliary_pricing import run_auxiliary_pricing, _freeze_hydro_spill
from test_hydro_rolling import hydro_case
from test_soft_tracking import solve, POLICY


def cascade_model():
    case=hydro_case(T=4)
    case.stage='RT'
    h1=case.cfg.units['hydro'][0]
    h1.update(ramp=100.,qout_max=300.)
    h1['seasons']['wet'].update(qin=120.,vmax=200.)
    h2=deepcopy(h1)
    h2.update(id='H2',upstream='H1',lag_periods=2,init_p_unit=50.)
    h2['seasons']['wet'].update(qin=5.)
    case.cfg.units['hydro'].append(h2)
    case.bids['units']['H2']=deepcopy(case.bids['units']['H1'])
    case.terminal=dict(tracking_policy=dict(POLICY),hydro_tracking={'H1':100.,'H2':100.})
    case.cfg.mode['auxiliary_pricing']['rt_tracking_policy']='remove'
    return solve(case)


def test_all_horizon_cascade_spill_fixed_and_actual_water_changes_remain_physical():
    m=cascade_model()
    before={v.name:(pyo.value(v),v.fixed) for v in m.component_data_objects(pyo.Var)}
    out=run_auxiliary_pricing(m);a=out['model']
    assert len(out['hydro_spill_fixation'])==8
    assert {(r['station'],r['relative_period']) for r in out['hydro_spill_fixation']}=={(h,t) for h in m.HS for t in m.T}
    assert any(r['dispatch_spill_m3s']>1e-6 for r in out['hydro_spill_fixation'])
    for row in out['hydro_spill_fixation']:
        assert row['fixed'] and row['difference_m3s']==0
    for h in m.HS:
        for t in m.T:
            assert not a.V[h,t].fixed and not a.Ph[h,t].fixed
            assert abs(pyo.value(a.c_water[h,t].body))<1e-6
            assert pyo.value(a.Qspill[h,t])==pyo.value(m.Qspill[h,t])
        # With fixed spill, reservoir deviation comes solely from generation
        # and physical delayed upstream release, never cost-free extra disposal.
        accumulated=0.
        for t in m.T:
            upstream=0.
            if h=='H2' and t>=2:
                upstream=pyo.value(a.Ph['H1',t-2]-m.Ph['H1',t-2])*m._md.hydro['H1']['water_rate']
            own=pyo.value(a.Ph[h,t]-m.Ph[h,t])*m._md.hydro[h]['water_rate']
            accumulated+=(upstream-own)*m._md.dt*.36
            assert pyo.value(a.V[h,t]-m.V[h,t])==pytest.approx(accumulated,abs=1e-6,rel=0)
    assert {v.name:(pyo.value(v),v.fixed) for v in m.component_data_objects(pyo.Var)}==before


def test_spill_fixation_rejects_incomplete_horizon_or_nonfinite_input():
    m=cascade_model();a=m.clone()
    del a.Qspill['H1',3]
    with pytest.raises(ValueError,match='complete matching'):_freeze_hydro_spill(m,a)
    m.Qspill['H1',0].set_value(float('nan'),skip_validation=True)
    with pytest.raises(ValueError,match='finite'):_freeze_hydro_spill(m,m.clone())


def test_da_zero_spill_is_fixed_too_without_fixing_hydro_state_or_power():
    from smm.da import run_da
    m=run_da(hydro_case(T=1),verbose=False).model
    assert pyo.value(m.Qspill['H1',0])==pytest.approx(0.,abs=1e-9,rel=0)
    out=run_auxiliary_pricing(m)
    row=out['hydro_spill_fixation'][0]
    assert row['dispatch_spill_m3s']==row['auxiliary_spill_m3s']==0.
    assert row['fixed'] and not m.Qspill['H1',0].fixed
    assert not out['model'].V['H1',0].fixed and not out['model'].Ph['H1',0].fixed
