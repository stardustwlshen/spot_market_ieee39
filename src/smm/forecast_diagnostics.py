"""Post-execution input forecast errors. Never used by a solver or a forecaster.

Truth is the executed RT current-interval research input, NOT generation dispatch.
Unexecuted forecast targets are excluded. No complete actual trajectory is read.
"""
from pathlib import Path
import json
import math
from numbers import Integral,Real
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from .causal_types import MODEL,ForecastFrame,PredictionRequest,ResourceSeries,ResourceSpec,utc_seconds,utc_iso,digest,plain
from .natural_inflow import selected_from_evidence

ACTUAL_COLUMNS=['operating_date','resource_id','kind','unit','period','valid_start_utc','valid_end_utc',
                'actual_input','actual_source','selected_inputs_hash']
DETAIL_COLUMNS=['operating_date','stage','window_start','resource_id','kind','unit','issue_time_utc',
                'valid_start_utc','valid_end_utc','period','lead_midpoint_h','lead_bucket','forecast_input',
                'actual_input','error_actual_minus_forecast','absolute_error','squared_error','forecast_id',
                'information_set_id','actual_selected_inputs_hash']
SUMMARY_COLUMNS=['stage','resource_id','kind','unit','lead_bucket','forecast_comparisons','unique_executed_periods',
                 'mae','rmse','bias_actual_minus_forecast','actual_mean_unique_periods','nrmse_by_executed_mean']


def make_diagnostic_context(configuration_base,source_manifest,day_summary,da_selected_inputs_hash,*,
                            origin='native_case_snapshot',source_records=None):
    """Capture explicit frozen inputs; validation happens in the diagnostic.

    For historical archives pass the ORIGINAL saved config, input-source manifest,
    day summary and DA receipt hash, and name/hash those source files. This helper
    never guesses missing metadata and never writes back to the archived run.
    """
    data=dict(schema_version=1,origin=origin,configuration_base=plain(configuration_base),
        source_manifest=plain(source_manifest),day_summary=plain(day_summary),
        da_selected_inputs_hash=da_selected_inputs_hash,source_records=plain(source_records or []))
    data['configuration_hash']=digest(data['configuration_base'])
    data['source_manifest_hash']=digest(data['source_manifest'])
    data['context_hash']=digest(data)
    return data


def _integer(value,label,lower=0,upper=95):
    if isinstance(value,(bool,np.bool_)) or not isinstance(value,Integral) or not lower<=value<=upper:
        raise ValueError(label+' must be a non-boolean integer in range')
    return int(value)


def _step(value,label):
    if isinstance(value,(bool,np.bool_)) or not isinstance(value,Real) or not math.isfinite(value) or value!=.25:
        raise ValueError(label+' must explicitly equal 0.25 hours')


