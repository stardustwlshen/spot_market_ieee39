"""Small real optimization and source-tamper tests for every new inflow consumer."""
from copy import deepcopy
from dataclasses import replace
import numpy as np
import pandas as pd
import pyomo.environ as pyo
import pytest
from smm.causal_types import *
from smm.causal_processes import ProcessPlan
from smm.causal_provider import compile_provider,save_bundle,load_bundle
from smm.natural_inflow import *
from smm import rolling,scuc
from smm.case import build_da_case
from smm.da import run_da
from smm.full import hydro_window_checks,hydro_terminal_lookahead_checks
from test_hydro_rolling import hydro_case

START='2026-07-15T00:00:00Z'

@pytest.fixture(scope='module')
def causal_context():
    old=hydro_case(T=96);cfg=old.cfg
    cfg.base['forecast']={'model':MODEL}
    cfg.base['solver']['threads']=4
    cfg.units['hydro'][0].update(e_day_min=200.,ramp=100.)
    cfg.mode['realtime']['hydro']='value'
    cfg.mode['realtime']['storage']['mode']='value'
    cfg.mode['realtime']['value_method']['terminal_policy']={'mode':'soft','penalty_yuan_per_discharge_mwh':1000.}
    cfg.mode['realtime']['value_method']['hydro_lookahead']='reference_continuation'
    resources=(ResourceSpec('load','load',120.),ResourceSpec('H1','natural_inflow',50.))
    plan=ProcessPlan(START,2,'UTC',resources,{'2026-07-15':{'natural_inflow':'inflow_rising'}},root_seed='42')
    actual,provider=compile_provider(plan)
    case=build_da_case(cfg,old.net,pd.DataFrame({'period':range(1,97)}),old.bids,
                       input_provider=provider,day_start_utc=START)
    return actual,provider,case


def test_bundle_roundtrip_and_immutability(causal_context,tmp_path):
    actual,provider,_=causal_context
    save_bundle(tmp_path/'source',actual)
    restored,p2=load_bundle(tmp_path/'source')
    assert restored.content_hash==actual.content_hash
    a=provider.select_day(START,0,0,stage='DA',executed_through_period=0)
    b=p2.select_day(START,0,0,stage='DA',executed_through_period=0)
    assert a.content_hash==b.content_hash
    assert deepcopy(provider) is provider
    with pytest.raises(FileExistsError): save_bundle(tmp_path/'source',actual)


def test_case_no_future_actual_and_model_array(causal_context):
    actual,provider,case=causal_context
    assert not any(c.endswith(('_actual','_id','_rt')) for c in case.profiles)
    md=scuc.prepare(case)
    assert md.hydro['H1']['qin'] is None
    np.testing.assert_array_equal(md.hydro['H1']['qin_series'],case.selected_inputs.values('H1'))
    with pytest.raises(ValueError,match='fallback'): natural_values(case.cfg,'H1',range(8))
    with pytest.raises(ValueError): provider.select_day(START,4,4,stage='RT',executed_through_period=3)
    with pytest.raises(ValueError): provider.select_day(START,0,1,stage='RT',current_interval=True,executed_through_period=0)


@pytest.fixture(scope='module')
def cleared(causal_context):
    actual,provider,case=causal_context
    da=run_da(case,verbose=False)
    plan=rolling.snapshot(da.model);plan['state_values']=scuc.state_values(da.model)
    state=rolling.initial_state(case.cfg)
    first=rolling.run_rt_period(case,plan,plan,case.cfg,state,0)
    return actual,provider,da,plan,state,first


