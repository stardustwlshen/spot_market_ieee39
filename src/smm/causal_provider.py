"""Runtime adapter for causal_v3. Scheduling and execution state remain caller-owned."""
from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import numpy as np
from .causal_types import (MODEL, PRE_ROLL_DAYS, SLOT_SECONDS, ResourceSpec, PredictionRequest,
                          PublicEvent, utc_seconds, utc_iso, digest, plain, freeze)
from .causal_forecast import CausalForecaster
from .causal_processes import ProcessPlan, ActualStore, generate_actual, solar_envelope
from .causal_presets import POLICIES

CONFIG_LOAD_REFERENCE_MW=6254.23


def resources_from_config(cfg):
    if cfg.dt!=.25 or cfg.T!=96:
        raise ValueError('causal_physical_v3 currently requires 96 x 15min operating days')
    result=[ResourceSpec('load','load',CONFIG_LOAD_REFERENCE_MW*float(cfg.base.get('load_scale',1.)))]
    for r in cfg.units['renewable']:
        if r['type'] not in ('wind','pv'): raise ValueError('unsupported renewable kind')
        result.append(ResourceSpec(r['id'],r['type'],float(r['cap']),r['type']+'_research_group'))
    for h in cfg.units['hydro']:
        qref=float(h['seasons'][cfg.base['season']]['qin'])
        if qref<=0: raise ValueError(f"{h['id']}: v3 needs explicit positive natural inflow reference; use legacy constant-zero or declare a research scale")
        result.append(ResourceSpec(h['id'],'natural_inflow',qref,'catchment_research_group'))
    return tuple(result)


