"""Enabled operational price flow never substitutes auxiliary dispatch."""
from copy import deepcopy
import json
import numpy as np
import pyomo.environ as pyo
import pytest
from smm.da import run_da
from smm import rolling, scuc
from test_da_stages import tiny_case
from test_hydro_rolling import hydro_case


def enable(case):
    case.cfg.mode['auxiliary_pricing'].update(enabled=True, rt_tracking_policy='remove')
    case.cfg.mode['penalties']['line']=10000.
    return case


def test_enabled_da_retains_formal_dispatch_and_exposes_both_prices():
    case=enable(tiny_case(reserve=500))
    case.cfg.mode['price_limits']['bid_min']=-1000
    case.bids['units']['CHEAP']['segments'][0]['price']=-500
    off=deepcopy(case);off.cfg.mode['auxiliary_pricing']['enabled']=False
    original=run_da(off,verbose=False)
    result=run_da(case,verbose=False)
    assert result.pricing_model is not result.model
    for name in ('P','flow','sR_up','sl'):
        for i,v in getattr(result.model,name).items():
            assert pyo.value(v)==pytest.approx(pyo.value(getattr(original.model,name)[i]))
    assert result.costs==original.costs and result.slack==original.slack
    np.testing.assert_allclose(result.dispatch_prices['lmp'], original.prices['lmp'])
    np.testing.assert_allclose(result.prices['lmp'], scuc.extract_prices(result.pricing_model)['lmp'])
    assert {'dispatch_raw_lmp','dispatch_energy','dispatch_congestion','pricing_source'} <= set(result.tables['prices'])
    assert set(result.tables['prices'].pricing_source)=={'bounded_auxiliary_pricing'}
    assert set(result.tables['lines'].dual_source)=={'bounded_auxiliary_pricing'}
    assert set(result.tables['lines'].flow_source)=={'standard_sced'}
    assert result.stage_diagnostics['auxiliary_pricing']['summary']['quantities_execute'] is False
    assert result.pricing_info['auxiliary_prices']['selected_for_settlement'] is True
    assert result.pricing_info['auxiliary_prices']['settlement_applied'] is True
    json.dumps(result.pricing_info,allow_nan=False)


def test_enabled_da_rt_rolls_only_formal_actual_state_and_prices_are_auxiliary():
    case=enable(hydro_case(T=8))
    case.cfg.mode['realtime']['tracking_policy']=dict(mode='soft_deliverable_energy',penalty_yuan_per_discharge_mwh=1000.)
    da=run_da(case,verbose=False)
    rr=rolling.run_rolling(da,periods=2,log=lambda *a:None)
    for row in rr.rt:
        assert row['pricing_source']=='bounded_auxiliary_pricing'
        assert row['repriced_meaning']=='separate_bounded_auxiliary_clone'
        assert row['pricing_info']['original_dispatch_unchanged']
        assert row['pricing_info']['removed_rt_tracking']
        np.testing.assert_allclose(row['prices']['lmp'], row['pricing_info']['auxiliary_prices']['raw']['lmp'])
        json.dumps(row['pricing_info'],allow_nan=False)
    assert rr.states[1].hydro['H1']==pytest.approx(rr.rt[0]['snap']['Vh']['H1'][0])
    assert rr.states[1].hydro_p['H1']==pytest.approx(rr.rt[0]['snap']['Ph']['H1'][0])
    tables=rolling.rt_tables(da,rr)
    assert set(tables['prices'].pricing_source)=={'bounded_auxiliary_pricing'}
    assert 'dispatch_raw_lmp' in tables['prices']
    assert set(tables['lines'].dual_source)=={'bounded_auxiliary_pricing'}


def test_enabled_aux_failure_is_not_silently_replaced(monkeypatch):
    from smm import auxiliary_pricing
    def fail(m):raise RuntimeError('auxiliary diagnostic failure')
    monkeypatch.setattr(auxiliary_pricing,'run_auxiliary_pricing',fail)
    with pytest.raises(RuntimeError,match='auxiliary diagnostic failure'):
        run_da(enable(tiny_case(reserve=0)),verbose=False)
    with pytest.raises(RuntimeError,match='auxiliary diagnostic failure'):
        rolling.clear_window(enable(tiny_case(reserve=0)))


def test_agc_combination_rejected_before_any_solver(monkeypatch):
    case=enable(tiny_case(reserve=0))
    case.cfg.mode['regulation']['enabled']=True
    def forbidden(*a,**kw):raise AssertionError('must reject before solve')
    monkeypatch.setattr(scuc,'build_model',forbidden)
    for call in (lambda:run_da(case,verbose=False),lambda:rolling.clear_window(case)):
        with pytest.raises(ValueError,match='regulation disabled'):
            call()
