from copy import deepcopy
import json
import numpy as np
import pandas as pd
import pytest
from smm.causal_types import ResourceSpec,utc_seconds,utc_iso
from smm.causal_processes import ProcessPlan
from smm.causal_provider import compile_provider
from smm.forecast_diagnostics import post_execution_forecast_diagnostics,write_post_execution_diagnostics,make_diagnostic_context

START='2026-07-15T00:00:00Z'

@pytest.fixture(scope='module')
def evidence():
    resources=(ResourceSpec('W','wind',100.),ResourceSpec('PV','pv',80.),ResourceSpec('load','load',1000.),ResourceSpec('H','natural_inflow',50.))
    _,p=compile_provider(ProcessPlan(START,1,'UTC',resources,root_seed='42'))
    def snap(s):
        ev=s.evidence();ev['day_start_utc']=START
        return {'input_provenance':ev}
    da=snap(p.select_day(START,0,0,stage='DA',executed_through_period=0))
    rt=[dict(t=t,snapshot=snap(p.select_day(START,t,t,stage='RT',current_interval=True,executed_through_period=t))) for t in range(4)]
    intra=[dict(tau=1,snapshot=snap(p.select_day(START,0,1,stage='ID',executed_through_period=1)))]
    context=make_diagnostic_context(dict(date='2026-07-15',dt_h=.25,periods=96,forecast={'model':'causal_physical_v3'},simulation_timezone='UTC'),
        p.source_manifest(),p.day_summary(START),da['input_provenance']['selected_inputs_hash'])
    return dict(dt_h=.25,project_timezone='UTC',diagnostic_context=context,da=da,rt_windows=rt,id_rounds=intra,causal_input_registry=p.archive(START))


def test_post_execution_only_and_original_rt_prediction(evidence):
    result=post_execution_forecast_diagnostics(evidence)
    assert result['status']['valid'] and result['status']['executed_periods']==4
    assert len(result['actual'])==16 and not result['actual'].duplicated(['resource_id','period']).any()
    detail=result['detail'];assert detail.period.max()==3
    da=detail[detail.stage=='DA'];assert len(da)==16
    assert set(detail.resource_id)=={'W','PV','load','H'}
    rt=detail[(detail.stage=='RT')&(detail.window_start==detail.period)&(detail.resource_id=='W')]
    assert (rt.absolute_error>1e-8).any()  # NOT the overwritten perfect current input.
    assert len(detail)>len(result['actual']) and result['status']['current_measurement_is_not_RT_prediction']
    assert np.array_equal(da.error_actual_minus_forecast,da.actual_input-da.forecast_input)
    night=result['summary'][(result['summary'].resource_id=='PV')&(result['summary'].lead_bucket=='all')]
    assert night.nrmse_by_executed_mean.isna().all() # no fabricated zero denominator metric


@pytest.mark.parametrize('damage',['registry','hole','measurement','forecast_hash','timezone'])
def test_missing_or_inconsistent_evidence_is_unverifiable(evidence,damage):
    bad=deepcopy(evidence)
    if damage=='registry':bad.pop('causal_input_registry')
    if damage=='hole':bad['rt_windows'].pop(1)
    if damage=='measurement':bad['rt_windows'][0]['snapshot']=bad['da']
    if damage=='timezone':bad['project_timezone']='America/Los_Angeles'
    if damage=='forecast_hash':next(iter(bad['causal_input_registry']['forecasts'].values()))['series'][0]['values'][0]+=1
    got=post_execution_forecast_diagnostics(bad)
    assert not got['status']['valid'] and got['status']['errors']
    assert got['actual'].empty and got['detail'].empty and got['summary'].empty


def test_writer_csv_and_summary_weighting(evidence,tmp_path):
    status=write_post_execution_diagnostics(evidence,tmp_path)
    assert status==json.loads((tmp_path/'forecast_error_status.json').read_text())
    detail=pd.read_csv(tmp_path/'forecast_error_detail.csv');summary=pd.read_csv(tmp_path/'forecast_error_summary.csv')
    for _,row in summary[summary.lead_bucket=='all'].iterrows():
        selected=detail[(detail.stage==row.stage)&(detail.resource_id==row.resource_id)]
        assert row.forecast_comparisons==len(selected)
        assert row.mae==pytest.approx(abs(selected.error_actual_minus_forecast).mean())
        assert row.rmse==pytest.approx(np.sqrt(np.mean(selected.error_actual_minus_forecast**2)))


def test_operating_date_uses_saved_timezone(evidence):
    # This day starts at 00:00 UTC, which is the same local calendar date in Shanghai.
    shifted=deepcopy(evidence);shifted['project_timezone']='America/Los_Angeles'
    got=post_execution_forecast_diagnostics(shifted)
    assert not got['status']['valid']  # Cannot relabel UTC data using a different saved timezone.

from test_causal_inflow_integration import causal_context

def test_full_output_writer_creates_post_execution_reports(causal_context,tmp_path,monkeypatch):
    from smm.full import run_full,write_full_outputs
    actual,provider,case=causal_context
    # A 96-period output smoke test is larger than tiny_case's one-period 5s budget.
    monkeypatch.setitem(case.cfg.base['solver'],'time_limit_s',30.)
    result=run_full(case.cfg,case,periods=2,log=lambda *a:None)
    write_full_outputs(result,tmp_path,make_plots=False)
    status=json.loads((tmp_path/'forecast_error_status.json').read_text())
    assert status['valid'] and status['executed_periods']==2 and not status['full_day']
    detail=pd.read_csv(tmp_path/'forecast_error_detail.csv')
    assert detail.period.max()==1 and not detail.empty
    catalog=json.loads((tmp_path/'output_manifest.json').read_text())
    items={x['file']:x for x in catalog['artifacts']}
    assert all(items[f]['present'] for f in ('executed_input_actual.csv','forecast_error_detail.csv','forecast_error_summary.csv','forecast_error_status.json'))