class CausalInputProvider:
    """Exact source-bound input service; no forecast_view/actual-minus-error fallback.

    `observation_source(issue, delays)` must return only legally available history.
    `execution_source(issue)` is a separate explicit current-interval research
    capability. The forecaster never receives that capability.
    """
    model_version=MODEL
    def __init__(self,forecaster,observation_source,*,execution_source=None,forecast_policy_by_date=None,
                 run_source_hash=None,start_utc=None,end_utc=None):
        self.forecaster=forecaster
        self._observations=observation_source
        self._execution=execution_source
        self.forecast_policy_by_date=freeze({d:dict(p) for d,p in (forecast_policy_by_date or {}).items()})
        allowed={r.resource_id for r in forecaster.resources}|{r.kind for r in forecaster.resources}
        for date,policies in self.forecast_policy_by_date.items():
            datetime.fromisoformat(date)
            if set(policies)-allowed or any(v not in POLICIES for v in policies.values()):
                raise ValueError('invalid date/resource forecast policy')
        self.run_source_hash=run_source_hash
        self.start_utc=start_utc; self.end_utc=end_utc
        self._frames={}; self._selections={}

    def __deepcopy__(self,memo):
        # Solver clones share the read-only source service; they never issue forecasts.
        memo[id(self)]=self
        return self

    def request(self,request):
        prefix=self._observations(request.issue_time_utc,self.forecaster.extra_delays(request))
        frame=self.forecaster.issue(request,prefix)
        self._frames[frame.forecast_id]=frame
        return frame

    def select_day(self,day_start_utc,issue_period,start_period,*,stage,end_period=96,
                   current_interval=False,executed_through_period=None):
        day=utc_seconds(day_start_utc)
        if self.start_utc is None or self.end_utc is None or not utc_seconds(self.start_utc)<=day<utc_seconds(self.end_utc) or (day-utc_seconds(self.start_utc))%86400:
            raise ValueError('operating day is outside the frozen process bundle')
        if not all(isinstance(x,int) and not isinstance(x,bool) for x in (issue_period,start_period,end_period)):
            raise ValueError('period indices must be integers')
        if not 0<=issue_period<=start_period<end_period<=96:
            raise ValueError('invalid day input range')
        if executed_through_period is None or not issue_period<=executed_through_period<=96:
            raise ValueError('runtime must declare the completed execution boundary')
        issue=utc_iso(day+issue_period*SLOT_SECONDS)
        date=datetime.fromtimestamp(utc_seconds(issue),timezone.utc).astimezone(ZoneInfo(self.forecaster.timezone)).date().isoformat()
        policies=self.forecast_policy_by_date.get(date,{})
        request=PredictionRequest(issue,issue,utc_iso(day+96*SLOT_SECONDS),stage,
                                  policy_overrides=policies)
        frame=self.request(request)
        selected=frame.select(utc_iso(day+start_period*SLOT_SECONDS),utc_iso(day+end_period*SLOT_SECONDS))
        if current_interval:
            if stage!='RT' or start_period!=issue_period or executed_through_period!=issue_period or self._execution is None:
                raise ValueError('current interval research observation requires exact RT execution boundary')
            selected=selected.with_current_measurement(self._execution(issue))
            for r in self.forecaster.resources:
                if r.kind in ('wind','pv') and selected.values(r.resource_id)[0]>r.scale+1e-7:
                    raise ValueError('current observed renewable exceeds capacity')
                if r.kind=='pv':
                    clear=float(solar_envelope(r,np.array([utc_seconds(issue)+150,utc_seconds(issue)+450,utc_seconds(issue)+750]),self.forecaster.timezone).mean())
                    if selected.values(r.resource_id)[0]>clear+1e-7: raise ValueError('current PV observation exceeds declared solar envelope')
        self._selections[selected.content_hash]=selected
        return selected

    def source_manifest(self):
        result=dict(model_version=MODEL,preset_version='physical_research_20261106_v1',
            grid_minutes=15,start_utc=self.start_utc,end_utc=self.end_utc,
            timezone=self.forecaster.timezone,root_seed=self.forecaster.root_seed,
            realization_id=self.forecaster.realization_id,actual_realization_hash=self.run_source_hash,
            public_context_hash=self.forecaster.public_context_hash,
            public_events_hash=digest(self.forecaster.public_events),
            forecast_policy_hash=digest(self.forecast_policy_by_date),
            resource_ids=[r.resource_id for r in self.forecaster.resources],
            public_load_scale_contract=dict(config_adapter_reference_mw=CONFIG_LOAD_REFERENCE_MW,
                config_adapter_formula='6254.23 MW times config.base.load_scale',
                network_load_role='nodal_distribution_weights_only',
                actual_public_scales_mw={r.resource_id:r.scale for r in self.forecaster.resources if r.kind=='load'}),
            public_resources=[dict(resource_id=r.resource_id,kind=r.kind,scale=r.scale,
                group_id=r.group_id,public_params=plain(r.public_params),
                scale_source='explicit_public_resource_spec')
                for r in self.forecaster.resources])
        result['provider_fingerprint']=digest(result)
        return result

    def day_summary(self,day_start_utc):
        day=utc_seconds(day_start_utc)
        if self.start_utc is None or self.end_utc is None or not utc_seconds(self.start_utc)<=day<utc_seconds(self.end_utc) or (day-utc_seconds(self.start_utc))%86400:
            raise ValueError('day is outside frozen series')
        date=datetime.fromtimestamp(day,timezone.utc).astimezone(ZoneInfo(self.forecaster.timezone)).date().isoformat()
        return dict(day_start_utc=utc_iso(day),day_end_utc=utc_iso(day+86400),operating_date=date,
            periods=96,dt_h=.25,forecast_policy=plain(self.forecast_policy_by_date.get(date,{})),
            provider_fingerprint=self.source_manifest()['provider_fingerprint'])

    def selection(self,content_hash):
        if content_hash not in self._selections: raise ValueError('selected input not found in source registry')
        return self._selections[content_hash]

    def archive(self,day_start_utc=None):
        frames=self._frames; selections=self._selections
        if day_start_utc is not None:
            day=utc_seconds(day_start_utc)
            selections={k:s for k,s in selections.items() if day<=utc_seconds(s.start_utc)<utc_seconds(s.end_utc)<=day+86400}
            ids={s.forecast_id for s in selections.values()}
            frames={k:f for k,f in frames.items() if k in ids}
        return dict(model=MODEL,run_source_hash=self.run_source_hash,source_manifest=self.source_manifest(),
            public_context_hash=self.forecaster.public_context_hash,
            forecast_policy_by_date=plain(self.forecast_policy_by_date),
            forecasts={k:f.to_dict() for k,f in sorted(frames.items())},
            selections={k:s.evidence() for k,s in sorted(selections.items())})


