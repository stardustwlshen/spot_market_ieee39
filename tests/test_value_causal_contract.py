"""Causal, unit-aware value method regressions using actual fixed-integer LPs.
These micro-markets do not claim global MIP value functions or full-day IEEE39 QA.
"""
from copy import deepcopy
import numpy as np
import pandas as pd
import pyomo.environ as pyo
import pytest
from smm import rolling,scuc,full,settlement
from smm.da import run_da
from test_causal_chain import stateful_case,independent_accounting,assert_physical_carry
from test_terminal_value_semantics import stock_lp,ASSETS


@pytest.fixture(scope='module')
def actual_id():
    c=stateful_case();da=run_da(c,verbose=False);plan=rolling._da_plan_arrays(da);state=rolling.initial_state(c.cfg)
    idi=rolling.run_intraday_round(c,plan,c.cfg,state,0,log=lambda *a:None)
    ref=rolling.intraday_reference(plan,plan,idi,c.cfg.T,cfg=c.cfg)
    return c,da,plan,state,idi,ref


@pytest.mark.parametrize('family,kind,key',[('storage','E','E'),('pumped','Vu','Vu'),('hydro','V','Vh')])
def test_real_reference_switch_changes_execution_without_resetting_actual_initial_state(actual_id,family,kind,key):
    c,da,plan,state,idi,ref=actual_id;before=full.actual_state_evidence(state)
    enabled=rolling.run_rt_period(c,plan,ref,c.cfg,state,0,ref=ref,id_active=True)
    cfg=deepcopy(c.cfg);cfg.mode['realtime']['handoff'][family]=False
    disabled=rolling.run_rt_period(c,plan,ref,cfg,state,0,ref=ref,id_active=True)
    assert full.actual_state_evidence(state)==before
    uid=next(iter(ref[key]));on=next(x for x in enabled['snap']['value_window'] if x['kind']==kind)
    off=next(x for x in disabled['snap']['value_window'] if x['kind']==kind)
    assert on['reference_source']=='intraday@0' and off['reference_source']=='day_ahead'
    assert abs(on['value_native']-off['value_native'])>1e-6
    assert abs(enabled['snap'][key][uid][0]-disabled['snap'][key][uid][0])>1e-6
    for row in disabled['snap']['value_window']:
        if row['kind']!=kind:assert row['reference_source']=='intraday@0'
    initial_key={'E':'storage','Vu':'pumped','V':'hydro'}[kind]
    assert enabled['model']._case.init[initial_key]==disabled['model']._case.init[initial_key]


@pytest.mark.parametrize('kind,con,group',[('E','c_soc','S'),('Vu','c_pres','PS'),('V','c_water','HS')])
def test_actual_id_dual_is_two_sided_at_smooth_points_and_valid_at_kinks(actual_id,kind,con,group):
    c,da,plan,state,idi,ref=actual_id;m=idi['model'];values=scuc.state_values(m)
    uid=next(iter(getattr(m,group)));base=pyo.value(m.obj);smooth=kinks=0
    for t in range(m._md.T-1):
        slopes=[]
        for eps in [-1e-4,1e-4]:
            trial=m.clone();eq=getattr(trial,con)[uid,t+1];lhs,rhs=eq.expr.args
            eq.set_value(lhs==rhs+eps);scuc.solve_sced(trial)
            slopes.append(-(pyo.value(trial.obj)-base)/eps)
        v=values[kind][uid]['value_native'][t]
        assert min(slopes)-.02<=v<=max(slopes)+.02
        if abs(slopes[0]-slopes[1])<.02:
            np.testing.assert_allclose(slopes,v,atol=.02,rtol=0);smooth+=1
        else:kinks+=1
    assert smooth>0
    if kind=='E':assert kinks>0 # Deliberate LP corner; not a dual sign bug.
    assert values[kind][uid]['value_native'][-1]==0.
    factor=1 if kind=='E' else (1/m._md.pumped[uid]['e_gen'] if kind=='Vu' else .36*m._md.hydro[uid]['water_rate'])
    np.testing.assert_allclose(values[kind][uid]['value_yuan_per_mwh'],values[kind][uid]['value_native']*factor)