def test_real_da_rt_water_same_issue_and_first_observation(cleared):
    actual,provider,da,plan,state,first=cleared
    case=da.case
    assert len(da.tables['hydro'])==96
    assert len(first['snap']['Ph']['H1'])==8
    q=first['snap']['hydro_constraints']['H1']['qin_m3s']
    assert q[0]==actual.current_measurement(START)['H1']
    selected=first['model']._case.selected_inputs
    np.testing.assert_array_equal(q,selected.values('H1')[:8])
    assert not np.array_equal(selected.values('H1')[1:],actual.actual('H1',START,'2026-07-16T00:00:00Z')[1:])
    for m in (da.model,first['model']):
        assert max(abs(pyo.value(m.c_water['H1',t].body)) for t in m.T)<1e-6
    nxt=rolling.advance(state,first['snap'],case.cfg)
    expected=state.hydro['H1']+(q[0]-first['snap']['Qout']['H1'][0])*.25*.36
    assert nxt.hydro['H1']==pytest.approx(expected,abs=1e-6)
    assert set(['Q_natural','Q_upstream'])<=set(da.tables['hydro'].columns)


def test_soft_lookahead_and_suffix_share_selected_source(cleared):
    actual,provider,da,plan,state,first=cleared
    selected=first['model']._case.selected_inputs;md=first['model']._md
    row=first['snap']['hydro_terminal_lookahead'][0]
    np.testing.assert_array_equal(row['natural_inflow_m3s'],selected.values('H1')[8:])
    expected=sum(selected.values('H1')[8:]-np.asarray(row['reference_outflows_m3s']))*.25*.36
    assert row['reference_recovery_volume']==pytest.approx(expected)
    assert len(md.hydro['H1']['qin_series'])==96
    suffix=first['model'].hydro_daily_energy_suffix
    assert len(suffix.F)==88
    for t in suffix.F:
        q=md.hydro['H1']['qin_series'][t]
        before=pyo.value(first['model'].V['H1',t-1]) if t==8 else pyo.value(suffix.V['H1',t-1])
        after=pyo.value(suffix.V['H1',t])
        out=pyo.value(suffix.Ph['H1',t]+suffix.Qspill['H1',t])
        assert after==pytest.approx(before+(q-out)*.25*.36,abs=1e-6)


def test_independent_source_audit_detects_tamper(cleared):
    from types import SimpleNamespace as NS
    actual,provider,da,plan,state,first=cleared
    nxt=rolling.advance(state,first['snap'],da.case.cfg)
    rr=NS(rt=[first],states=[state,nxt],id_rounds=[],id_failures=[])
    result=hydro_window_checks(da,rr)
    assert result['hydro_window_checks_available']
    assert result['hydro_water_balance_max_err_1e4m3']<1e-6
    bad=dict(first,snap=deepcopy(first['snap']))
    bad['snap']['hydro_constraints']['H1']['qin_m3s'][2]+=1
    assert not hydro_window_checks(da,NS(rt=[bad],states=[state,nxt]))['hydro_window_checks_available']
    ev=deepcopy(first['snap']['input_provenance']);ev['rows'][0]['values'][2]+=1
    with pytest.raises(ValueError,match='hash'): selected_from_evidence(ev)


def test_output_evidence_does_not_require_future_profiles(cleared,tmp_path):
    from smm.output_catalog import write_profile_evidence
    actual,provider,da,*_=cleared
    write_profile_evidence(da.case,tmp_path)
    assert (tmp_path/'causal_forecast_registry.json').is_file()
    assert pd.read_csv(tmp_path/'forecast_accuracy.csv').empty


def test_seasonal_baseline_is_explicit_and_fixed_reference():
    from smm.causal_provider import seasonal_inflow_spec
    r=ResourceSpec('H1','natural_inflow',100.)
    p=seasonal_inflow_spec(r,50.,'inflow_rising')
    assert p['parameters']['target']==.5 and p['parameters']['amplitude']==.5
    assert r.scale==100.
    with pytest.raises(ValueError): seasonal_inflow_spec(r,1000.,'inflow_pulse')


def test_archive_requires_registry_and_matches_issued_future(cleared):
    actual,provider,da,plan,state,first=cleared
    sn=first['snap'];cfg=da.case.cfg
    with pytest.raises(ValueError,match='registry'): evidence_natural_values(cfg,sn,'H1',[8])
    archived=provider.archive()
    expected=evidence_natural_values(cfg,sn,'H1',range(8,96),source_registry=archived)
    np.testing.assert_array_equal(expected,first['model']._case.selected_inputs.values('H1')[8:])
    damaged=deepcopy(archived); fid=sn['input_provenance']['forecast_id']
    damaged['forecasts'][fid]['series'][0]['values'][10]+=1
    with pytest.raises(ValueError,match='hash'): evidence_natural_values(cfg,sn,'H1',[10],source_registry=damaged)


