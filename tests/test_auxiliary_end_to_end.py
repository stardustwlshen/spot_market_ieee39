"""Real tiny complete pipeline, including settlement and all artifact writers."""
from copy import deepcopy
import json
import numpy as np
import pandas as pd
from smm import full,rolling
from smm.config import load_config
from smm.bids import default_bids
from smm.live_runner import capture_provenance
from test_hydro_rolling import hydro_case


def test_enabled_aux_soft_tracking_prefix_settlement_and_artifacts(tmp_path):
    case=hydro_case(T=8);cfg=case.cfg
    cfg.mode['auxiliary_pricing'].update(enabled=True,rt_tracking_policy='remove')
    cfg.mode['penalties']['line']=10000.
    cfg.mode['realtime']['tracking_policy']=dict(mode='soft_deliverable_energy',penalty_yuan_per_discharge_mwh=1000.)
    cfg.mode['reserve'].update(storage_reserve=True,down_enabled=False,T_dur_h=.25,rho_L=.05)
    case.R_up=np.full(8,6.);case.R_dn=np.zeros(8)
    source=load_config();battery=deepcopy(source.units['storage'][0]);battery.update(bus=2,eta_c=.8,eta_d=.9,soc0=.5)
    cfg.units['storage']=[battery]
    case.bids['units'][battery['id']]=deepcopy(default_bids(source)['units'][battery['id']])
    case.bids['units'][battery['id']]['soc_end']=None
    capture_provenance(tmp_path,cfg.root)
    res=full.run_full(cfg,case,periods=2,log=lambda *a:None)
    summary=full.write_full_outputs(res,tmp_path,make_plots=False)
    da=res['da'];rr=res['rr'];detail=res['settle']['detail']
    dasn=rolling.snapshot(da.model)
    assert dasn['reserve']['T_dur_h']==.25
    assert all(r['snap']['reserve']['T_dur_h']==.25 for r in rr.rt)
    assert all(r['T_dur_h']==.25 for r in dasn['reserve']['resources'])
    for row in detail[detail.entity=='H1'].itertuples():
        assert abs(row.q_da-dasn['Ph']['H1'][row.period])<1e-7
        assert abs(row.q_rt-rr.rt[row.period]['snap']['Ph']['H1'][0])<1e-7
    np.testing.assert_allclose(detail.total,detail.q_da*detail.lmp_da*cfg.dt+(detail.q_rt-detail.q_da)*detail.lmp_rt*cfg.dt,rtol=0,atol=1e-7)
    assert res['settle']['reconciliation'].residual.abs().max()<1e-6
    for row in detail.itertuples():
        bi=case.net.buses.index(row.bus)
        assert row.lmp_da==da.prices['lmp_clamped'][bi,row.period]
        assert row.lmp_rt==rr.rt[row.period]['prices']['lmp_clamped'][bi,0]
    assert res['checks']['rt_raw_lmp_decomposition_max_err']<1e-7
    for path in ('da_nodal_prices.csv','rt_prices.csv'):
        prices=pd.read_csv(tmp_path/path)
        assert {'dispatch_raw_lmp','dispatch_energy','dispatch_congestion'}<=set(prices)
        assert set(prices.pricing_source)=={'bounded_auxiliary_pricing'}
    for scope in (res,summary,json.loads((tmp_path/'summary.json').read_text()),json.loads((tmp_path/'output_scope.json').read_text())):
        assert scope['completed_scope']=='debug_prefix'
        assert scope['settlement_scope']=='illustrative_prefix'
        assert scope['executed_periods']==2 and scope['expected_periods']==8
        assert scope['settlement_calculated'] and not scope['settled']
    assert summary['provenance']['source_fingerprint']==json.loads((tmp_path/'run_provenance.json').read_text())['source']['content_fingerprint']
    assert set(pd.read_csv(tmp_path/'settlement_detail.csv').settlement_scope)=={'illustrative_prefix'}
    aux=json.loads((tmp_path/'auxiliary_pricing_evidence.json').read_text())
    assert aux['pricing_quantities_executable'] is False
    assert len(aux['rt_windows'])==2
    for window in aux['rt_windows']:
        removed=window['diagnostics']['removed_rt_tracking']
        assert removed
        assert all('auxiliary_target_deviation_native' in row for row in removed)
    assert all(r['pricing_info']['original_dispatch_unchanged'] for r in rr.rt)
    assert not res['rt']['reserve_system'].reserve_down_enabled.any()

    actual_states=json.loads((tmp_path/'rolling_actual_states.json').read_text())
    assert [s['t'] for s in actual_states['windows']]==[0,1]
    assert all('pumped_used' in s and 'pumped_hist' in s for s in actual_states['windows'])
    assert len(actual_states['windows'][1]['hydro_hist']['H1'])==1
