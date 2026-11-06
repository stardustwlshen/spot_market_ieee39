"""RT soft targets are explicit and do not silently change ID or old scenes."""
import copy
from types import SimpleNamespace as NS
import numpy as np
import pytest
from smm import rolling, scuc
from smm.config import rt_terminal_policy
from test_handoff_integrity import context


def test_old_scene_without_terminal_block_remains_hard(context):
    cfg,plan=context
    cfg.mode['realtime']['value_method'].pop('terminal_policy',None)
    assert rt_terminal_policy(cfg.mode)['mode']=='hard'
    assert rolling.value_asset_reachability(cfg,plan,8)['target_policy']=='day_end'


@pytest.mark.parametrize('spec',[{}, {'mode':'free'}, {'mode':'soft'}, {'mode':'soft','penalty_yuan_per_discharge_mwh':True},
    {'mode':'soft','penalty_yuan_per_discharge_mwh':float('inf')}, {'mode':'soft','penalty_yuan_per_discharge_mwh':-1},
    {'mode':'soft','penalty_yuan_per_discharge_mwh':'1000'}, {'mode':'soft','penalty_yuan_per_discharge_mwh':1000,'typo':1}])
def test_invalid_rt_target_configuration_rejected(context,spec):
    cfg,_=context;cfg.mode['realtime']['value_method']['terminal_policy']=spec
    with pytest.raises(ValueError):rt_terminal_policy(cfg.mode)


def test_rt_soft_day_end_never_changes_id_terms_or_continuation(context):
    cfg,plan=context
    rt=rolling.value_method_terms(cfg,plan,88,96,realtime=True)
    idi=rolling.value_method_terms(cfg,plan,88,96)
    assert rt['state_band_terminal_policy']['mode']=='soft'
    assert 'state_band_terminal_policy' not in idi
    assert rt['state_band']==idi['state_band'] # same target, different enforcement
    assert all(x[3]==0 for x in rt['state_value'])
    assert rolling.value_asset_reachability(cfg,plan,8)['target_policy']=='day_end_soft'
    assert rolling.value_asset_reachability(cfg,plan,96) is None


def test_soft_intermediate_band_does_not_encode_strict_day_end_reachability(context):
    cfg,plan=context
    s=cfg.units['storage'][0];uid=s['id'];s['pmax']=1
    plan['E'][uid][-1]=s['soc_max']*s['ecap']
    terms=rolling.value_method_terms(cfg,plan,87,95,realtime=True,hydro=False,pumped=False)
    row=next(x for x in terms['state_band'] if x[1]==uid)
    centre=plan['E'][uid][94];width=.02*s['ecap']
    assert row[3]==pytest.approx(max(s['soc_min']*s['ecap'],centre-width))
    assert row[4]==pytest.approx(min(s['soc_max']*s['ecap'],centre+width))


def test_failed_id_reference_is_da_not_old_id_bundle(context):
    cfg,plan=context;ref=rolling.failed_intraday_reference(plan,4,'infeasible')
    for kind,key in rolling.handoff.STATE_KEYS.items():
        for unit in plan[key]:
            np.testing.assert_array_equal(ref[key][unit],plan[key][unit])
            meta=ref['reference_metadata'][kind][unit]
            assert meta['trajectory_source']=='day_ahead' and meta['round'] is None
            assert '@4' in meta['fallback_reason']
    assert ref is not plan and ref['reference_source']=='day_ahead'


