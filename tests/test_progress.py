"""Live lifecycle regressions without optimization or external UI processes."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from smm import full, rolling
from smm.live_runner import RunCancelled


@pytest.fixture
def mocked_flow(monkeypatch):
    cfg = SimpleNamespace(T=3, dt=.25, base={'dt_h':.25}, units={'thermal': []},
        mode={'intraday':{'enabled':False,'interval_h':1},
              'realtime':{'interval_min':15,'lookahead_h':2.,'binding_periods':1}})
    da = SimpleNamespace(case=SimpleNamespace(cfg=cfg), model=object())
    # This fixture isolates lifecycle ordering, not DA model physics.
    # The optional lower-bound audit is inactive with no hydro resources.
    monkeypatch.setattr(rolling, 'snapshot', lambda _: {})
    calls = []
    def solve(case, da_plan, plan, cfg, state, t, **kw):
        calls.append(t)
        return dict(snap={'future':[t,t+1]}, prices={'lmp_clamped':np.array([[100.]])},
                    dispatch_prices={'lmp_clamped':np.array([[100.]])},pricing_info=None,repriced_meaning='disabled',
                    slack={}, repriced=False, anchor_relaxed=0, anchor_slack_present=False,
                    pricing_source='standard_sced', time_s=.01, mip={'time_s':.01}, sced={'time_s':.005,'termination':'optimal'}, hydro_window_targets=[])
    monkeypatch.setattr(rolling, '_da_plan_arrays', lambda _: {})
    monkeypatch.setattr(rolling, '_abs_plan', lambda *a: {})
    monkeypatch.setattr(rolling, 'initial_state', lambda _: 0)
    monkeypatch.setattr(rolling, 'advance', lambda state,*a:state+1)
    monkeypatch.setattr(rolling, 'run_rt_period', solve)
    run_da = Mock(return_value=da)
    settle = Mock(return_value={})
    monkeypatch.setattr(full, 'run_da', run_da)
    monkeypatch.setattr(rolling, 'rt_tables', lambda *a: {})
    monkeypatch.setattr(rolling, 'intraday_tables', lambda *a: {})
    monkeypatch.setattr(full.settlement, 'settle', settle)
    monkeypatch.setattr(full, 'checks', lambda *a: {})
    return cfg, da, calls, run_da, settle


def test_events_deliver_executed_prefix_before_next_solve_and_settlement(mocked_flow):
    cfg, da, calls, run_da, settle = mocked_flow
    phases, prefix = [], []
    def callback(event):
        phases.append(event['phase'])
        assert not settle.called
        if event['phase']=='rt_window':
            rr = event['rr']
            assert event['da'] is da
            assert len(rr.rt)==event['completed']==len(calls)
            assert [r['t'] for r in rr.rt] == list(range(event['completed']))
            assert event['round'] is rr.rt[-1]
            # Copy at callback time: rr is intentionally a live mutable prefix.
            prefix.append(tuple(r['t'] for r in rr.rt))
    result = full.run_full(cfg,da.case,progress_callback=callback)
    assert phases == ['da_start','da_done','rt_window','rt_window','rt_window']
    assert prefix == [(0,), (0,1), (0,1,2)]
    assert len(result['rr'].rt)==3
    assert all(r['sced']=={'time_s':.005,'termination':'optimal'} for r in result['rr'].rt)
    settle.assert_called_once_with(da,result['rr'])


def test_cancel_before_da_does_not_solve_or_settle(mocked_flow):
    cfg, da, calls, run_da, settle = mocked_flow
    with pytest.raises(RunCancelled,match='before DA'):
        full.run_full(cfg,da.case,should_cancel=lambda:True)
    run_da.assert_not_called(); settle.assert_not_called(); assert calls==[]


def test_cancel_between_windows_preserves_prefix_without_next_solve_or_settlement(mocked_flow):
    cfg, da, calls, run_da, settle = mocked_flow
    stop = False
    prefixes=[]
    def callback(event):
        nonlocal stop
        if event['phase']=='rt_window':
            prefixes.append(tuple(r['t'] for r in event['rr'].rt)); stop=True
    with pytest.raises(RunCancelled,match='after 1'):
        full.run_full(cfg,da.case,progress_callback=callback,should_cancel=lambda:stop)
    assert calls==[0] and prefixes==[(0,)]
    settle.assert_not_called()


@pytest.mark.parametrize('phase,expected_calls',[('da_start',[]),('da_done',[]),('rt_window',[0])])
def test_callback_failure_propagates_without_settlement(mocked_flow,phase,expected_calls):
    cfg, da, calls, run_da, settle = mocked_flow
    def callback(event):
        if event['phase']==phase: raise OSError('progress write failed')
    with pytest.raises(OSError,match='progress write failed'):
        full.run_full(cfg,da.case,progress_callback=callback)
    assert calls==expected_calls
    settle.assert_not_called()
    if phase=='da_start': run_da.assert_not_called()


def test_spot_summary_excludes_capacity_fee_even_if_legacy_config_contains_it():
    cfg=SimpleNamespace(dt=.25, units={'pumped':[{'id':'PS1','bus':2,'capacity_price':9999}]},
                        mode={'realtime':{'storage':{'mode':'sced_anchor','anchor':'hourly_eq'}}})
    ps=pd.DataFrame([dict(unit='PS1',period=0,P_gen=100.,P_pump=0.,V_up=200.)])
    da=SimpleNamespace(case=SimpleNamespace(cfg=cfg,bids={'units':{}}), tables={'pumped':ps})
    entities=pd.DataFrame([dict(entity='PS1_发电',da_amount=100.,rt_amount=10.,total=110.),
                           dict(entity='PS1_抽水',da_amount=-50.,rt_amount=-5.,total=-55.)])
    result=full.pumped_full_summary({'da':da,'rt':{'pumped':ps},'rr':SimpleNamespace(rt=[{}]),
                                    'settle':{'by_entity':entities}})['PS1']
    assert result['energy_net_revenue']==55.
    assert not any('capacity' in key for key in result)


def test_cancel_after_final_window_still_blocks_settlement(mocked_flow):
    cfg, da, calls, run_da, settle = mocked_flow
    stop = False
    def callback(event):
        nonlocal stop
        if event['phase']=='rt_window' and event['completed']==event['total']:
            stop=True
    with pytest.raises(RunCancelled):
        full.run_full(cfg,da.case,progress_callback=callback,should_cancel=lambda:stop)
    assert calls==[0,1,2]
    settle.assert_not_called()