def compile_provider(plan,*,forecast_policy_by_date=None,public_events=()):
    actual=generate_actual(plan)
    f=CausalForecaster(plan.resources,plan.timezone,plan.root_seed,plan.realization_id,
        history_start_utc=utc_seconds(plan.start_utc)-PRE_ROLL_DAYS*86400,public_events=public_events)
    provider=CausalInputProvider(f,actual.observations,execution_source=actual.current_measurement,
        forecast_policy_by_date=forecast_policy_by_date,run_source_hash=actual.content_hash,start_utc=plan.start_utc,end_utc=plan.end_utc)
    return actual,provider


def save_bundle(directory,actual,*,forecast_policy_by_date=None,public_events=()):
    """Create a new immutable source bundle. Never overwrite an existing run."""
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=False)
    with (directory/'actual.npz').open('xb') as out:
        np.savez_compressed(out,starts=actual.starts,**{r.resource_id:actual.values[r.resource_id] for r in actual.plan.resources})
    manifest=actual.metadata()
    manifest.update(forecast_policy_by_date=plain(forecast_policy_by_date or {}),public_events=plain(public_events))
    manifest['bundle_content_hash']=digest(manifest)
    (directory/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,allow_nan=False),encoding='utf-8')
    return manifest['bundle_content_hash']


def load_bundle(directory):
    directory=Path(directory)
    raw=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    supplied=raw.pop('bundle_content_hash')
    if digest(raw)!=supplied or raw['model']!=MODEL: raise ValueError('invalid source manifest hash/version')
    fields=dict(raw['process_plan']); fields['resources']=tuple(ResourceSpec(**r) for r in fields['resources'])
    plan=ProcessPlan(**fields)
    with np.load(directory/'actual.npz',allow_pickle=False) as data:
        if set(data.files)!={'starts'}|{r.resource_id for r in plan.resources}: raise ValueError('bundle resource mismatch')
        starts=data['starts']; expected=np.arange(utc_seconds(plan.start_utc)-PRE_ROLL_DAYS*86400,utc_seconds(plan.end_utc),900)
        if not np.array_equal(starts,expected): raise ValueError('bundle absolute time coverage mismatch')
        values={r.resource_id:data[r.resource_id] for r in plan.resources}
        if any(len(v)!=len(starts) for v in values.values()): raise ValueError('bundle incomplete trajectory')
        actual=ActualStore(plan,starts,values,raw['checkpoints'],raw['clip_counts'])
    if actual.content_hash!=raw['actual_realization_hash'] or plan.content_hash!=raw['plan_hash']:
        raise ValueError('bundle actual/plan hash mismatch')
    f=CausalForecaster(plan.resources,plan.timezone,plan.root_seed,plan.realization_id,
        history_start_utc=utc_seconds(plan.start_utc)-PRE_ROLL_DAYS*86400,
        public_events=tuple(PublicEvent(**e) for e in raw['public_events']))
    return actual,CausalInputProvider(f,actual.observations,execution_source=actual.current_measurement,
        forecast_policy_by_date=raw['forecast_policy_by_date'],run_source_hash=actual.content_hash,start_utc=plan.start_utc,end_utc=plan.end_utc)


def declare_bid_availability(bids,frame):
    """Explicit template action using only an issued forecast, never future actual."""
    from copy import deepcopy
    result=deepcopy(bids)
    for s in frame.series:
        if s.kind not in ('wind','pv'): continue
        row=result['units'][s.resource_id]
        row['avail_pmax']=list(s.values)
        row['availability_provenance']=dict(kind='issued_forecast',forecast_id=frame.forecast_id,
            issue_time_utc=frame.request.issue_time_utc,information_set_id=frame.information_set_id)
    return result


def seasonal_inflow_spec(reference_resource,day_qin,spec='inflow_steady'):
    """Expand a private daily seasonal baseline relative to one fixed public Qref.

    Does not change ResourceSpec.scale/public prior or reset hydrological state.
    Out-of-domain combined pulse/level is rejected rather than clipped.
    """
    import math
    from .causal_presets import resolve_actual
    if reference_resource.kind!='natural_inflow' or isinstance(day_qin,bool) or not math.isfinite(day_qin) or day_qin<0:
        raise ValueError('seasonal inflow needs a natural-water reference and nonnegative qin')
    preset=spec if isinstance(spec,str) else spec.get('preset','inflow_steady')
    parameters=resolve_actual('natural_inflow',spec)
    ratio=float(day_qin)/reference_resource.scale
    parameters['target']*=ratio;parameters['amplitude']*=ratio
    expanded=dict(preset=preset,parameters=parameters)
    resolve_actual('natural_inflow',expanded)
    return expanded
