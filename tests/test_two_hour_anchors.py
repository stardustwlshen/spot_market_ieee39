"""Hard two-hour clock anchors, physical reachability and full rolling execution."""
from copy import deepcopy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyomo.environ as pyo
import pytest

from smm import rolling, scuc
from smm.config import load_config
from smm.da import run_da
from smm.storage_anchors import anchor_periods, validate_anchor_config, hard_anchor_checks
from smm.auxiliary_pricing import run_auxiliary_pricing
from test_storage_reserve import battery_case, pumped_case
from test_hydro_rolling import hydro_case


def config():
    cfg=load_config()
    assert cfg.mode['realtime']['storage']['anchor']=='two_hour_eq'
    return cfg


@pytest.mark.parametrize('start,end,want', [(0,8,[7]),(1,9,[7]),(4,12,[7]),(7,15,[7]),
    (8,16,[15]),(0,16,[7,15]),(88,96,[95]),(92,96,[95]),(95,96,[95]),(0,3,[])])
def test_clock_end_indices_and_midnight(start,end,want):
    assert anchor_periods(config(),start,end)==want


@pytest.mark.parametrize('dt', [.5,1.,2.])
def test_no_hardcoded_four_or_eight_steps(dt):
    cfg=config();cfg.base.update(dt_h=dt,periods=int(24/dt));cfg.mode['realtime']['interval_min']=60*dt
    assert anchor_periods(cfg,0,cfg.T)==[int(h/dt)-1 for h in range(2,25,2)]


@pytest.mark.parametrize('target,value,pattern', [('dt',.3,'整数倍'),('interval',30,'一致'),
    ('lookahead',1.,'至少2'),('lookahead',2.1,'整数倍'),('lookahead',float('nan'),'有限正数'),
    ('binding',2,'首时段'),('periods',97,'跨日')])
def test_invalid_timing_fails_before_solve(target,value,pattern):
    cfg=config()
    if target=='dt':cfg.base['dt_h']=value;cfg.mode['realtime']['interval_min']=value*60
    elif target=='periods':cfg.base['periods']=value
    else:cfg.mode['realtime'][{'interval':'interval_min','lookahead':'lookahead_h','binding':'binding_periods'}[target]]=value
    with pytest.raises(ValueError,match=pattern):validate_anchor_config(cfg)


def test_odd_window_end_not_added_and_missing_target_not_clipped():
    cfg=config();s=cfg.units['storage'][0];sid=s['id']
    series={d['id']:np.full(96,d['ecap']*.5) for d in cfg.units['storage']}
    assert [a[0] for a in rolling.storage_anchors(cfg,series,1,9)[sid]]==[6]
    series[sid][7]=s['ecap']*2
    with pytest.raises(ValueError,match='不裁剪'):rolling.storage_anchors(cfg,series,0,8)
    with pytest.raises(ValueError,match='缺少日前状态'):rolling.storage_anchors(cfg,{},0,8)
    with pytest.raises(ValueError,match='缺少日前状态'):rolling.pumped_anchors(cfg,{},0,8)


def _direct_case(kind, reachable=True):
    c=battery_case('idle',enabled=False) if kind=='E' else pumped_case('idle',enabled=False)
    c.stage='RT';unit='ES1' if kind=='E' else 'PS1';target=100. if kind=='E' else 110.
    if not reachable:target+=5.
    c.terminal.update(storage_anchor_hard=True,
        tracking_policy=dict(mode='soft_deliverable_energy',penalty_yuan_per_discharge_mwh=1.))
    c.terminal['storage_anchor' if kind=='E' else 'pumped_anchor']={unit:[(0,target,target)]}
    return c


@pytest.mark.parametrize('kind',['E','Vu'])
def test_actual_hard_equation_cannot_pay_to_miss_or_relax(kind):
    c=_direct_case(kind);m=scuc.build_model(c)
    assert m._hard_anchor_indices=={0} and m.c_anc_eq[0].equality
    assert m.sA_up[0].fixed and m.sA_dn[0].fixed
    assert m._anchor_cost_coefficients[0]==0
    scuc.solve_scuc(m);scuc.fix_integers(m);scuc.solve_sced(m)
    assert abs(pyo.value(m.c_anc_eq[0].body)-pyo.value(m.c_anc_eq[0].lower))<1e-6
    assert scuc.relax_anchors(m)==0
    impossible=_direct_case(kind,reachable=False)
    with pytest.raises(RuntimeError):scuc.solve_scuc(scuc.build_model(impossible))


