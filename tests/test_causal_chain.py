"""Independent real-solver causal contracts for the full stage chain.
Synthetic 12-period market is deliberately small and adversarial, not IEEE39.
"""
from copy import deepcopy
from types import SimpleNamespace as NS
import json
import numpy as np
import pandas as pd
import pyomo.environ as pyo
import pytest
from smm import rolling, scuc, settlement, full
from smm.da import run_da
from test_da_stages import tiny_case


def thermal_case():
    c=tiny_case(reserve=0,local_price=700);cfg=c.cfg;T=12
    cfg.base['periods']=T;cfg.base['solver'].update(time_limit_s=10,mip_rel_gap=0)
    c.net.branches.loc[:,'rate_mw']=100.
    cfg.units['thermal'][0].update(type='coal',ut=.25,dt=.25)
    cfg.units['thermal'][1].update(type='gas_gt',pmin=20,ut=.25,dt=.25,
        init=dict(online=False,p0=0.,hours=48.))
    c.bids['units']['LOCAL']['startup_cost']={k:1. for k in ['hot','warm','cold']}
    c.bids['units']['LOCAL']['noload_cost']=10.
    cfg.mode['reserve'].update(include_ctg=False,rho_L=0.,rho_RE=0.,rho_L_dn=0.,rho_RE_dn=0.)
    cfg.mode['intraday'].update(enabled=True,interval_h=1,time_limit_s=10,mip_rel_gap=0,lead_issue_periods=1)
    cfg.mode['realtime'].update(lookahead_h=.25,time_limit_s=10,mip_rel_gap=0,hydro='value')
    cfg.mode['realtime']['storage']['mode']='value'
    # An intentionally biased ID/RT forecast isolates the commitment handoff:
    # ID forecasts 140MW and must start GT; actual demand is only 50MW.
    c.profiles=pd.DataFrame({f'load_{stage}':np.full(T,50. if stage in ['da','actual'] else 140.)
                            for stage in ['da','actual','id','rt']})
    c.load_bus={2:np.full(T,50.)};c.R_up=np.zeros(T);c.R_dn=np.zeros(T)
    return c


def independent_accounting(da,rr):
    s=settlement.settle(da,rr);detail=s['detail'];T=len(rr.rt);dt=da.case.cfg.dt
    sn=rolling.snapshot(da.model);bidx={b:i for i,b in enumerate(da.case.net.buses)}
    for row in detail.itertuples():
        bi=bidx[row.bus];t=row.period
        if row.entity in sn['P']:
            assert abs(row.q_da-sn['P'][row.entity][t])<1e-8
            assert abs(row.q_rt-rr.rt[t]['snap']['P'][row.entity][0])<1e-8
        assert row.lmp_da==da.prices['lmp_clamped'][bi,t]
        assert row.lmp_rt==rr.rt[t]['prices']['lmp_clamped'][bi,0]
        expected=(row.q_da*row.lmp_da+(row.q_rt-row.q_da)*row.lmp_rt)*dt
        assert abs(row.total-expected)<1e-7
    assert detail.groupby('period').q_da.sum().abs().max()<1e-8
    assert detail.groupby('period').q_rt.sum().abs().max()<1e-8
    assert s['reconciliation'].residual.abs().max()<1e-7
    # One and only one binding interval enters settlement per rolling window.
    assert len(detail.query("entity=='LOCAL'"))==T
    for t,r in enumerate(rr.rt):
        assert rr.states[t+1].t==t+1
        assert rr.states[t+1].thermal['LOCAL']['online']==bool(r['snap']['u']['LOCAL'][0])
        assert rr.states[t+1].thermal['LOCAL']['p0']==r['snap']['P']['LOCAL'][0]
    return s


@pytest.fixture(scope='module')
def causal_cases():
    cases={}
    for policy in ['inherit','source_da','free','id_off']:
        c=thermal_case()
        if policy=='source_da':c.cfg.mode['realtime']['handoff_source']='day_ahead'
        elif policy=='free':c.cfg.mode['realtime']['intraday_commit_rt']='free'
        elif policy=='id_off':c.cfg.mode['intraday']['enabled']=False
        da=run_da(c,verbose=False);rr=rolling.run_rolling(da,log=lambda *x:None)
        s=independent_accounting(da,rr);cases[policy]=(da,rr,s)
    return cases


