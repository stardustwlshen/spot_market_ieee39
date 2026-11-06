"""UI verification uses real schema-2 writer output; never a forged passing table."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import shutil
import sys
import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from forecast_error_view import load_forecast_diagnostics
from smm.causal_types import ResourceSpec
from smm.causal_processes import ProcessPlan
from smm.causal_provider import compile_provider
from smm.forecast_diagnostics import make_diagnostic_context,write_post_execution_diagnostics

START='2026-07-15T00:00:00Z'


def _evidence(start=START,timezone='UTC',unexecuted_id=False):
    resources=(ResourceSpec('W','wind',100.),ResourceSpec('PV','pv',80.),
               ResourceSpec('load','load',1000.),ResourceSpec('H','natural_inflow',50.))
    _,provider=compile_provider(ProcessPlan(start,1,timezone,resources,root_seed='42'))
    def snapshot(selected):
        result=selected.evidence();result['day_start_utc']=start
        return {'input_provenance':result}
    da=snapshot(provider.select_day(start,0,0,stage='DA',executed_through_period=0))
    rt=[dict(t=t,snapshot=snapshot(provider.select_day(start,t,t,stage='RT',current_interval=True,
                                                      executed_through_period=t))) for t in range(4)]
    # Legal advance ID: issue is before tau. Do not confuse issue with target start.
    intra=[dict(tau=1,snapshot=snapshot(provider.select_day(start,0,1,stage='ID',executed_through_period=1)))]
    if unexecuted_id:intra.append(dict(tau=8,snapshot=snapshot(provider.select_day(start,0,8,stage='ID',executed_through_period=1))))
    context=make_diagnostic_context(dict(date='2026-07-15',dt_h=.25,periods=96,
        forecast={'model':'causal_physical_v3'},simulation_timezone=timezone),provider.source_manifest(),
        provider.day_summary(start),da['input_provenance']['selected_inputs_hash'])
    return dict(dt_h=.25,project_timezone=timezone,diagnostic_context=context,da=da,rt_windows=rt,
                id_rounds=intra,causal_input_registry=provider.archive(start))


@pytest.fixture(scope='module')
def evidence():return _evidence()


@pytest.fixture(scope='module')
def written(evidence,tmp_path_factory):
    target=tmp_path_factory.mktemp('native_schema2')
    assert write_post_execution_diagnostics(evidence,target)['valid']
    return target


@pytest.fixture
def source(written,tmp_path):
    target=tmp_path/'report';shutil.copytree(written,target)
    return target


def _status(source,**changes):
    path=source/'forecast_error_status.json';status=json.loads(path.read_text());status.update(changes)
    path.write_text(json.dumps(status))


def _table(source,name):return pd.read_csv(source/(name+'.csv'),dtype=str,keep_default_na=False)


def _write(source,name,frame):frame.to_csv(source/(name+'.csv'),index=False)


def _reject(source):
    result=load_forecast_diagnostics(source)
    assert not result['valid'],result
    assert result['reason'] and not any(k in result for k in ('actual','detail','summary'))


def _file_hashes(path):return {p.name:sha256(p.read_bytes()).hexdigest() for p in path.iterdir() if p.is_file()}


def test_native_writer_prefix_and_original_rt_error(source):
    before=_file_hashes(source);data=load_forecast_diagnostics(source)
    assert data['valid'],data
    assert data['verification_scope']=='schema2_context_status_and_csv_internal_consistency'
    assert data['status']['context_origin']=='native_case_snapshot'
    assert len(data['actual'])==16 and data['status']['executed_periods']==4
    assert data['detail'].period.max()==3
    assert (data['summary'].query("stage=='RT' and resource_id=='W'").rmse>0).all()
    assert data['summary'].query("resource_id=='PV'").nrmse_by_executed_mean.isna().all()
    assert _file_hashes(source)==before


def test_explicit_frozen_context_is_accepted_and_labelled(evidence,tmp_path):
    historical=deepcopy(evidence);native=historical.pop('diagnostic_context')
    context=make_diagnostic_context(native['configuration_base'],native['source_manifest'],native['day_summary'],
        native['da_selected_inputs_hash'],origin='explicit_frozen_archive',
        source_records=[dict(file='scenario_snapshot/config/base.yaml',sha256='a'*64)])
    assert write_post_execution_diagnostics(historical,tmp_path,frozen_context=context)['valid']
    data=load_forecast_diagnostics(tmp_path)
    assert data['valid'],data
    assert data['status']['context_origin']=='explicit_frozen_archive'


def test_non_utc_local_date_is_not_relabelled(tmp_path):
    evidence=_evidence('2026-07-14T16:00:00Z','Asia/Shanghai')
    assert write_post_execution_diagnostics(evidence,tmp_path)['valid']
    data=load_forecast_diagnostics(tmp_path)
    assert data['valid'],data
    assert data['status']['operating_date']=='2026-07-15'
    assert set(data['actual'].operating_date)=={'2026-07-15'}


def test_unexecuted_published_id_is_excluded_not_counted_as_actual(tmp_path):
    assert write_post_execution_diagnostics(_evidence(unexecuted_id=True),tmp_path)['valid']
    result=load_forecast_diagnostics(tmp_path);assert result['valid'],result
    assert result['status']['excluded_unexecuted_publication_windows']==1
    assert len(result['actual'])==16 and result['detail'].period.max()==3
    assert result['detail'].window_start.max()==3


def test_advance_id_and_frame_stage_alias_are_legal(source):
    data=load_forecast_diagnostics(source);assert data['valid'],data
    target=data['detail'].query("stage=='ID'")
    assert set(target.window_start)=={1} and set(target.issue_time_utc)=={START}
    # Same published contents legitimately share DA/ID/RT frame identity.
    first=data['detail'].query("window_start==0 or stage=='ID'")
    assert first.forecast_id.nunique()==1


@pytest.mark.parametrize('schema',[1,True,None,3,'2',2.0])
def test_non_schema2_not_upgraded(source,schema):
    _status(source,schema_version=schema);_reject(source)
    if schema==1:assert '历史' in load_forecast_diagnostics(source)['reason']


@pytest.mark.parametrize('key,value',[
    ('context_origin',None),('context_origin','guessed'),('context_hash','bad'),
    ('configuration_hash',None),('source_manifest_hash','A'*64),('da_selected_inputs_hash',''),
    ('source_records',{}),('source_records',[{'file':'base.yaml','sha256':'bad'}]),
    ('source_records',[{'file':'','sha256':'a'*64}]),('errors',['contradiction']),
    ('model','legacy'),('status','guessed'),('valid',1),
    ('executed_periods',True),('executed_periods',4.0),('expected_periods',24),
    ('resource_count',False),('actual_input_rows',15),('forecast_comparisons',False),
    ('publication_windows',False),('excluded_unexecuted_publication_windows',False),
    ('full_day',True),('operating_date','2026-07-16'),('day_start_utc','2026-07-15T00:01:00Z'),
    ('current_measurement_is_not_RT_prediction',False),('error_sign','forecast_minus_actual'),
    ('lead_reference','interval_start'),('scope','future_actual'),
    ('truth','dispatch'),('weighting','equal window means'),('normalized_error_denominator','forecast mean')])
def test_invalid_context_and_status_fail_closed(source,key,value):
    _status(source,**{key:value});_reject(source)


def test_explicit_archive_requires_source_records(source):
    _status(source,context_origin='explicit_frozen_archive',source_records=[]);_reject(source)


def test_missing_or_nonobject_status_not_zero(tmp_path):
    assert not load_forecast_diagnostics(tmp_path)['valid']
    path=tmp_path/'forecast_error_status.json'
    for value in ([],None,{'valid':False,'errors':['missing original forecast']}):
        path.write_text(json.dumps(value));_reject(tmp_path)


@pytest.mark.parametrize('table,column,value',[
    ('executed_input_actual','period','False'),('executed_input_actual','period','0.0'),
    ('executed_input_actual','period','-1'),('executed_input_actual','period','4'),
    ('forecast_error_detail','window_start','False'),('forecast_error_detail','window_start','1.0'),
    ('forecast_error_detail','window_start','-99'),('forecast_error_detail','period','96'),
    ('forecast_error_detail','stage','rt'),('forecast_error_detail','operating_date','2026-07-16'),
    ('forecast_error_detail','valid_start_utc','2026-07-15T00:15:00Z'),
    ('forecast_error_detail','valid_end_utc','2026-07-15T01:00:00Z'),
    ('forecast_error_detail','issue_time_utc','2026-07-15T00:00:00'),
    ('forecast_error_detail','issue_time_utc','2026-07-15T00:00:00.1Z'),
    ('forecast_error_detail','issue_time_utc','2026-07-15T00:01:00Z'),
    ('forecast_error_detail','issue_time_utc','2026-07-14T23:45:00Z'),
    ('forecast_error_detail','issue_time_utc','2026-07-15T00:15:00Z'),
    ('executed_input_actual','valid_start_utc','2026-07-15T00:00:00'),
    ('forecast_error_detail','lead_midpoint_h','0'),('forecast_error_detail','lead_bucket','all'),
    ('forecast_error_detail','actual_input','9999'),('forecast_error_detail','absolute_error','9999'),
    ('forecast_error_detail','squared_error','9999'),('forecast_error_detail','error_actual_minus_forecast','9999'),
    ('forecast_error_detail','actual_selected_inputs_hash','f'*64),('forecast_error_detail','forecast_id','bad'),
    ('forecast_error_detail','forecast_id','f'*64),('forecast_error_detail','information_set_id','f'*64),
    ('forecast_error_detail','unit','MWh'),('forecast_error_detail','kind','unknown'),
    ('executed_input_actual','actual_source','future_actual'),('executed_input_actual','actual_input','-1'),
    ('executed_input_actual','selected_inputs_hash','f'*64),('executed_input_actual','kind','unknown'),
    ('forecast_error_detail','forecast_input','nan'),('forecast_error_detail','forecast_input','inf'),
    ('forecast_error_summary','forecast_comparisons','True'),('forecast_error_summary','unique_executed_periods','1.0')])
def test_bad_csv_time_integer_source_or_arithmetic(source,table,column,value):
    frame=_table(source,table);frame.loc[0,column]=value;_write(source,table,frame);_reject(source)


@pytest.mark.parametrize('column',['mae','rmse','bias_actual_minus_forecast','actual_mean_unique_periods','nrmse_by_executed_mean',
                                   'forecast_comparisons','unique_executed_periods'])
@pytest.mark.parametrize('bucket',['all','0-1h'])
def test_recomputes_each_bucket_and_all_stage_metrics(source,column,bucket):
    frame=_table(source,'forecast_error_summary')
    idx=frame.index[(frame.resource_id=='W')&(frame.stage=='RT')&(frame.lead_bucket==bucket)][0]
    frame.loc[idx,column]=str(float(frame.loc[idx,column])+1) if column not in ('forecast_comparisons','unique_executed_periods') else '1'
    _write(source,'forecast_error_summary',frame);_reject(source)


def test_zero_mean_cannot_be_reported_as_zero_normalized_error(source):
    frame=_table(source,'forecast_error_summary');frame.loc[frame.resource_id=='PV','nrmse_by_executed_mean']='0'
    _write(source,'forecast_error_summary',frame);_reject(source)


@pytest.mark.parametrize('damage',['missing_actual','duplicate_actual','missing_pair','duplicate_pair','missing_bucket','duplicate_all','extra_summary','missing_rt_window','missing_resource','missing_field','duplicate_header'])
def test_complete_prefix_and_summary_coverage(source,damage):
    actual=_table(source,'executed_input_actual');detail=_table(source,'forecast_error_detail');summary=_table(source,'forecast_error_summary')
    if damage=='missing_actual':actual=actual.iloc[1:];_status(source,actual_input_rows=len(actual))
    if damage=='duplicate_actual':actual=pd.concat([actual,actual.iloc[:1]]);_status(source,actual_input_rows=len(actual))
    if damage=='missing_pair':detail=detail.iloc[1:];_status(source,forecast_comparisons=len(detail))
    if damage=='duplicate_pair':detail=pd.concat([detail,detail.iloc[:1]]);_status(source,forecast_comparisons=len(detail))
    if damage=='missing_bucket':summary=summary.iloc[1:]
    if damage=='duplicate_all':summary=pd.concat([summary,summary[summary.lead_bucket=='all'].iloc[:1]])
    if damage=='extra_summary':extra=summary.iloc[:1].copy();extra.loc[:,'resource_id']='ghost';summary=pd.concat([summary,extra])
    if damage=='missing_rt_window':detail=detail[~((detail.stage=='RT')&(detail.window_start=='3'))];_status(source,forecast_comparisons=len(detail),publication_windows=5)
    if damage=='missing_resource':detail=detail[~((detail.stage=='RT')&(detail.window_start=='0')&(detail.resource_id=='W'))];_status(source,forecast_comparisons=len(detail))
    if damage=='missing_field':detail=detail.drop(columns=['squared_error'])
    for name,frame in [('executed_input_actual',actual),('forecast_error_detail',detail),('forecast_error_summary',summary)]:_write(source,name,frame)
    if damage=='duplicate_header':
        path=source/'forecast_error_summary.csv';text=path.read_text();path.write_text(text.replace('stage,resource_id','stage,stage',1))
    _reject(source)


def test_metric_weighting_is_pairs_not_mean_of_window_rmses(source):
    data=load_forecast_diagnostics(source);assert data['valid'],data
    detail=data['detail'].query("stage=='RT' and resource_id=='W'")
    expected=float(np.sqrt(np.mean((detail.actual_input-detail.forecast_input)**2)))
    actual=data['summary'].query("stage=='RT' and resource_id=='W' and lead_bucket=='all'").iloc[0]
    assert actual.rmse==pytest.approx(expected) and actual.forecast_comparisons==10 and actual.unique_executed_periods==4


def test_apptest_stage_resource_read_only(source):
    before=_file_hashes(source)
    code=f"import sys\nsys.path.insert(0,{str(ROOT/'app')!r})\nfrom forecast_error_view import render_forecast_diagnostics\nrender_forecast_diagnostics({str(source)!r})"
    at=AppTest.from_string(code,default_timeout=30).run();assert not at.exception
    assert len(at.get('plotly_chart'))==1
    assert any('不代表每次优化使用了全部预测' in x.value for x in at.caption)
    next(x for x in at.radio if x.label=='原预测发布阶段').set_value('RT').run();assert not at.exception
    assert any('查看一次原始发布'==x.label for x in at.selectbox) and len(at.metric)==3
    next(x for x in at.selectbox if x.label=='误差分析资源').set_value('H').run();assert not at.exception
    assert len(at.metric)==3 and _file_hashes(source)==before


def test_apptest_legacy_not_presented_as_verified(source):
    _status(source,schema_version=1)
    code=f"import sys\nsys.path.insert(0,{str(ROOT/'app')!r})\nfrom forecast_error_view import render_forecast_diagnostics\nrender_forecast_diagnostics({str(source)!r})"
    at=AppTest.from_string(code,default_timeout=30).run();assert not at.exception
    assert not at.get('plotly_chart') and not at.metric
    assert any('历史' in x.value and '不可核验' in x.value for x in at.info)