@pytest.mark.parametrize('kind',['E','Vu'])
@pytest.mark.parametrize('policy',['preserve','remove'])
def test_auxiliary_tracking_policy_never_mutates_formal_hard_constraint(kind,policy):
    c=_direct_case(kind);c.cfg.mode['auxiliary_pricing']['rt_tracking_policy']=policy
    m=scuc.build_model(c);scuc.solve_scuc(m);scuc.fix_integers(m);scuc.solve_sced(m)
    out=run_auxiliary_pricing(m);a=out['model']
    assert m.c_anc_eq[0].active and m.sA_up[0].fixed
    assert a.c_anc_eq[0].active==(policy=='preserve')
    if policy=='remove':assert out['removed_rt_tracking'][0]['removed_constraint']=='hard_rt_da_tracking'


def combined_case(T=16):
    c=hydro_case(T=T);cfg=c.cfg
    b=battery_case('idle',enabled=False);p=pumped_case('idle',enabled=False)
    cfg.units['storage']=deepcopy(b.cfg.units['storage']);cfg.units['pumped']=deepcopy(p.cfg.units['pumped'])
    cfg.units['pumped'][0].update(switch_gap=1, max_gen_starts=20,max_pump_starts=20,init_mode='idle')
    # Keep the fixture's hydraulic policy, but remove DA hourly pumped mode simplification.
    cfg.mode['pumped']['mode_step_periods']=1
    cfg.mode['realtime']['storage']['anchor']='two_hour_eq'
    cfg.mode['realtime']['tracking_policy']=dict(mode='soft_deliverable_energy',penalty_yuan_per_discharge_mwh=1000.)
    cfg.mode['reserve'].update(storage_reserve=False,include_ctg=False,rho_L=0.,rho_RE=0.,rho_L_dn=0.,rho_RE_dn=0.)
    cfg.base['solver'].update(time_limit_s=10.)
    cfg.mode['realtime'].update(time_limit_s=10.,mip_rel_gap=0.)
    c.bids['units']['ES1']=deepcopy(b.bids['units']['ES1']);c.bids['units']['ES1']['soc_end']=.5
    c.bids['units']['PS1']=deepcopy(p.bids['units']['PS1']);c.bids['units']['PS1']['vup_end']=110.
    # DA quiet, RT expensive first hour then cheap recovery; four-hour repetition.
    da=np.full(T,120.);real=np.resize(np.r_[np.full(4,230.),np.full(4,20.)],T)
    c.load_bus={2:da};c.R_up=np.zeros(T);c.R_dn=np.zeros(T)
    c.profiles=pd.DataFrame({'load_da':da,'load_actual':real,'load_rt':real,'load_id':real})
    return c


@pytest.fixture(scope='module')
def combined_run():
    c=combined_case();da=run_da(c,verbose=False);rr=rolling.run_rolling(da,periods=16,log=lambda *a:None)
    return da,rr


def test_rolling_two_and_four_hour_execution_and_hydro_unchanged(combined_run):
    da,rr=combined_run;cfg=da.case.cfg;plan=rolling.snapshot(da.model)
    for r in rr.rt:
        for a in r['snap']['anchors']:
            if a['kind'] in ('E','Vu'):
                assert a['hard'] and a['state_end_hour']%2==0
                assert a['value']==pytest.approx(a['lower'],abs=1e-6)
                assert a['slack_up']==a['slack_dn']==0
        h=r['snap']['hydro_constraints']['H1']
        assert h['terminal_kind']=='soft_da_window_end'
        assert h['v_end_target_1e4m3']==pytest.approx(plan['Vh']['H1'][min(r['t']+8,16)-1])
    for t in (7,15):
        for key in ('E','Vu'):
            for unit, values in rr.rt[t]['snap'][key].items():
                assert values[0]==pytest.approx(plan[key][unit][t],abs=1e-6)
    audit=hard_anchor_checks(cfg,plan,rr.rt)
    assert audit['storage_pumped_hard_anchors_satisfied']
    assert audit['hard_anchor_executed_count']==4
    # End-of-hour states are free to differ, demonstrating actual flexibility, not only fewer rows.
    differences=[abs(r['snap'][key][u][0]-plan[key][u][r['t']]) for r in rr.rt if r['t'] in (3,11)
                 for key in ('E','Vu') for u in plan[key]]
    assert max(differences)>1.


