"""Predeclared 64-seed x 7-day physical/causal point-forecast research audit.

No fit to data, calibrated occurrence probability or stochastic optimization.
Fixed seeds and scenarios; failures are reported, never resampled.
"""
from pathlib import Path
import sys,json,time,hashlib,resource
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from smm.config import load_config
from smm.causal_types import *
from smm.causal_processes import ProcessPlan,solar_envelope
from smm.causal_provider import compile_provider,resources_from_config
from smm.causal_forecast import CausalForecaster

START='2026-07-15T00:00:00Z'; DAYS=7; SEEDS=tuple(range(64)); HOURS=(0,6,12,18); POLICIES=('baseline','responsive')
wind=('wind_normal','wind_low','wind_high','wind_ramp','wind_volatile','wind_normal','wind_low')
pv=('pv_clear','pv_broken_cloud','pv_overcast','pv_fast_cloud','pv_broken_cloud','pv_clear','pv_overcast')
load=('load_normal','load_low','load_high','load_peak_shift','load_volatile','load_normal','load_high')
water=('inflow_steady','inflow_rising','inflow_recession','inflow_dry','inflow_pulse','inflow_recession','inflow_steady')
base=utc_seconds(START);cfg=load_config();resources=resources_from_config(cfg)
dates=[utc_iso(base+d*86400)[:10] for d in range(DAYS)]
cards={date:dict(wind=wind[d],pv=pv[d],load=load[d],natural_inflow=water[d]) for d,date in enumerate(dates)}
rows=[];process=[];volumes=[]; started=time.perf_counter()
source={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__).resolve().parents[1]/'src/smm').glob('causal_*.py')}
for seed in SEEDS:
    actual,_=compile_provider(ProcessPlan(START,DAYS,'UTC',resources,cards,root_seed=str(seed)))
    for r in resources:
        x=actual.actual(r.resource_id,START,actual.plan.end_utc)
        acf={str(lag):float(np.corrcoef(x[:-lag],x[lag:])[0,1]) for lag in (1,4,16,96) if np.std(x)>0}
        process.append(dict(seed=seed,resource=r.resource_id,kind=r.kind,minimum=float(x.min()),maximum=float(x.max()),
            ramp_abs_p95=float(np.quantile(abs(np.diff(x)),.95)),ramp_abs_max=float(max(abs(np.diff(x)))),acf=acf))
    winds=[r.resource_id for r in resources if r.kind=='wind']
    if len(winds)>=2:
        process.append(dict(seed=seed,kind='wind_pair_correlation',resources=winds[:2],
                            correlation=float(np.corrcoef(*(actual.actual(k,START,actual.plan.end_utc) for k in winds[:2]))[0,1])))
    for policy in POLICIES:
        forecaster=CausalForecaster(resources,'UTC',str(seed),'0',history_start_utc=base-PRE_ROLL_DAYS*86400)
        errors={r.resource_id:{b:[] for b in ('0-1h','1-4h','4-12h','12-24h')} for r in resources}
        for d in range(DAYS):
            for hour in HOURS:
                issue=base+d*86400+hour*3600;end=base+(d+1)*86400
                request=PredictionRequest(utc_iso(issue),utc_iso(issue),utc_iso(end),'DA',policy)
                frame=forecaster.issue(request,actual.observations(request.issue_time_utc,forecaster.extra_delays(request)))
                starts=np.arange(issue,end,900);lead=(starts+450-issue)/3600
                for r in resources:
                    prediction=np.asarray(next(s.values for s in frame.series if s.resource_id==r.resource_id))
                    truth=actual.actual(r.resource_id,issue,end);error=truth-prediction
                    visible=np.ones(len(error),bool)
                    if r.kind=='pv':
                        clear=solar_envelope(r,(starts[:,None]+[150,450,750]).ravel(),'UTC').reshape(-1,3).mean(axis=1)
                        visible=clear>0
                        if not (prediction[~visible]==0).all(): raise AssertionError('PV night forecast not zero')
                    if r.kind in ('pv','wind') and (prediction>r.scale+1e-8).any(): raise AssertionError('capacity violation')
                    if r.kind=='natural_inflow': volumes.append(dict(seed=seed,policy=policy,resource=r.resource_id,issue_time=request.issue_time_utc,
                        forecast_horizon_h=(end-issue)/3600,volume_error_m3=float(error.sum()*900)))
                    for label,lo,hi in (('0-1h',0,1),('1-4h',1,4),('4-12h',4,12),('12-24h',12,24)):
                        errors[r.resource_id][label].extend(error[(lead>=lo)&(lead<hi)&visible].tolist())
        for r in resources:
            for label,values in errors[r.resource_id].items():
                e=np.asarray(values)
                if len(e): rows.append(dict(seed=seed,policy=policy,resource=r.resource_id,kind=r.kind,lead_bucket=label,
                    count=len(e),mae=float(abs(e).mean()),rmse=float(np.sqrt(np.mean(e**2))),bias=float(e.mean()),
                    nrmse=float(np.sqrt(np.mean(e**2))/r.scale),normalizer=r.scale,
                    absolute_error_p50=float(np.quantile(abs(e),.5)),absolute_error_p90=float(np.quantile(abs(e),.9)),
                    absolute_error_p99=float(np.quantile(abs(e),.99))))
    print(f'seed {seed} done, elapsed {time.perf_counter()-started:.1f}s',flush=True)
summary=[]
for kind in ('wind','pv','load','natural_inflow'):
    for bucket in ('0-1h','1-4h','4-12h','12-24h'):
        item=dict(kind=kind,lead_bucket=bucket)
        for policy in POLICIES:
            selected=[r for r in rows if r['kind']==kind and r['lead_bucket']==bucket and r['policy']==policy]
            if selected:
                item[policy]=dict(mean_seed_resource_nrmse=float(np.mean([r['nrmse'] for r in selected])),
                    mean_seed_resource_mae=float(np.mean([r['mae'] for r in selected])),
                    mean_seed_resource_bias=float(np.mean([r['bias'] for r in selected])))
        if all(p in item for p in POLICIES): item['responsive_improves_mean_nrmse']=item['responsive']['mean_seed_resource_nrmse']<item['baseline']['mean_seed_resource_nrmse']
        summary.append(item)
report=dict(model=MODEL,source_sha256=source,assumptions='synthetic_uncalibrated_no_occurrence_probabilities',
    plan=dict(seeds=list(SEEDS),days=DAYS,issues_local_hours=HOURS,policies=POLICIES,actual_cards=cards),
    scope='point_forecast_research;not_solver_or_joint_multiday_acceptance',error_sign='actual_minus_forecast',
    aggregation='equal seed-resource mean; PV daylight only; overlapping forecast errors are not executed quantities',
    publication_samples=len(SEEDS)*DAYS*len(HOURS)*len(POLICIES),
    elapsed_seconds=time.perf_counter()-started,peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
    summary=summary,metrics=rows,process_metrics=process,natural_volume_forecast_errors=volumes)
output=Path(sys.argv[1] if len(sys.argv)>1 else 'causal_v3_statistics.json')
output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
print(json.dumps(dict(output=str(output),elapsed_seconds=report['elapsed_seconds'],summary=summary),ensure_ascii=False,indent=2))
