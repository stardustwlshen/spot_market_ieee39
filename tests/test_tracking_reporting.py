"""Soft tracking is an economic deviation, not a relaxed physical water balance."""
from types import SimpleNamespace as NS
from copy import deepcopy
import numpy as np
import pytest
from smm import full


def evidence(monkeypatch, soft=True):
    qin=1/.09  # Declared legacy natural inflow, independently checked in both stages.
    cfg=NS(T=1,dt=.25,base={'season':'wet','forecast':{'model':'legacy_v1'}},
        mode={'realtime':{'hydro':'window_end_da'}},
        units={'hydro':[dict(id='H',seasons={'wet':dict(vmin=0.,vmax=200.,qeco=0.,qin=qin)})]})
    if soft: cfg.mode['realtime']['tracking_policy']={'mode':'soft_deliverable_energy','penalty_yuan_per_discharge_mwh':1000.}
    def snap(volume, qin, target=None, *, outflow=0.):
        return dict(Ph={'H':np.array([0.])},Vh={'H':np.array([volume])},Qout={'H':np.array([outflow])},delta={('H',0):np.array([1])},
            hydro_constraints={'H':dict(p_start_mw=0.,v_start_1e4m3=100.,qin_m3s=[qin],upstream_in_m3s=[0.],
                ramp_up_limit_mw=[0.],ramp_down_limit_mw=[0.],v_end_target_1e4m3=target,
                terminal_kind='soft_da_window_end' if soft else 'hard_da_window_end',
                tracking_slack_up_1e4m3=1.,tracking_slack_down_1e4m3=0.)})
    # DA releases the declared inflow; RT retains it. The 1e4m3 difference is
    # an economic target deviation, not invented stage-dependent natural water.
    da=NS(case=NS(cfg=cfg),model=object()); dasn=snap(100.,qin,outflow=qin)
    monkeypatch.setattr(full.rolling,'snapshot',lambda m:dasn)
    rr=NS(rt=[{'t':0,'snap':snap(101.,qin,100.)}],states=[NS(hydro={'H':100.},hydro_p={'H':0.})])
    return da,rr


def test_soft_physical_valid_nonzero_target_deviation_passes(monkeypatch):
    da,rr=evidence(monkeypatch)
    out=full.hydro_window_checks(da,rr)
    assert out['hydro_window_constraints_satisfied']
    assert out['hydro_tracking_target_deviation_max_1e4m3']==1.
    assert out['hydro_tracking_equation_max_err_1e4m3']==0.
    assert out['hydro_window_terminal_max_err_1e4m3']==0.
    assert out['hydro_water_balance_max_err_1e4m3']==0.


@pytest.mark.parametrize('corruption',['slack','target','water','missing','nan'])
def test_soft_tracking_fail_closed(monkeypatch,corruption):
    da,rr=evidence(monkeypatch);sn=rr.rt[0]['snap'];meta=sn['hydro_constraints']['H']
    if corruption=='slack':meta['tracking_slack_up_1e4m3']=0.
    if corruption=='target':meta['v_end_target_1e4m3']=99.
    if corruption=='water':meta['qin_m3s']=[0.]
    if corruption=='missing':del meta['tracking_slack_up_1e4m3']
    if corruption=='nan':meta['tracking_slack_up_1e4m3']=float('nan')
    assert not full.hydro_window_checks(da,rr)['hydro_window_constraints_satisfied']


@pytest.mark.parametrize('corruption',['water_with_valid_source','coherent_but_false_source'])
def test_soft_tracking_cannot_hide_water_or_source_corruption(monkeypatch,corruption):
    da,rr=evidence(monkeypatch);sn=rr.rt[0]['snap'];meta=sn['hydro_constraints']['H']
    if corruption=='water_with_valid_source':
        sn['Qout']['H'][0]=1.  # Source remains valid; reported stock omits this release.
    else:
        meta['qin_m3s']=[0.];sn['Vh']['H'][0]=100.
        meta['tracking_slack_up_1e4m3']=0.  # Internally balanced, but false natural source.
    out=full.hydro_window_checks(da,rr)
    assert not out['hydro_window_constraints_satisfied']
    if corruption=='water_with_valid_source':
        assert out['hydro_window_checks_available']
        assert out['hydro_water_balance_max_err_1e4m3']==pytest.approx(.09)
    else:
        assert not out['hydro_window_checks_available']