def test_hard_audit_fails_closed_and_startpoint_never_reset(combined_run):
    da,rr=combined_run;cfg=da.case.cfg;plan=rolling.snapshot(da.model)
    rounds=deepcopy(rr.rt);rounds[0]['snap']['anchors']=[]
    assert not hard_anchor_checks(cfg,plan,rounds)['storage_pumped_hard_anchors_satisfied']
    state=rolling.state_from_plan(cfg,plan,8);state.storage['ES1']+=1.
    with pytest.raises(ValueError,match='不得重置'):rolling.run_rt_period(da.case,plan,plan,cfg,state,8)
    state=rolling.state_from_plan(cfg,plan,7);state.storage['ES1']=20.
    plan=deepcopy(plan);plan['E']['ES1'][7]=180.
    with pytest.raises(RuntimeError,match='储能/抽蓄锚点=.*硬锚点不自动放松'):
        rolling.run_rt_period(da.case,plan,plan,cfg,state,7)


def test_legacy_migration_explicit_and_hydro_untouched():
    sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
    from anchor_policy_controls import two_hour_modes
    cfg=config();cfg.mode['realtime']['storage']['anchor']='hourly_eq'
    modes={cfg.rule_set:deepcopy(cfg.mode)};before=deepcopy(modes)
    out=two_hour_modes(cfg,modes)
    assert modes==before
    out[cfg.rule_set]['realtime']['storage']['anchor']='hourly_eq'
    assert out==before


def test_same_hardness_hourly_vs_two_hour_objective_and_flexibility(combined_run):
    da,rr=combined_run;plan=rolling.snapshot(da.model)
    # Isolate anchor cadence: same physical data/initial state, hard in both cases.
    cfg=da.case.cfg
    new=rolling.run_rt_period(da.case,plan,plan,cfg,rolling.initial_state(cfg),0)
    hourly=scuc.build_model(new['model']._case)
    hourly.extra_hourly=pyo.ConstraintList()
    for key in ('E','Vu'):
        for unit in plan[key]:
            hourly.extra_hourly.add(getattr(hourly,key)[unit,3]==plan[key][unit][3])
    scuc.solve_scuc(hourly);scuc.fix_integers(hourly);scuc.solve_sced(hourly)
    assert pyo.value(hourly.obj)>new['sced']['objective']+1.
    assert max(scuc.slack_summary(hourly).values())<1e-6
    assert max(new['slack'].values())<1e-6
    for key in ('E','Vu'):
        for unit in plan[key]:
            assert pyo.value(getattr(hourly,key)[unit,3])==pytest.approx(plan[key][unit][3],abs=1e-6)
    assert max(abs(new['snap'][key][unit][3]-plan[key][unit][3]) for key in ('E','Vu') for unit in plan[key])>1.


def test_2400_last_short_window_uses_index95_not_zero():
    c=combined_case(T=96)
    # DA is inexpensive and constant; only the final RT window needs solving.
    da=run_da(c,verbose=False);plan=rolling.snapshot(da.model);state=rolling.state_from_plan(c.cfg,plan,95)
    r=rolling.run_rt_period(c,plan,plan,c.cfg,state,95)
    assert len(r['snap']['E']['ES1'])==1
    for a in r['snap']['anchors']:
        if a['kind'] in ('E','Vu'):
            assert a['period']==0 and a['state_end_hour']==24.
            assert a['value']==pytest.approx(plan[a['kind']][a['unit']][95],abs=1e-6)
    assert r['snap']['hydro_constraints']['H1']['terminal_kind']=='soft_da_window_end'