def test_real_id_commitment_switch_changes_dispatch_and_deviation_payment(causal_cases):
    for policy,(da,rr,s) in causal_cases.items():
        assert da.stage_comparison['integer_mismatch']==0
        assert da.stage_comparison['objective_nonincrease']
        assert not any(v.fixed for v in da.model.P.values())
        np.testing.assert_array_equal(rolling.snapshot(da.model)['u']['CHEAP'],1)
        np.testing.assert_array_equal(rolling.snapshot(da.model)['u']['LOCAL'],0)
        for r in rr.id_rounds:
            np.testing.assert_array_equal(r['snap']['u']['CHEAP'],1)
            np.testing.assert_array_equal(r['snap']['u']['LOCAL'],1)
        actual=np.array([r['snap']['P']['LOCAL'][0] for r in rr.rt])
        np.testing.assert_allclose(actual,20. if policy=='inherit' else 0.,atol=1e-7)
        for r in rr.rt:
            assert r['reference_metadata']['u']['CHEAP']['source']=='day_ahead'
            assert r['snap']['u']['CHEAP'][0]==1
            if policy=='inherit':assert r['reference_metadata']['u']['LOCAL']['source'].startswith('intraday@')
            else:assert 'LOCAL' not in r['thermal_reference_u'] # historical explicit free-GT semantics
        gt=s['by_entity'].query("entity=='LOCAL'").iloc[0]
        assert gt.q_da==0.
        assert gt.q_rt==pytest.approx(60. if policy=='inherit' else 0.)
        assert gt.total==pytest.approx(600. if policy=='inherit' else 0.)


def test_id_physical_failure_real_rt_reverts_then_recovers_without_state_reset(monkeypatch):
    c=thermal_case();da=run_da(c,verbose=False);original=rolling.run_intraday_round
    entered=[]
    def injected(case,plan,cfg,state,tau,log):
        entered.append((tau,state.t,state.thermal['LOCAL']['online'],state.thermal['LOCAL']['p0']))
        if tau==4:raise scuc.InfeasibleModelError('controlled ID physical failure',phase='scuc',solver_status='infeasible')
        return original(case,plan,cfg,state,tau,log)
    monkeypatch.setattr(rolling,'run_intraday_round',injected)
    rr=rolling.run_rolling(da,log=lambda *x:None);s=independent_accounting(da,rr)
    assert [r['tau'] for r in rr.id_rounds]==[0,8]
    assert [r['tau'] for r in rr.id_failures]==[4]
    assert entered[1]==(4,4,True,20.) # actual GT state reaches failed round, not DA off
    np.testing.assert_allclose([r['snap']['P']['LOCAL'][0] for r in rr.rt],[20.]*4+[0.]*4+[20.]*4)
    for r in rr.rt[4:8]:
        assert r['reference_metadata']['u']['LOCAL']['source']=='day_ahead'
        assert 'controlled ID physical failure' in r['reference_metadata']['u']['LOCAL']['fallback_reason']
    assert s['by_entity'].query("entity=='LOCAL'").iloc[0].total==pytest.approx(400.)


def test_id_pricing_failure_never_becomes_dispatch_fallback_or_settlement(monkeypatch):
    c=thermal_case();original=rolling.run_intraday_round;settled=[]
    def broken(case,plan,cfg,state,tau,log):
        if tau==4:raise scuc.InfeasibleModelError('controlled pricing failure',phase='auxiliary_pricing',solver_status='infeasible')
        return original(case,plan,cfg,state,tau,log)
    monkeypatch.setattr(rolling,'run_intraday_round',broken)
    original_settle=settlement.settle
    monkeypatch.setattr(settlement,'settle',lambda *args:(settled.append(True),original_settle(*args))[1])
    with pytest.raises(scuc.InfeasibleModelError,match='controlled pricing failure'):
        full.run_full(c.cfg,c,log=lambda *x:None)
    assert not settled