def _context(evidence,registry,external):
    embedded=evidence.get('diagnostic_context')
    if embedded is not None and external is not None and digest(embedded)!=digest(external):
        raise ValueError('explicit context contradicts embedded frozen context')
    ctx=embedded if embedded is not None else external
    if not isinstance(ctx,dict):raise ValueError('frozen diagnostic context is missing; cannot infer grid/date/timezone')
    _integer(ctx['schema_version'],'context schema',1,1)
    if ctx['origin'] not in ('native_case_snapshot','explicit_frozen_archive'):
        raise ValueError('unknown diagnostic context origin')
    unsigned={k:v for k,v in ctx.items() if k!='context_hash'}
    if digest(unsigned)!=ctx['context_hash']:raise ValueError('frozen diagnostic context hash mismatch')
    if not isinstance(ctx['source_records'],list) or any(not isinstance(r,dict) or not isinstance(r.get('file'),str) or not r['file'] or not isinstance(r.get('sha256'),str) or len(r['sha256'])!=64 or any(c not in '0123456789abcdef' for c in r['sha256']) for r in ctx['source_records']):
        raise ValueError('invalid frozen source record references')
    base=ctx['configuration_base'];src=ctx['source_manifest'];day_info=ctx['day_summary']
    if not all(isinstance(x,dict) for x in (base,src,day_info)):
        raise ValueError('frozen configuration/source/day summary must be mappings')
    if digest(base)!=ctx['configuration_hash'] or digest(src)!=ctx['source_manifest_hash']:
        raise ValueError('frozen configuration/source hash mismatch')
    _step(base['dt_h'],'frozen configuration dt_h');_step(evidence['dt_h'],'evidence dt_h')
    _integer(base['periods'],'frozen configuration periods',96,96)
    if base['forecast']['model']!=MODEL or src['model_version']!=MODEL:
        raise ValueError('frozen configuration/source model mismatch')
    _integer(src['grid_minutes'],'source grid minutes',15,15)
    if digest({k:v for k,v in src.items() if k!='provider_fingerprint'})!=src['provider_fingerprint']:
        raise ValueError('source manifest fingerprint mismatch')
    for left,right in (('run_source_hash','actual_realization_hash'),('public_context_hash','public_context_hash')):
        if not registry.get(left) or registry[left]!=src[right]:raise ValueError('registry differs from frozen source '+right)
    resources=tuple(sorted((ResourceSpec(r['resource_id'],r['kind'],r['scale'],r['group_id'],r['public_params'])
        for r in src['public_resources']),key=lambda r:r.resource_id))
    if digest(dict(resources=resources,timezone=src['timezone'],model=MODEL))!=src['public_context_hash']:
        raise ValueError('source public resources/timezone do not match the registered context')
    if digest(registry['forecast_policy_by_date'])!=src['forecast_policy_hash']:
        raise ValueError('registry policy differs from frozen source')
    if 'source_manifest' in registry and digest(registry['source_manifest'])!=ctx['source_manifest_hash']:
        raise ValueError('registry source manifest contradicts frozen context')
    _step(day_info['dt_h'],'day summary dt_h');_integer(day_info['periods'],'day summary periods',96,96)
    if day_info['provider_fingerprint']!=src['provider_fingerprint']:raise ValueError('day summary source mismatch')
    day=utc_seconds(day_info['day_start_utc']);end=day+86400
    if day%900 or utc_seconds(day_info['day_end_utc'])!=end:raise ValueError('frozen day grid is inconsistent')
    source_start,source_end=utc_seconds(src['start_utc']),utc_seconds(src['end_utc'])
    if not source_start<=day<end<=source_end or (day-source_start)%86400:
        raise ValueError('operating day is outside the frozen source bundle')
    zone=ZoneInfo(src['timezone']);local=datetime.fromtimestamp(day,timezone.utc).astimezone(zone)
    next_day=datetime.fromtimestamp(end,timezone.utc).astimezone(zone)
    if (local.hour,local.minute,local.second)!=(0,0,0) or (next_day.hour,next_day.minute,next_day.second)!=(0,0,0) or (next_day.date()-local.date()).days!=1:
        raise ValueError('frozen timezone/day does not describe one 24-hour operating day')
    date=local.date().isoformat()
    if base['date']!=date or day_info['operating_date']!=date:
        raise ValueError('frozen configuration and source operating dates differ')
    if base.get('simulation_timezone',src['timezone'])!=src['timezone']:
        raise ValueError('frozen configuration and source timezones differ')
    if 'project_timezone' in evidence and evidence['project_timezone']!=src['timezone']:
        raise ValueError('evidence timezone differs from frozen source')
    if 'operating_date' in evidence and evidence['operating_date']!=date:
        raise ValueError('evidence operating date differs from frozen source')
    if ctx['origin']=='explicit_frozen_archive' and not ctx['source_records']:
        raise ValueError('historical derived context must identify the original frozen sources')
    return ctx,day,date,src