def test_wrong_issue_or_truncated_source_is_rejected(causal_context):
    actual,provider,case=causal_context
    state=rolling.initial_state(case.cfg);state.t=1
    stale=provider.select_day(START,0,1,stage='ID',executed_through_period=1)
    with pytest.raises(ValueError,match='issue'):
        rolling.make_window_case(case,case.cfg,1,9,1,False,state,{}, {},'RT',selected_inputs=stale)
    frame=provider.request(PredictionRequest(START,START,'2026-07-16T00:00:00Z'))
    truncated=frame.select(START,'2026-07-15T02:00:00Z')
    with pytest.raises(ValueError,match='coverage'):
        rolling.make_window_case(case,case.cfg,0,8,0,False,rolling.initial_state(case.cfg),{}, {},'RT',selected_inputs=truncated)


def test_consumer_contract_and_per_day_registry(causal_context):
    from smm.causal_integration import consumer_capabilities
    from smm.causal_presets import capabilities
    c=consumer_capabilities()
    assert c['ready'] and c['contract_version']=='causal_inputs_qin_v3_1'
    assert capabilities()['consumer_integration_ready']
    actual,provider,case=causal_context
    first=provider.select_day(START,0,0,stage='DA',executed_through_period=0)
    day2='2026-07-16T00:00:00Z'
    second=provider.select_day(day2,0,0,stage='DA',executed_through_period=0)
    one=provider.archive(START);two=provider.archive(day2)
    assert first.content_hash in one['selections'] and second.content_hash not in one['selections']
    assert second.content_hash in two['selections'] and first.content_hash not in two['selections']
    assert set(one['forecasts'])=={s['forecast_id'] for s in one['selections'].values()}
    assert not set(one['forecasts'])&set(two['forecasts'])
    assert next(r['scale'] for r in provider.source_manifest()['public_resources'] if r['resource_id']=='load')==120.


def test_config_load_reference_and_zero_qin_are_explicit(causal_context):
    from smm.causal_provider import resources_from_config
    cfg=deepcopy(causal_context[2].cfg)
    cfg.base['load_scale']=.5
    resources=resources_from_config(cfg)
    load=next(r for r in resources if r.kind=='load')
    assert load.scale==6254.23*.5
    plan=ProcessPlan(START,1,'UTC',resources)
    actual,provider=compile_provider(plan)
    contract=provider.source_manifest()['public_load_scale_contract']
    assert contract['config_adapter_reference_mw']==6254.23
    assert contract['network_load_role']=='nodal_distribution_weights_only'
    assert contract['actual_public_scales_mw']['load']==load.scale
    cfg.units['hydro'][0]['seasons'][cfg.base['season']]['qin']=0.
    with pytest.raises(ValueError,match='positive natural inflow reference'):
        resources_from_config(cfg)


def test_rt_water_source_columns_survive_csv(cleared,tmp_path):
    from types import SimpleNamespace as NS
    actual,provider,da,plan,state,first=cleared
    rr=NS(rt=[dict(first,t=0)],states=[state,rolling.advance(state,first['snap'],da.case.cfg)])
    frame=rolling.rt_tables(da,rr)['hydro']
    path=tmp_path/'rt_hydro.csv';frame.to_csv(path,index=False)
    saved=pd.read_csv(path)
    assert len(saved)==1
    row=saved.iloc[0]
    assert row.Q_natural==pytest.approx(actual.current_measurement(START)['H1'])
    assert row.Q_in==pytest.approx(row.Q_natural+row.Q_upstream)
    assert row.natural_input_source=='current_interval_perfect_measurement'
    assert row.natural_forecast_id==first['snap']['input_provenance']['forecast_id']
    assert row.natural_issue_time_utc==START
