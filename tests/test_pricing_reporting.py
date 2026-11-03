"""Price clone diagnostics must never be represented as dispatched quantities."""
import json
from types import SimpleNamespace as NS
import numpy as np
import pandas as pd
from smm import results
from smm.full import write_pricing_evidence,execution_safety_checks
from test_full_safety import fixture


def test_nodal_duals_and_dispatch_duals_are_separate():
    m=NS(_md=NS(T=1,buses=[1]))
    def prices(x):return dict(lmp=np.array([[x]]),lmp_clamped=np.array([[min(x,800.)]]),energy=np.array([x]),congestion=np.array([[0.]]),lmp_decomp=np.array([[x]]))
    frame=results.price_tables(m,prices(900.),0.,800.,dispatch_prices=prices(1100.),pricing_source='bounded_auxiliary_pricing')
    assert frame.iloc[0].lmp_raw==900.
    assert frame.iloc[0].lmp==800.
    assert frame.iloc[0].dispatch_raw_lmp==1100.
    assert frame.iloc[0].physical_quantity_source=='standard_sced'
    assert frame.iloc[0].settlement_price_source.endswith('_clipped_to_market_limits')
    old=results.price_tables(m,prices(100.),0.,800.)
    assert old.iloc[0].dispatch_raw_lmp==old.iloc[0].lmp_raw
    assert old.iloc[0].pricing_source=='standard_sced'


def test_aux_dual_activity_does_not_change_physical_flow():
    m=NS(_md=NS(T=1,lines=['L']),_case=NS(net=NS(branches=pd.DataFrame([dict(branch_id='L',rate_mw=100.)]))),
        flow={('L',0):40.},sl={('L',0):0.})
    rows=results.line_results(m,dict(mu_plus=np.array([[12.]]),mu_minus=np.array([[0.]])),dual_source='bounded_auxiliary_pricing')
    r=rows.iloc[0]
    assert r.flow_mw==40.
    assert not r.dispatch_at_capacity and r.pricing_dual_active
    assert r.flow_source=='standard_sced' and r.dual_source=='bounded_auxiliary_pricing'


def test_safety_uses_formal_overload_with_auxiliary_prices():
    da,rt=fixture(574.6)
    rt['prices']['pricing_source']='bounded_auxiliary_pricing'
    check=execution_safety_checks(da,rt)
    assert not check['rt_network_feasible']
    assert check['rt_pricing_sources']==['bounded_auxiliary_pricing']
    assert 'same_fixed_integer' not in check['price_source']


def test_export_diagnostics_does_not_promote_aux_quantities_to_execution(tmp_path):
    info=dict(bands=[dict(unit='G',dispatch_mw=20.,auxiliary_mw=24.,deviation_mw=4.)],
              removed_rt_tracking=[dict(kind='V',dispatch_target_deviation_native=0.,auxiliary_target_deviation_native=2.)],
              auxiliary_prices={'raw':{'lmp':np.array([[7.]])}},disabled_price=float('nan'))
    da=NS(pricing_info=None)
    out=write_pricing_evidence(da,[dict(t=0,pricing_info=info)],tmp_path)
    saved=json.loads((tmp_path/'auxiliary_pricing_evidence.json').read_text())
    assert saved['pricing_quantities_executable'] is False
    assert saved['rt_windows'][0]['diagnostics']['disabled_price'] is None
    table=pd.read_csv(tmp_path/'auxiliary_pricing_bands.csv')
    assert table.iloc[0].dispatch_mw==20. and table.iloc[0].auxiliary_mw==24.
    assert out['settlement_quantity_source']=='formal_DA_and_executed_RT_first_period'