@pytest.mark.parametrize('kind',['E','Vu','V'])
def test_zero_day_end_continuation_value_does_not_replace_terminal_target(kind):
    protected=stock_lp(kind,T=8,target=2.,terminal='direct');removed=stock_lp(kind,T=8,target=2.,terminal='none')
    uid=ASSETS[kind][0]
    assert scuc.state_values(protected)[kind][uid]['value_native'][-1]==0
    assert pyo.value(getattr(protected,kind)[uid,7])==pytest.approx(2.)
    assert pyo.value(getattr(removed,kind)[uid,7])==pytest.approx(0.)
    assert pyo.value(removed.obj)<pyo.value(protected.obj)


def test_remove_only_value_term_changes_all_three_inventory_decisions():
    c=stateful_case();cfg=c.cfg
    # Deliberately wide sensitivity bands expose the coefficient's effect.
    # Production policies and default bids are NOT changed by this micro-test.
    cfg.mode['realtime']['value_method'].update(band_fraction=.5,hydro_band_fraction=.5,hydro_lookahead='off')
    for uid,fields in [('ES1',['dis_segments','ch_segments']),('PS1',['gen_segments','pump_segments'])]:
        for field in fields:
            for segment in c.bids['units'][uid][field]:segment['price']=1. if field in ['dis_segments','gen_segments'] else 0.
    da=run_da(c,verbose=False);plan=rolling._da_plan_arrays(da);state=rolling.initial_state(cfg)
    idi=rolling.run_intraday_round(c,plan,cfg,state,0,log=lambda *a:None)
    ref=rolling.intraday_reference(plan,plan,idi,cfg.T,cfg=cfg)
    base=rolling.run_rt_period(c,plan,ref,cfg,state,0,ref=ref,id_active=True)
    for kind,key in rolling.handoff.STATE_KEYS.items():
        uid=next(iter(ref[key]));alt=deepcopy(ref)
        alt['state_values'][kind][uid]['value_native'][:]=0.
        alt['state_values'][kind][uid]['value_yuan_per_mwh'][:]=0.
        zero=rolling.run_rt_period(c,plan,alt,cfg,state,0,ref=alt,id_active=True)
        w=next(w for w in base['snap']['value_window'] if w['kind']==kind)
        z=next(w for w in zero['snap']['value_window'] if w['kind']==kind)
        assert (w['lower'],w['upper'],w['reference_state'])==(z['lower'],z['upper'],z['reference_state'])
        assert w['value_native']>0 and z['value_native']==0
        assert w['state']>z['state']+1e-6
        assert base['snap'][key][uid][0]>zero['snap'][key][uid][0]+1e-6


def test_unexecuted_rt_tail_id_plan_and_scuc_preplan_cannot_enter_settlement(actual_id):
    c,da,plan,state,idi,ref=actual_id
    rr=rolling.run_rolling(da,log=lambda *a:None)
    original=independent_accounting(da,rr);assert_physical_carry(da,rr)
    # Mutate separate diagnostic/nonexecuted data after execution. The public
    # settlement output must remain bit-for-bit the same.
    for r in rr.rt:
        for key in ['P','Pw','Ph','Pd','Pc','Pg','Pp']:
            for arr in r['snap'][key].values():arr[1:]=1e8
    for r in rr.id_rounds:
        for key in ['P','Pw','Ph','Pd','Pc','Pg','Pp']:
            for arr in r['snap'][key].values():arr[:]=1e8
    previous=deepcopy(da.scuc_snapshot)
    try:
        for k in da.scuc_snapshot['P']:da.scuc_snapshot['P'][k]=1e8
        after=settlement.settle(da,rr)
    finally:da.scuc_snapshot=previous
    pd.testing.assert_frame_equal(original['detail'],after['detail'])
    pd.testing.assert_frame_equal(original['reconciliation'],after['reconciliation'])