def test_old_hard_target_remains_strict(monkeypatch):
    da,rr=evidence(monkeypatch,soft=False)
    out=full.hydro_window_checks(da,rr)
    assert not out['hydro_window_constraints_satisfied']
    assert out['hydro_window_terminal_max_err_1e4m3']==1.


def test_three_tracking_slack_units_separated():
    slack=dict(shed_mwh=0.,surplus_mwh=0.,line_slack_max=0.,reserve_up_short_max=0.,reserve_dn_short_max=0.,
               anchor_slack_max_mwh=3.,storage_anchor_slack_max_mwh=1.,pumped_anchor_slack_max_1e4m3=2.,hydro_anchor_slack_max_1e4m3=3.)
    out=full.slack_policy_checks(slack,[{'slack':slack}])
    assert out['storage_anchor_slack_max_mwh']==1.
    assert out['pumped_anchor_slack_max_1e4m3']==2.
    assert out['hydro_anchor_slack_max_1e4m3']==3.
    assert not out['rules_satisfied_no_slack']


def test_tracking_table_preserves_native_units_and_no_dt_in_stock_penalty():
    from smm.results import tracking_results
    m=NS(_case=NS(t0=4,terminal={}),_anchors=[('S',0,10.,10.,'E'),('P',0,20.,20.,'Vu'),('H',0,30.,30.,'V')],
         E={('S',0):11.},Vu={('P',0):18.},V={('H',0):33.},
         A_lo={0:10.,1:20.,2:30.},A_hi={0:10.,1:20.,2:30.},
         sA_up={0:1.,1:0.,2:3.},sA_dn={0:0.,1:2.,2:0.},
         c_anc_lo={i:NS(active=True) for i in range(3)},c_anc_hi={i:NS(active=True) for i in range(3)},
         _anchor_cost_coefficients={0:900.,1:500.,2:1000./.36})
    rows=tracking_results(m).set_index('unit')
    assert rows.loc['S','stock_unit']=='MWh'
    assert rows.loc['P','stock_unit']==rows.loc['H','stock_unit']=='1e4m3'
    assert rows.loc['S','penalty_yuan']==900.
    assert rows.loc['P','penalty_yuan']==1000.
    assert rows.loc['H','penalty_yuan']==pytest.approx(3000./.36)
    m.c_anc_lo[2].active=False;m.c_anc_hi[2].active=False;m._anchor_cost_coefficients[2]=0.
    rows=tracking_results(m).set_index('unit')
    assert rows.loc['H','penalty_yuan']==0.
    assert rows.loc['H','deviation_up']==3.  # removed aux target is not falsely reached


def test_real_soft_hydro_window_reporting_and_penalty():
    from test_hydro_rolling import hydro_case
    from smm.da import run_da
    from smm import rolling
    from smm.results import tracking_results,cost_breakdown
    case=hydro_case(T=1)
    case.cfg.mode['realtime']['tracking_policy']={'mode':'soft_deliverable_energy','penalty_yuan_per_discharge_mwh':1000.}
    da=run_da(case,verbose=False);plan=rolling.snapshot(da.model);state=rolling.initial_state(case.cfg)
    r=rolling.run_rt_period(case,plan,plan,case.cfg,state,0)
    meta=r['snap']['hydro_constraints']['H1']
    assert meta['terminal_kind']=='soft_da_window_end'
    assert not hasattr(r['model'],'c_hydro_window_end')
    row=tracking_results(r['model']).query("kind == 'V'").iloc[0]
    assert row.penalty_yuan_per_stock_unit==pytest.approx(1000./.36)
    assert cost_breakdown(r['model'])['hydro_tracking_penalty_yuan']==pytest.approx(row.penalty_yuan)
    out=full.hydro_window_checks(da,rolling.RollingResult(rt=[r],states=[state]))
    assert out['hydro_window_constraints_satisfied']