def _rolling_probe(monkeypatch, cfg, plan, fail):
    cfg.mode['intraday']['enabled']=True
    calls=[]
    monkeypatch.setattr(rolling,'_da_plan_arrays',lambda da:copy.deepcopy(plan))
    monkeypatch.setattr(rolling,'initial_state',lambda cfg:NS(t=0))
    monkeypatch.setattr(rolling,'advance',lambda state,snap,cfg:NS(t=state.t+1))
    def idi(case,da,cfg,state,t,log):
        if t==4 or fail != 'middle':
            raise fail if isinstance(fail,BaseException) else scuc.InfeasibleModelError('physical',phase='scuc',solver_status='infeasible')
        return dict(tau=t,snap={k:{u:a[t:].copy() for u,a in val.items()} for k,val in plan.items() if k!='state_values'})
    def ref(da,previous,r,total,**kw):
        out=rolling.failed_intraday_reference(da,r['tau'],'placeholder')
        out['reference_source']=f"intraday@{r['tau']}"
        return out
    def rt(case,da,p,cfg,state,t,*,ref,id_active):
        calls.append(dict(t=t,state_t=state.t,ref=ref,active=id_active))
        return dict(snap={},prices={'lmp_clamped':np.zeros((1,1))},dispatch_prices={},pricing_info={},
            repriced_meaning='',slack={k:0. for k in ('shed_mwh','surplus_mwh','line_slack_max','reserve_up_short_max','reserve_dn_short_max','anchor_slack_max_mwh')},
            repriced=False,hydro_window_targets=[],anchor_relaxed=0,anchor_slack_present=False,pricing_source='',time_s=0.,mip={'time_s':0.},sced={})
    monkeypatch.setattr(rolling,'run_intraday_round',idi)
    monkeypatch.setattr(rolling,'intraday_reference',ref)
    monkeypatch.setattr(rolling,'run_rt_period',rt)
    return lambda:rolling.run_rolling(NS(case=NS(cfg=cfg)),periods=9,log=lambda *args:None), calls


def test_id_physical_failure_replaces_old_reference_then_recovers(monkeypatch,context):
    cfg,plan=context;run,calls=_rolling_probe(monkeypatch,cfg,plan,'middle');rr=run()
    assert [r['tau'] for r in rr.id_rounds]==[0,8]
    assert [r['tau'] for r in rr.id_failures]==[4]
    assert calls[3]['ref']['reference_source']=='intraday@0'
    assert all(calls[t]['ref']['reference_source']=='day_ahead' for t in range(4,8))
    assert calls[8]['ref']['reference_source']=='intraday@8'
    assert all(r['active'] and r['state_t']==r['t'] for r in calls)


def test_first_failed_id_attempt_still_activates_explicit_da_commitment_fallback(monkeypatch,context):
    cfg,plan=context;run,calls=_rolling_probe(monkeypatch,cfg,plan,'all');rr=run()
    assert not rr.id_rounds and len(rr.id_failures)==3
    assert all(c['active'] for c in calls)
    assert calls[0]['ref']['reference_metadata']['u']['GT']['source']=='day_ahead'


@pytest.mark.parametrize('error',[RuntimeError('software'),scuc.SolverBudgetExceededError('budget'),
    scuc.InfeasibleModelError('pricing',phase='auxiliary_pricing',solver_status='infeasible')])
def test_id_nonphysical_errors_are_never_fallback(monkeypatch,context,error):
    cfg,plan=context;run,calls=_rolling_probe(monkeypatch,cfg,plan,error)
    with pytest.raises(type(error),match=str(error)):run()
    assert calls==[]


def test_planned_soft_day_end_does_not_retry_unchanged_physics(monkeypatch,context):
    from test_handoff_integrity import da_case_probe, install_window_probe
    cfg,plan=context;attempts=[]
    def fail(case,**kwargs):
        attempts.append(case)
        raise scuc.InfeasibleModelError('physical',phase='scuc')
    install_window_probe(monkeypatch,fail)
    state=rolling.initial_state(cfg);state.t=95
    with pytest.raises(RuntimeError,match='physical') as exc:
        rolling.run_rt_period(da_case_probe(cfg),plan,plan,cfg,state,95)
    assert len(attempts)==1
    assert isinstance(exc.value.__cause__,scuc.InfeasibleModelError)
    assert exc.value.__cause__.phase=='scuc'
    assert attempts[0].terminal['state_band_terminal_policy']['mode']=='soft'
    assert not attempts[0].terminal.get('state_band_soft',False)


def test_model_accepts_normalized_legacy_hard_policy_without_penalty():
    from test_storage_reserve import battery_case
    c=battery_case('idle',enabled=False);c.stage='RT'
    c.terminal['state_band_terminal_policy']={'mode':'hard','penalty_yuan_per_discharge_mwh':None}
    scuc.build_model(c)
