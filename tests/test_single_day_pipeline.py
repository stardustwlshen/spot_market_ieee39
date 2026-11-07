"""Real 24-hour reduced-network DA/ID/RT/settlement smoke, not IEEE39 acceptance."""
import json
import pandas as pd
from smm import full
from test_hydro_rolling import hydro_case


def test_single_day_all_96_periods_and_intraday_outputs(tmp_path):
    case=hydro_case(T=96)
    cfg=case.cfg
    cfg.units['hydro'][0]['seasons']['wet']['e_day']=2000.
    cfg.mode['intraday'].update(enabled=True,interval_h=4.)
    events=[]
    result=full.run_full(cfg,case,periods=None,log=lambda *a: None,progress_callback=events.append)
    assert len(result['rr'].rt)==96
    assert result['rr'].id_rounds
    assert not result['rr'].id_failures
    summary=full.write_full_outputs(result,tmp_path,make_plots=False)
    assert summary['completed_scope']=='full_day'
    assert summary['settlement_scope']=='full_day'
    assert summary['settled'] and summary['executed_periods']==96
    assert set(pd.read_csv(tmp_path/'rt_units.csv').period)==set(range(96))
    assert not pd.read_csv(tmp_path/'id_rounds_summary.csv').empty
    assert not pd.read_csv(tmp_path/'id_plans.csv').empty
    assert not pd.read_csv(tmp_path/'da_unit_awards.csv').empty
    assert not pd.read_csv(tmp_path/'settlement_detail.csv').empty
    assert result['settle']['reconciliation'].residual.abs().max()<1e-6
    states=json.loads((tmp_path/'rolling_actual_states.json').read_text())
    assert [s['t'] for s in states['windows']]==list(range(96))
    assert any(e['phase']=='id_round' for e in events)
    assert len([e for e in events if e['phase']=='rt_window'])==96