def stateful_case():
    from smm.config import load_config
    c=thermal_case();cfg=c.cfg;source=load_config();T=cfg.T
    h=deepcopy(source.units['hydro'][0]);h.update(id='H1',bus=2,n_units=1,unit_pmax=20.,zones=[[0.,20.]],
        ramp=10.,water_rate=1.,init_units_on=1,init_p_unit=10.,qout_max=50.,spill_penalty=50.,e_day_min=None)
    h['seasons']={'wet':dict(qin=10.,v0=100.,vmin=90.,vmax=110.,v_end=100.,qeco=0.,e_day=60.)}
    h.pop('commitment', None)  # Historical aggregate fixture, not the new identified default.
    cfg.units['hydro']=[h];c.bids['units']['H1']=dict(mode='price_quantity',segments=[dict(p_start=0.,p_end=20.,price=5.)])
    s=deepcopy(source.units['storage'][0]);s.update(bus=2,pmax=10.,ecap=20.,soc0=.5,eta_c=.8,eta_d=.9)
    cfg.units['storage']=[s]
    c.bids['units']['ES1']=dict(mode='price_quantity',dis_segments=[dict(p_start=0.,p_end=10.,price=380.)],
        ch_segments=[dict(p_start=0.,p_end=10.,price=280.)],soc_min=.1,soc_max=.9,soc_end=.5)
    p=deepcopy(source.units['pumped'][0]);p.update(bus=2,n_units=1,unit_gen_max=10.,unit_gen_min=2.,unit_pump=10.,
        unit_pump_min=10.,e_gen=10.,e_pump=12.,v_total=200.,vup_min=99.,vup_max=101.,vdn_min=99.,vdn_max=101.,
        vup0=100.,vup_end_bid=None,init_mode='idle',max_gen_starts=24,max_pump_starts=24,switch_gap=1,start_cost_gen=0.,start_cost_pump=0.)
    cfg.units['pumped']=[p]
    c.bids['units']['PS1']=dict(mode='price_quantity',gen_segments=[dict(p_start=0.,p_end=10.,price=380.)],
        pump_segments=[dict(p_start=0.,p_end=10.,price=280.)],vup_end=100.)
    cfg.mode['realtime']['lookahead_h']=.5
    for stage in ['id','rt']:
        c.profiles[f'load_{stage}']=np.r_[np.full(4,50.),np.full(T-4,140.)]
    return c


def assert_physical_carry(da,rr):
    cfg=da.case.cfg;dt=cfg.dt
    for t,r in enumerate(rr.rt):
        before,after=rr.states[t:t+2];sn=r['snap'];assert after.t==t+1
        for s in cfg.units['storage']:
            u=s['id'];expected=before.storage[u]+(s['eta_c']*sn['Pc'][u][0]-sn['Pd'][u][0]/s['eta_d'])*dt
            assert after.storage[u]==pytest.approx(expected,abs=1e-7)
            assert after.storage[u]==sn['E'][u][0]
        for p in cfg.units['pumped']:
            u=p['id'];expected=before.pumped[u]['V']+(sn['Pp'][u][0]/p['e_pump']-sn['Pg'][u][0]/p['e_gen'])*dt
            assert after.pumped[u]['V']==pytest.approx(expected,abs=1e-7)
            assert after.pumped[u]['V']==sn['Vu'][u][0]
            for kind,key,snapkey in [('gen','ng','ngen'),('pump','np','npump')]:
                assert after.pumped_used[u][kind]==before.pumped_used[u][kind]+max(0,sn[snapkey][u][0]-before.pumped[u][key])
        for h in cfg.units['hydro']:
            u=h['id'];expected=before.hydro[u]+(h['seasons'][cfg.base['season']]['qin']-sn['Qout'][u][0])*dt*.36
            assert after.hydro[u]==pytest.approx(expected,abs=1e-7)
            assert after.hydro[u]==sn['Vh'][u][0]
            assert after.hydro_used[u]==pytest.approx(before.hydro_used[u]+sn['Ph'][u][0]*dt,abs=1e-7)
            assert after.hydro_hist[u][t]==sn['Qout'][u][0]
            assert np.isnan(after.hydro_hist[u][t+1:]).all()
        assert not r['fallback']


def test_real_all_resource_value_handoff_and_first_period_physics():
    c=stateful_case();da=run_da(c,verbose=False);rr=rolling.run_rolling(da,log=lambda *x:None)
    independent_accounting(da,rr);assert_physical_carry(da,rr)
    rounds={r['tau']:r for r in rr.id_rounds};assert list(rounds)==[0,4,8]
    da_values=scuc.state_values(da.model)
    different=0
    for rt in rr.rt:
        source_round=max(t for t in rounds if t<=rt['t']);idi=rounds[source_round]
        id_values=scuc.state_values(idi['model'])
        for w in rt['snap']['value_window']:
            kind,u,k=w['kind'],w['unit'],w['period'];rel=k-source_round
            assert w['reference_source']==f'intraday@{source_round}'
            assert w['value_source']==f'intraday@{source_round}'
            assert w['reference_state']==pytest.approx(idi['snap'][rolling.handoff.STATE_KEYS[kind]][u][rel],abs=1e-7)
            expected=0. if k==c.cfg.T-1 else id_values[kind][u]['value_native'][rel]
            assert w['value_native']==pytest.approx(expected,abs=1e-7)
            if abs(expected-da_values[kind][u]['value_native'][k])>1e-4:different+=1
    assert different>0 # The source change is numerically real, not only a label.
