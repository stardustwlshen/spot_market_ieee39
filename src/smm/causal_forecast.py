"""Causal point forecasts from an availability-filtered observation prefix.

This module receives no ActualStore, private day cards or real process states.
Persistent filter states remain at observation effective times; publication uses
copies. Out-of-order/late records replay only the currently available prefix.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
from .causal_types import (MODEL, SLOT_SECONDS, SUBSTEP_SECONDS, PRE_ROLL_DAYS,
    ResourceSeries, ForecastFrame, ObservationPrefix, PredictionRequest,
    utc_seconds, utc_iso, digest, plain, delay_seconds)
from .causal_presets import POLICIES, FORECAST
from .causal_processes import random_slots, logit, sigmoid, solar_envelope, load_baseline, validate_public


@dataclass(frozen=True)
class FilterState:
    effective_at: float
    level: float
    trend: float


def propagate(state, at, baseline, parameters):
    delta=(at-state.effective_at)/3600
    if delta < -1e-12:
        raise ValueError('filter cannot assimilate backwards; replay available prefix')
    tm,tt=parameters['mean_h'],parameters['trend_h']
    a,c=math.exp(-delta/tm),math.exp(-delta/tt)
    j=delta*a if tm==tt else (c-a)/(1/tm-1/tt)
    return FilterState(at,baseline+a*(state.level-baseline)+j*state.trend,c*state.trend)


class CausalForecaster:
    def __init__(self,resources,timezone,root_seed='0',realization_id='0',*,history_start_utc,
                 public_events=()):
        self.resources=tuple(sorted(resources,key=lambda r:r.resource_id))
        if len({r.resource_id for r in self.resources})!=len(self.resources):
            raise ValueError('duplicate forecast resources')
        for r in self.resources: validate_public(r)
        self.timezone=timezone; self.root_seed=str(root_seed); self.realization_id=str(realization_id)
        self.history_start=utc_seconds(history_start_utc)
        if self.history_start%SUBSTEP_SECONDS: raise ValueError('history clock must align to substeps')
        self.public_events=tuple(public_events)
        ids={r.resource_id:r for r in self.resources}
        if len({e.event_id for e in public_events})!=len(self.public_events):
            raise ValueError('public event IDs must be unique')
        for e in self.public_events:
            if e.resource_id not in ids: raise ValueError('public event refers to unknown resource')
            r=ids[e.resource_id]
            if r.kind in ('wind','pv') and not 0<e.baseline_value<1:
                raise ValueError('public wind/cloud baseline must be a fraction within (0,1)')
            if r.kind=='load' and e.baseline_value<=0: raise ValueError('public load ratio must be positive')
        self.public_context_hash=digest(dict(resources=self.resources,timezone=timezone,model=MODEL))
        self._observer_cache={}; self._bias_cache={}; self._published={}
        self.stats=dict(assimilated_observations=0,replayed_observations=0,cache_extensions=0)

    def extra_delays(self,policy_id):
        kind_index={'wind':0,'pv':1,'load':2,'natural_inflow':3}
        policies={r.resource_id: policy_id.policy_for(r) if isinstance(policy_id,PredictionRequest) else policy_id for r in self.resources}
        if any(p not in POLICIES for p in policies.values()): raise ValueError('unknown forecast policy')
        return {r.resource_id:POLICIES[policies[r.resource_id]]['delay_minutes'][kind_index[r.kind]] for r in self.resources}

    def _events(self,issue):
        return tuple(e for e in self.public_events if utc_seconds(e.released_at)<=issue)

    def _baseline(self,r,at,events):
        p=r.public_params
        value=p.get('public_prior',.5 if r.kind=='wind' else .65 if r.kind=='pv' else 1. if r.kind=='load' else r.scale)
        selected=[e for e in events if e.resource_id==r.resource_id and utc_seconds(e.valid_start)<=at<utc_seconds(e.valid_end)]
        if selected:
            # Unique deterministic latest publication; equal release conflicts are rejected.
            selected.sort(key=lambda e:(utc_seconds(e.released_at),e.event_id))
            if len(selected)>1 and selected[-1].released_at==selected[-2].released_at:
                raise ValueError('ambiguous overlapping public forecasts at the same release time')
            value=selected[-1].baseline_value
        if r.kind in ('wind','pv'): return float(logit(value))
        if r.kind=='load': return math.log(value)
        return math.log1p(value/r.scale)

    def _advance_public(self,r,state,at,events,parameters):
        """Exact propagation through known piecewise-constant public baselines.

        An event entirely inside a gap between observations must still influence
        the latent level. Evaluate each segment internally, not at its right edge.
        """
        if at<state.effective_at:
            raise ValueError('cannot advance public baseline backwards')
        relevant=[e for e in events if e.resource_id==r.resource_id]
        if not relevant:
            return propagate(state,at,self._baseline(r,at,events),parameters)
        cuts=sorted({utc_seconds(x) for e in relevant for x in (e.valid_start,e.valid_end)
                     if state.effective_at<utc_seconds(x)<at})+[at]
        for end in cuts:
            inside=(state.effective_at+end)/2
            state=propagate(state,end,self._baseline(r,inside,events),parameters)
        return state

    def _transform(self,r,series):
        mid=series.starts+SLOT_SECONDS/2
        if r.kind=='wind':
            if (series.values>r.scale+1e-8).any(): raise ValueError('wind observation exceeds capacity')
            return mid,logit(series.values/r.scale)
        if r.kind=='pv':
            # Use the same declared discrete interval-average envelope as actual.
            sub=series.starts[:,None]+np.array([150,450,750])[None,:]
            clear=solar_envelope(r,sub.ravel(),self.timezone).reshape(-1,3).mean(axis=1)
            if (series.values>clear+1e-7).any(): raise ValueError('PV observation exceeds declared clear-sky envelope')
            mask=clear>.02*r.scale*r.public_params.get('peak_ratio',.85)
            return mid[mask],logit(series.values[mask]/clear[mask])
        if r.kind=='load':
            if (series.values<=0).any(): raise ValueError('v3 system load observations require positive values')
            sub=series.starts[:,None]+np.array([150,450,750])[None,:]
            base=load_baseline(r,sub.ravel(),self.timezone).reshape(-1,3).mean(axis=1)
            return mid,np.log(series.values/base)
        return mid,np.log1p(series.values/r.scale)

    def _state(self,r,series,policy_id,events):
        times,values=self._transform(r,series)
        p,fp=POLICIES[policy_id],FORECAST[r.kind]
        key=(r.resource_id,policy_id,digest(events))
        prior=self._observer_cache.get(key)
        consumed=0
        if prior is not None:
            old_times,old_values,state=prior
            n=len(old_times)
            if n<=len(times) and np.array_equal(old_times,times[:n]) and np.array_equal(old_values,values[:n]):
                consumed=n; self.stats['cache_extensions']+=1
            else:
                state=FilterState(self.history_start,self._baseline(r,self.history_start,events),0.)
                self.stats['replayed_observations']+=len(times)
        else:
            state=FilterState(self.history_start,self._baseline(r,self.history_start,events),0.)
        for at,z in zip(times[consumed:],values[consumed:]):
            if at<self.history_start: raise ValueError('observation precedes declared filter history')
            delta=(float(at)-state.effective_at)/3600
            if delta<=0: raise ValueError('duplicate/out-of-order effective observations')
            minus=self._advance_public(r,state,float(at),events,fp)
            residual=float(z)-minus.level
            state=FilterState(float(at),minus.level+p['level']*residual,
                float(np.clip(minus.trend+p['trend']*residual/delta,-fp['max_trend'],fp['max_trend'])))
        self.stats['assimilated_observations']+=len(times)-consumed
        # Publication NEVER changes this persistent observation-effective time.
        self._observer_cache[key]=(times.copy(),values.copy(),state)
        return state

    def _bias(self,r,issue):
        first=self.history_start//SUBSTEP_SECONDS
        last=issue//SUBSTEP_SECONDS
        if last<first: raise ValueError('issue precedes public fixed history start')
        count=last-first+1
        old=self._bias_cache.get(r.resource_id)
        if old is None or len(old)<count:
            offset=0 if old is None else len(old)
            z,_=random_slots(self.root_seed,self.realization_id,'forecast.analysis_bias',r.resource_id,first+offset,count-offset)
            a=math.exp(-(SUBSTEP_SECONDS/3600)/FORECAST[r.kind]['bias_tau_h'])
            state=0. if old is None else float(old[-1]); appended=np.empty(len(z))
            for k,innovation in enumerate(z):
                state=a*state+math.sqrt(1-a*a)*innovation; appended[k]=state
            old=appended if old is None else np.r_[old,appended]
            self._bias_cache[r.resource_id]=old
        return float(old[count-1])

    def issue(self,request:PredictionRequest,prefix:ObservationPrefix):
        if request.policy_id not in POLICIES or any(request.policy_for(r) not in POLICIES for r in self.resources): raise ValueError('unknown forecast policy')
        if set(dict(request.policy_overrides))-({r.resource_id for r in self.resources}|{r.kind for r in self.resources}): raise ValueError('unknown policy override resource')
        issue=utc_seconds(request.issue_time_utc)
        if utc_seconds(prefix.issue_time_utc)!=issue: raise ValueError('prefix belongs to another issue time')
        observations={s.resource_id:s for s in prefix.series}
        if set(observations)!={r.resource_id for r in self.resources}:
            raise ValueError('observation prefix must cover exact resource identities')
        # The caller must have applied the policy latency, in addition to true availability.
        delays=self.extra_delays(request)
        for r in self.resources:
            s=observations[r.resource_id]
            earliest=s.starts+SLOT_SECONDS+delay_seconds(r.public_params.get('observation_delay_minutes',0))+delay_seconds(delays[r.resource_id])
            if (s.available_at<earliest).any() or (s.available_at>issue).any():
                raise ValueError('prefix violates declared observation delay')
        events=self._events(issue)
        public_hash=digest(dict(context=self.public_context_hash,events=events))
        policy_hash=digest(dict(policies={r.resource_id:dict(id=request.policy_for(r),parameters=POLICIES[request.policy_for(r)]) for r in self.resources},parameters=FORECAST))
        info=digest(dict(observations=prefix.content_hash,public=public_hash,policy=policy_hash,
                         seed=self.root_seed,realization=self.realization_id,history_start=self.history_start))
        a,b=utc_seconds(request.target_start_utc),utc_seconds(request.target_end_utc)
        times=np.arange(a,b,SUBSTEP_SECONDS)+SUBSTEP_SECONDS/2
        result=[]
        for r in self.resources:
            fp=FORECAST[r.kind]; policy_id=request.policy_for(r); p=POLICIES[policy_id]
            state=self._state(r,observations[r.resource_id],policy_id,events)
            # Advance only a disposable copy. Small steps also honour changing public baselines.
            at=state.effective_at
            while at+SUBSTEP_SECONDS<issue:
                at+=SUBSTEP_SECONDS
                state=self._advance_public(r,state,at,events,fp)
            state=self._advance_public(r,state,float(issue),events,fp)
            error=fp['sigma']*(p['beta_multiplier']+p['sigma_multiplier']*self._bias(r,issue))
            estimates=[]
            for target in times:
                at=state.effective_at
                while at+SUBSTEP_SECONDS<target:
                    at+=SUBSTEP_SECONDS; state=self._advance_public(r,state,at,events,fp)
                state=self._advance_public(r,state,float(target),events,fp)
                estimates.append(state.level+(1-math.exp(-(target-issue)/3600/fp['bias_h']))*error)
            z=np.array(estimates)
            if r.kind=='wind': out=r.scale*sigmoid(z)
            elif r.kind=='pv': out=solar_envelope(r,times,self.timezone)*sigmoid(z)
            elif r.kind=='load': out=load_baseline(r,times,self.timezone)*np.exp(np.clip(z,-r.public_params.get('b_max',.35),r.public_params.get('b_max',.35)))
            else: out=r.scale*np.maximum(np.expm1(z),0)
            result.append(ResourceSeries(r.resource_id,r.kind,r.variable,r.unit,tuple(out.reshape(-1,3).mean(axis=1))))
        frame=ForecastFrame(request,tuple(result),info,prefix.content_hash,public_hash,policy_hash)
        key=digest(dict(request=plain(request),information_set_id=info))
        previous=self._published.get(key)
        if previous is not None and previous.content_hash!=frame.content_hash:
            raise RuntimeError('immutable forecast publication conflict')
        self._published[key]=frame
        return frame

    def filter_state_evidence(self):
        return {str(k):dict(effective_at=utc_iso(int(v[2].effective_at)),level=v[2].level,trend=v[2].trend)
                for k,v in self._observer_cache.items()}
