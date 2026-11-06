from copy import deepcopy
from pathlib import Path
import hashlib
import pytest
from smm.causal_types import digest
from smm.forecast_diagnostics import post_execution_forecast_diagnostics,make_diagnostic_context
from test_forecast_diagnostics import evidence


def rebuild(context):
    return make_diagnostic_context(context['configuration_base'],context['source_manifest'],context['day_summary'],
        context['da_selected_inputs_hash'],origin=context['origin'],source_records=context['source_records'])


@pytest.mark.parametrize('where,key,value',[
 ('id','tau',-99),('id','tau',False),('id','tau',1.),('id','tau',2),('id','tau',96),
 ('rt','t',False),('rt','t',0.),('rt','t',-1),
 ('top','dt_h',1.),('top','dt_h',True),('top','dt_h','0.25'),
 ('id','stage','RT'),('rt','stage','ID'),('id','issue_t',True),('id','issue_t',2),
 ('top','project_timezone','America/Los_Angeles'),('top','operating_date','2026-07-16')])
def test_contradictory_metadata_rejected(evidence,where,key,value):
    bad=deepcopy(evidence)
    target=bad['id_rounds'][0] if where=='id' else bad['rt_windows'][0] if where=='rt' else bad
    target[key]=value
    got=post_execution_forecast_diagnostics(bad)
    assert not got['status']['valid'],(where,key,value)
    assert got['actual'].empty and got['detail'].empty and got['summary'].empty


@pytest.mark.parametrize('field,value',[('date','2026-07-16'),('dt_h',1.),('periods',True),('periods',96.),('simulation_timezone','Asia/Shanghai')])
def test_frozen_config_must_agree_even_with_recomputed_context_hash(evidence,field,value):
    bad=deepcopy(evidence);ctx=bad['diagnostic_context'];ctx['configuration_base'][field]=value
    bad['diagnostic_context']=rebuild(ctx)
    assert not post_execution_forecast_diagnostics(bad)['status']['valid']


def test_missing_context_is_unknown_not_inferred(evidence):
    bad=deepcopy(evidence);bad.pop('diagnostic_context')
    assert not post_execution_forecast_diagnostics(bad)['status']['valid']
    bad=deepcopy(evidence);bad.pop('dt_h')
    assert not post_execution_forecast_diagnostics(bad)['status']['valid']


def test_old_archive_explicit_frozen_context_with_valid_early_id(evidence):
    old=deepcopy(evidence);ctx=old.pop('diagnostic_context')
    old.pop('project_timezone');old['causal_input_registry'].pop('source_manifest')
    # Each supplied context field comes from an archived source, not inferred from labels.
    ctx['origin']='explicit_frozen_archive';ctx['source_records']=[{'file':'original_config.json','sha256':digest(ctx['configuration_base'])}]
    ctx=rebuild(ctx)
    got=post_execution_forecast_diagnostics(old,frozen_context=ctx)
    assert got['status']['valid'] and got['status']['context_origin']=='explicit_frozen_archive'
    id_rows=got['detail'][got['detail'].stage=='ID']
    assert set(id_rows.window_start)=={1} and set(id_rows.issue_time_utc)=={'2026-07-15T00:00:00Z'}
    assert id_rows.period.min()==1


def test_external_context_cannot_override_native_context(evidence):
    ctx=deepcopy(evidence['diagnostic_context']);ctx['configuration_base']['date']='2026-07-16';ctx=rebuild(ctx)
    assert not post_execution_forecast_diagnostics(evidence,frozen_context=ctx)['status']['valid']


def test_context_registry_and_daily_admission_hashes(evidence):
    for damage in ('context_hash','source_hash','run_hash','public_hash','policy_hash','DA_hash','day','duplicate_id'):
        bad=deepcopy(evidence);ctx=bad['diagnostic_context'];reg=bad['causal_input_registry']
        if damage=='context_hash':ctx['context_hash']='x'
        if damage=='source_hash':ctx['source_manifest_hash']='x';ctx['context_hash']=digest({k:v for k,v in ctx.items() if k!='context_hash'})
        if damage=='run_hash':reg['run_source_hash']='x'
        if damage=='public_hash':reg['public_context_hash']='x'
        if damage=='policy_hash':reg['forecast_policy_by_date']={'2026-07-15':{'wind':'delayed'}}
        if damage=='DA_hash':ctx['da_selected_inputs_hash']='x';bad['diagnostic_context']=rebuild(ctx)
        if damage=='day':ctx['day_summary']['day_start_utc']='2026-07-16T00:00:00Z';bad['diagnostic_context']=rebuild(ctx)
        if damage=='duplicate_id':bad['id_rounds'].append(deepcopy(bad['id_rounds'][0]))
        assert not post_execution_forecast_diagnostics(bad)['status']['valid'],damage


def test_frame_stage_alias_is_allowed_but_unknown_stage_is_not(evidence):
    good=deepcopy(evidence)
    for frame in good['causal_input_registry']['forecasts'].values():frame['request']['stage']='RT'
    assert post_execution_forecast_diagnostics(good)['status']['valid']
    bad=deepcopy(evidence);next(iter(bad['causal_input_registry']['forecasts'].values()))['request']['stage']='UNKNOWN'
    assert not post_execution_forecast_diagnostics(bad)['status']['valid']

@pytest.mark.parametrize('section',['causal_input_registry','forecasts','selections','context_base','context_source'])
def test_malformed_archive_collection_is_unverifiable(evidence,section):
    bad=deepcopy(evidence)
    if section=='causal_input_registry':bad[section]=[]
    elif section in ('forecasts','selections'):bad['causal_input_registry'][section]=[]
    else:
        key='configuration_base' if section=='context_base' else 'source_manifest'
        bad['diagnostic_context'][key]=[]
        bad['diagnostic_context']['context_hash']=digest({k:v for k,v in bad['diagnostic_context'].items() if k!='context_hash'})
    assert not post_execution_forecast_diagnostics(bad)['status']['valid']


def test_legitimate_non_utc_operating_day():
    from smm.causal_types import ResourceSpec
    from smm.causal_processes import ProcessPlan
    from smm.causal_provider import compile_provider
    start='2026-07-15T07:00:00Z';zone='America/Los_Angeles'
    _,provider=compile_provider(ProcessPlan(start,1,zone,(ResourceSpec('load','load',100.),)))
    def snapshot(selected):
        ev=selected.evidence();ev['day_start_utc']=start
        return {'input_provenance':ev}
    da=provider.select_day(start,0,0,stage='DA',executed_through_period=0)
    rt=[dict(t=t,snapshot=snapshot(provider.select_day(start,t,t,stage='RT',current_interval=True,executed_through_period=t))) for t in range(2)]
    base=dict(date='2026-07-15',dt_h=.25,periods=96,forecast={'model':'causal_physical_v3'},simulation_timezone=zone)
    ev=dict(dt_h=.25,project_timezone=zone,da=snapshot(da),rt_windows=rt,id_rounds=[],
        causal_input_registry=provider.archive(start),diagnostic_context=make_diagnostic_context(base,provider.source_manifest(),provider.day_summary(start),da.content_hash))
    got=post_execution_forecast_diagnostics(ev)
    assert got['status']['valid'] and got['status']['operating_date']=='2026-07-15'
    assert got['actual'].valid_start_utc.iloc[0]=='2026-07-15T07:00:00Z'