def _window_metadata(row,stage,window,day,date,issue=None):
    for label in ('stage',):
        if label in row and row[label]!=stage:raise ValueError('window stage contradicts its container')
    if 'issue_t' in row and row['issue_t'] is not None:
        claimed=_integer(row['issue_t'],'window issue_t')
        if issue is not None and claimed!=issue:raise ValueError('window issue_t differs from selected source')
    if 'day_start_utc' in row and utc_seconds(row['day_start_utc'])!=day:raise ValueError('window day mismatch')
    if 'operating_date' in row and row['operating_date']!=date:raise ValueError('window date mismatch')
    if 'dt_h' in row:_step(row['dt_h'],'window dt_h')


def post_execution_forecast_diagnostics(evidence,*,frozen_context=None):
    """Return status plus three DataFrames from a frozen hydro_window_evidence record.

    Missing/contradictory evidence yields unverifiable status and empty tables, not
    invented zero errors. A genuinely executed contiguous debug prefix is valid.
    """
    status=dict(schema_version=2,model=MODEL,valid=False,status='unverifiable',errors=[],
        truth='executed_RT_current_interval_perfect_measurement_of_available_inputs_not_dispatch',
        error_sign='actual_minus_original_issued_forecast',
        scope='selected_issued_targets_intersected_with_executed_prefix_only',
        weighting='equal forecast-target pairs within each stage/resource/lead bucket; overlapping windows repeat comparisons, never executed quantities',
        current_measurement_is_not_RT_prediction=True,lead_reference='interval_midpoint_minus_issue_time',
        normalized_error_denominator='resource mean over unique executed input periods; unavailable when mean is zero')
    empty=lambda:dict(status=status,actual=pd.DataFrame(columns=ACTUAL_COLUMNS),
        detail=pd.DataFrame(columns=DETAIL_COLUMNS),summary=pd.DataFrame(columns=SUMMARY_COLUMNS))
    try:
        registry=evidence['causal_input_registry']
        if not isinstance(registry,dict) or registry.get('model')!=MODEL:
            raise ValueError('missing or wrong-version frozen forecast registry')
        if not isinstance(registry.get('forecasts'),dict) or not isinstance(registry.get('selections'),dict):
            raise ValueError('forecast registry collections must be mappings')
        context,day,date,source_manifest=_context(evidence,registry,frozen_context)
        windows=evidence['rt_windows']
        if not windows:raise ValueError('no executed RT input measurements')
        if [_integer(r['t'],'RT period') for r in windows]!=list(range(len(windows))) or len(windows)>96:
            raise ValueError('executed RT inputs are not a unique contiguous prefix')
        frame_cache={}
        id_rounds=evidence.get('id_rounds',[])
        indices=[_integer(r['tau'],'ID tau') for r in id_rounds]
        if indices!=sorted(set(indices)):raise ValueError('ID window starts must be unique and ordered')
        def source(snapshot):
            ev=snapshot['input_provenance'];selected=selected_from_evidence(ev)
            stored=registry['selections'].get(selected.content_hash)
            if stored is None or selected_from_evidence(stored)!=selected:
                raise ValueError('selected source not independently present in frozen registry')
            fid=selected.forecast_id
            if fid not in frame_cache:
                raw=registry['forecasts'][fid]
                frame=ForecastFrame(PredictionRequest(**raw['request']),tuple(ResourceSeries(**r) for r in raw['series']),
                    raw['information_set_id'],raw['observation_prefix_hash'],raw['public_information_hash'],raw['policy_hash'],
                    raw['model_version'],raw['preset_version'])
                if frame.model_version!=MODEL or frame.content_hash!=fid:raise ValueError('issued forecast content hash/version mismatch')
                frame_cache[fid]=frame
            frame=frame_cache[fid]
            if frame.information_set_id!=selected.information_set_id or frame.request.issue_time_utc!=selected.issue_time_utc:
                raise ValueError('issued/selected information or issue mismatch')
            original=frame.select(selected.start_utc,selected.end_utc)
            if {r.resource_id for r in original.series}!={r.resource_id for r in selected.series}:
                raise ValueError('issued/selected resource mismatch')
            offset=int(selected.sources[0]=='current_interval_perfect_measurement')
            for r in selected.series:
                if not np.array_equal(r.values[offset:],original.values(r.resource_id)[offset:]):
                    raise ValueError('selected future inputs differ from issued forecast')
            return ev,selected,frame
        da_ev,da_selected,_=source(evidence['da'])
        if utc_seconds(da_ev['day_start_utc'])!=day or da_selected.content_hash!=context['da_selected_inputs_hash']:
            raise ValueError('DA source differs from frozen daily admission')
        if utc_seconds(da_selected.start_utc)!=day or utc_seconds(da_selected.end_utc)!=day+86400:
            raise ValueError('DA source does not cover the declared operating day')
        identities={r.resource_id:(r.kind,r.unit) for r in da_selected.series}
        if set(identities)!=set(source_manifest['resource_ids']):raise ValueError('resources differ from frozen bundle')
        actual_rows=[];actual={}
        for row in windows:
            ev,selected,_=source(row['snapshot']);t=_integer(row['t'],'RT period');start=day+t*900
            _window_metadata(row,'RT',t,day,date,t)
            if utc_seconds(ev['day_start_utc'])!=day or utc_seconds(selected.start_utc)!=start or utc_seconds(selected.issue_time_utc)!=start:
                raise ValueError('executed source has wrong day or RT interval')
            if selected.sources[0]!='current_interval_perfect_measurement':
                raise ValueError('executed current input measurement is missing')
            if {r.resource_id:(r.kind,r.unit) for r in selected.series}!=identities:
                raise ValueError('executed resource identities are incomplete')
            for r in selected.series:
                actual[(r.resource_id,t)]=(r.values[0],selected.content_hash)
                actual_rows.append(dict(operating_date=date,resource_id=r.resource_id,kind=r.kind,unit=r.unit,period=t,
                    valid_start_utc=utc_iso(start),valid_end_utc=utc_iso(start+900),actual_input=r.values[0],
                    actual_source=selected.sources[0],selected_inputs_hash=selected.content_hash))
        publication_windows=[('DA',0,evidence['da'])]+[('ID',r['tau'],r['snapshot']) for r in id_rounds]+[('RT',r['t'],r['snapshot']) for r in windows]
        rows=[];seen=set();excluded=0
        for stage,window,snapshot in publication_windows:
            window=_integer(window,stage+' window start')
            ev,selected,frame=source(snapshot)
            if utc_seconds(ev['day_start_utc'])!=day:raise ValueError('publication belongs to a different operating day')
            issue=utc_seconds(frame.request.issue_time_utc)
            if not day<=issue<=day+window*900 or (issue-day)%900:
                raise ValueError('publication issue is inconsistent with its operating window')
            if utc_seconds(selected.start_utc)!=day+window*900 or utc_seconds(selected.end_utc)!=day+86400:
                raise ValueError('window index differs from selected input coverage')
            if stage in ('DA','RT') and issue!=day+window*900:
                raise ValueError('DA/RT issue must equal the declared window start')
            _window_metadata(snapshot,stage,window,day,date,(issue-day)//900)
            _window_metadata(ev,stage,window,day,date,(issue-day)//900)
            if stage=='ID':
                row=next(r for r in id_rounds if r['tau']==window)
                _window_metadata(row,stage,window,day,date,(issue-day)//900)
            if stage!='RT' and any(s!='issued_forecast' for s in selected.sources):raise ValueError('planning stage cannot use current RT measurement')
            key=(stage,window,selected.issue_time_utc)
            if key in seen:raise ValueError('duplicate publication window')
            seen.add(key)
            if window>=len(windows):
                excluded+=1
                continue  # Valid published targets without execution cannot be error samples.
            if {r.resource_id:(r.kind,r.unit) for r in frame.series}!=identities:raise ValueError('forecast resource mismatch')
            issue=utc_seconds(frame.request.issue_time_utc);start=utc_seconds(frame.request.target_start_utc)
            for r in frame.series:
                for i,predicted in enumerate(r.values):
                    valid=start+i*900;t=(valid-day)//900
                    if valid<day or valid>=day+len(windows)*900 or valid<utc_seconds(selected.start_utc) or valid>=utc_seconds(selected.end_utc):continue
                    if valid%900 or valid<issue:raise ValueError('forecast target grid is invalid')
                    truth,actual_hash=actual[(r.resource_id,t)];error=truth-predicted;lead=(valid+450-issue)/3600
                    bucket='0-1h' if lead<1 else '1-4h' if lead<4 else '4-12h' if lead<12 else '12-24h'
                    rows.append(dict(operating_date=date,stage=stage,window_start=window,resource_id=r.resource_id,
                        kind=r.kind,unit=r.unit,issue_time_utc=frame.request.issue_time_utc,valid_start_utc=utc_iso(valid),
                        valid_end_utc=utc_iso(valid+900),period=t,lead_midpoint_h=lead,lead_bucket=bucket,
                        forecast_input=predicted,actual_input=truth,error_actual_minus_forecast=error,
                        absolute_error=abs(error),squared_error=error*error,forecast_id=selected.forecast_id,
                        information_set_id=frame.information_set_id,actual_selected_inputs_hash=actual_hash))
        actual_frame=pd.DataFrame(actual_rows,columns=ACTUAL_COLUMNS);detail=pd.DataFrame(rows,columns=DETAIL_COLUMNS)
        if detail.empty or not np.isfinite(detail[['forecast_input','actual_input','squared_error']].to_numpy()).all():
            raise ValueError('missing or nonfinite forecast comparisons')
        unique_means=actual_frame.groupby('resource_id').actual_input.mean().to_dict();summaries=[]
        def summarize(group,stage,uid,kind,unit,bucket):
            mean=float(unique_means[uid]);rmse=float(np.sqrt(group.squared_error.mean()))
            return dict(stage=stage,resource_id=uid,kind=kind,unit=unit,lead_bucket=bucket,
                forecast_comparisons=len(group),unique_executed_periods=int(group.period.nunique()),
                mae=float(group.absolute_error.mean()),rmse=rmse,bias_actual_minus_forecast=float(group.error_actual_minus_forecast.mean()),
                actual_mean_unique_periods=mean,nrmse_by_executed_mean=rmse/mean if mean>0 else None)
        for (stage,uid,kind,unit,bucket),group in detail.groupby(['stage','resource_id','kind','unit','lead_bucket'],sort=True):
            summaries.append(summarize(group,stage,uid,kind,unit,bucket))
        for (stage,uid,kind,unit),group in detail.groupby(['stage','resource_id','kind','unit'],sort=True):
            summaries.append(summarize(group,stage,uid,kind,unit,'all'))
        status.update(valid=True,status='verified_executed_prefix',operating_date=date,day_start_utc=utc_iso(day),
            executed_periods=len(windows),expected_periods=96,resource_count=len(identities),
            actual_input_rows=len(actual_rows),forecast_comparisons=len(rows),
            full_day=len(windows)==96,publication_windows=len(publication_windows),
            excluded_unexecuted_publication_windows=excluded,context_origin=context['origin'],
            context_hash=context['context_hash'],configuration_hash=context['configuration_hash'],
            source_manifest_hash=context['source_manifest_hash'],source_records=context['source_records'],
            da_selected_inputs_hash=context['da_selected_inputs_hash'])
        return dict(status=status,actual=actual_frame,detail=detail,summary=pd.DataFrame(summaries,columns=SUMMARY_COLUMNS))
    except (ValueError,KeyError,TypeError,IndexError,OverflowError) as exc:
        status['errors']=[str(exc)];return empty()


def write_post_execution_diagnostics(evidence,directory,*,frozen_context=None):
    """Write only to the caller's new result/review directory; never alter inputs."""
    result=post_execution_forecast_diagnostics(evidence,frozen_context=frozen_context);d=Path(directory)
    for key,name in [('actual','executed_input_actual.csv'),('detail','forecast_error_detail.csv'),('summary','forecast_error_summary.csv')]:
        result[key].to_csv(d/name,index=False)
    (d/'forecast_error_status.json').write_text(json.dumps(result['status'],ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return result['status']
