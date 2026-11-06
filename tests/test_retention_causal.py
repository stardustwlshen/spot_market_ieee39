"""Short true-v3 actual-input/suffix run; not another complete-day solve."""
import json,copy
from types import SimpleNamespace as NS
import pytest
from smm import full,result_retention as retention
from smm.hydro_daily_energy import audit_exported
from test_causal_inflow_integration import causal_context


def test_compact_v3_registry_and_hydro_suffix(causal_context,tmp_path):
    actual,provider,case=causal_context
    case.cfg.mode['intraday'].update(enabled=True,interval_h=6.,mip_rel_gap=0.)
    case.cfg.base['solver']['mip_rel_gap']=0.
    case.cfg.mode['auxiliary_pricing']['enabled']=True
    result=full.run_full(case.cfg,case,periods=2,log=lambda *a:None,retain_id_models=False)
    assert result['rr'].id_models_retained is False and len(result['rr'].id_rounds)==1
    row=result['rr'].id_rounds[0]
    assert 'model' not in row and 'pricing_model' not in row
    assert row['snap']['hydro_daily_energy']['active']
    archive=retention.archive_state_evidence(result['rr'])
    assert len(retention.validate_archived_state_evidence(json.loads(json.dumps(archive))))==1
    d=tmp_path/'result';full.write_full_outputs(result,d,make_plots=False)
    hydro=json.loads((d/'hydro_window_evidence.json').read_text());states=json.loads((d/'rolling_actual_states.json').read_text())
    check=audit_exported(case.cfg,hydro,states,bids=case.bids)
    assert check['hydro_daily_minimum_evidence_valid']
    assert check['hydro_daily_minimum_full_day'] is False
    for r in result['rr'].rt:
        expected=actual.current_measurement(r['snap']['input_provenance']['issue_time_utc'])['H1']
        assert r['snap']['hydro_constraints']['H1']['qin_m3s'][0]==expected
    assert json.loads((d/'forecast_error_status.json').read_text())['valid']
    (tmp_path/'causal_compact_report.json').write_text(json.dumps(dict(scope='true causal_v3 2 x 15min RT, 1 ID, 96period DA/suffix, auxiliary enabled; not full day',hydro_audit=check,DA_gap=result['da'].scuc_info['gap'],ID_gaps=[r['mip']['gap'] for r in result['rr'].id_rounds],RT_gaps=[r['mip']['gap'] for r in result['rr'].rt]),indent=2))
