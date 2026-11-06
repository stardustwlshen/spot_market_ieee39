"""Synthetic physical processes. Simulator-private arrays never enter forecaster API.

All assumptions/presets are explicitly uncalibrated. Internal 5 min evolution,
15 min interval means, fixed public 35-day prehistory, no midnight resets.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from collections.abc import Mapping
import hashlib
import math
import numpy as np
from .causal_types import (MODEL, PRESET_VERSION, PRE_ROLL_DAYS, SLOT_SECONDS, SUBSTEP_SECONDS,
    ResourceSpec, ObservationSeries, ObservationPrefix, digest, freeze, plain,
    utc_seconds, utc_iso, grid_slot, immutable_array, delay_seconds)
from .causal_presets import resolve_actual
from .forecast import LOAD_SHAPE


def random_slots(seed, realization_id, role, owner, first_slot, count):
    """Philox4x64 counter is the absolute 5min slot; no draw-order dependence.

    Four raw uint64 words per counter. Fixed 53-bit midpoint mapping and
    Box-Muller using words 0,1; word 2 provides cloud switching uniform.
    """
    key = hashlib.sha256(f'{MODEL}|philox_bm_v1|{seed}|{realization_id}|{role}|{owner}'.encode()).digest()[:16]
    generator = np.random.Philox(key=np.frombuffer(key,dtype='<u8'), counter=int(first_slot))
    raw = generator.random_raw(4*count).reshape(count,4)
    u = ((raw[:,:3] >> np.uint64(11)).astype(float)+.5)/2**53
    return np.sqrt(-2*np.log(u[:,0]))*np.cos(2*np.pi*u[:,1]), u[:,2]


def sigmoid(x):
    return 1./(1.+np.exp(-np.clip(x,-700,700)))


def logit(x):
    x=np.clip(x,1e-4,1.-1e-4)
    return np.log(x/(1.-x))


def local_features(seconds, zone):
    dates = [datetime.fromtimestamp(float(t), timezone.utc).astimezone(ZoneInfo(zone)) for t in np.atleast_1d(seconds)]
    hours = np.array([d.hour+d.minute/60+d.second/3600 for d in dates])
    return dates,hours


def validate_public(resource):
    p=resource.public_params
    allowed={'sunrise','sunset','peak_ratio','gamma','public_prior','weekend_multiplier','weekend_peak_shift_h',
             'holiday_dates','b_max','observation_delay_minutes'}
    if set(p)-allowed:
        raise ValueError(f'unknown public resource parameters: {set(p)-allowed}')
    for key, value in p.items():
        if key=='holiday_dates':
            if any(not isinstance(x,str) for x in value):
                raise ValueError('holiday dates must be explicit ISO dates')
            continue
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError(f'invalid public parameter {key}')
    if not 0 <= p.get('observation_delay_minutes',0) <= 360:
        raise ValueError('observation delay outside supported range')
    if resource.kind=='pv':
        rise,setting=p.get('sunrise',6.5),p.get('sunset',19.5)
        if not (0<=rise<setting<=24 and 6<=setting-rise<=18 and .5<=p.get('peak_ratio',.85)<=1 and .8<=p.get('gamma',1.3)<=2):
            raise ValueError('invalid declared synthetic solar clock')
    if resource.kind in ('wind','pv') and not .05<=p.get('public_prior',.5 if resource.kind=='wind' else .65)<=.99:
        raise ValueError('capacity-factor/cloud public prior is invalid')
    if resource.kind=='load' and not (.1<=p.get('b_max',.35)<=.7 and .5<=p.get('weekend_multiplier',.92)<=1.2 and -2<=p.get('weekend_peak_shift_h',1.)<=2):
        raise ValueError('invalid public load-domain/calendar parameters')
    if resource.kind=='natural_inflow' and p.get('public_prior',resource.scale)<0:
        raise ValueError('natural water public prior cannot be negative')


def solar_envelope(resource, seconds, zone):
    _,hour=local_features(seconds,zone)
    p=resource.public_params
    rise,setting=p.get('sunrise',6.5),p.get('sunset',19.5)
    daylight=(hour>rise)&(hour<setting)
    result=np.zeros(len(hour))
    result[daylight]=resource.scale*p.get('peak_ratio',.85)*np.sin(np.pi*(hour[daylight]-rise)/(setting-rise))**p.get('gamma',1.3)
    return result


def load_baseline(resource, seconds, zone, private_shift=0.):
    dates,hours=local_features(seconds,zone)
    holidays=set(resource.public_params.get('holiday_dates',()))
    is_rest=lambda d:d.weekday()>=5 or d.date().isoformat() in holidays
    rest=np.array([is_rest(d) for d in dates],float)
    prior=np.array([is_rest(d-timedelta(days=1)) for d in dates],float)
    # Public calendar transitions occur only AFTER the boundary; no future-card smoothing.
    blend=np.minimum(hours/2.,1.)
    rest=prior+(rest-prior)*blend
    mult=1+rest*(resource.public_params.get('weekend_multiplier',.92)-1)
    shift=rest*resource.public_params.get('weekend_peak_shift_h',1.)+private_shift
    return resource.scale*mult*np.interp(hours-shift,np.arange(24)+.5,LOAD_SHAPE,period=24)


@dataclass(frozen=True)
class ProcessPlan:
    start_utc: str
    day_count: int
    timezone: str
    resources: tuple[ResourceSpec,...]
    day_specs: Mapping=field(default_factory=dict)  # ISO local date -> resource ID or kind -> actual spec
    root_seed: str='0'
    realization_id: str='0'

    def __post_init__(self):
        if isinstance(self.day_count,bool) or not isinstance(self.day_count,int) or self.day_count<1:
            raise ValueError('day_count must be a positive integer')
        grid_slot(self.start_utc)
        zone=ZoneInfo(self.timezone)
        start=datetime.fromtimestamp(utc_seconds(self.start_utc),timezone.utc).astimezone(zone)
        if (start.hour,start.minute,start.second)!=(0,0,0):
            raise ValueError('process series must start at local operating midnight')
        for k in range(self.day_count):
            a,b=start+timedelta(days=k),start+timedelta(days=k+1)
            if b.timestamp()-a.timestamp()!=86400:
                raise ValueError('23/25h DST operating days are not supported')
        if len({r.resource_id for r in self.resources})!=len(self.resources) or not self.resources:
            raise ValueError('resources must be unique and nonempty')
        if not str(self.root_seed).isdigit():
            raise ValueError('root_seed must be a decimal string')
        for r in self.resources: validate_public(r)
        allowed_dates={(start+timedelta(days=k)).date().isoformat() for k in range(self.day_count)}
        if set(self.day_specs)-allowed_dates:
            raise ValueError('actual card date outside series')
        for date,items in self.day_specs.items():
            if set(items)-({r.resource_id for r in self.resources}|{r.kind for r in self.resources}):
                raise ValueError('actual card has unknown resource/kind')
            for r in self.resources:
                resolve_actual(r.kind,items.get(r.resource_id,items.get(r.kind)))
        object.__setattr__(self,'resources',tuple(sorted(self.resources,key=lambda r:r.resource_id)))
        object.__setattr__(self,'day_specs',freeze(self.day_specs))
        object.__setattr__(self,'root_seed',str(self.root_seed))

    @property
    def end_utc(self): return utc_iso(utc_seconds(self.start_utc)+self.day_count*86400)

    @property
    def content_hash(self): return digest(self)


class ActualStore:
    """Simulator-owned immutable truth, including separately labelled synthetic prehistory."""
    def __init__(self,plan,starts,values,checkpoints,clip_counts):
        self.plan=plan
        self.starts=immutable_array(starts,np.int64)
        self.values=freeze({k:immutable_array(v) for k,v in values.items()})
        self.checkpoints=freeze(checkpoints)
        self.clip_counts=freeze(clip_counts)
        self._observations={r.resource_id:ObservationSeries(r.resource_id,self.starts,self.values[r.resource_id],
            self.starts+SLOT_SECONDS+delay_seconds(r.public_params.get('observation_delay_minutes',0))) for r in plan.resources}

    @property
    def content_hash(self):
        return digest({k:hashlib.sha256(v.tobytes()).hexdigest() for k,v in self.values.items()})

    def actual(self,resource_id,start,end):
        a=grid_slot(start)-self.starts[0]//SLOT_SECONDS
        b=grid_slot(end)-self.starts[0]//SLOT_SECONDS
        if a<0 or b>len(self.starts) or b<=a: raise ValueError('actual interval out of range')
        return self.values[resource_id][a:b].copy()

    def observations(self,issue_time_utc,extra_delay_minutes=None):
        issue=utc_seconds(issue_time_utc)
        delay=extra_delay_minutes or {}
        selected=[]
        for uid,s in sorted(self._observations.items()):
            seconds=delay_seconds(delay.get(uid,0))
            if seconds<0: raise ValueError('cannot advance observation availability')
            mask=s.available_at+seconds<=issue
            selected.append(ObservationSeries(uid,s.starts[mask],s.values[mask],s.available_at[mask]+seconds,s.source))
        return ObservationPrefix(utc_iso(issue),tuple(selected))

    def current_measurement(self,issue_time_utc):
        """Explicit executor-only research exception, not a forecaster method."""
        issue=utc_seconds(issue_time_utc)
        if issue<utc_seconds(self.plan.start_utc): raise ValueError('execution before task start')
        return {r.resource_id:float(self.actual(r.resource_id,issue,issue+SLOT_SECONDS)[0]) for r in self.plan.resources}

    def metadata(self):
        return dict(model=MODEL,preset_version=PRESET_VERSION,calibrated=False,
            plan_hash=self.plan.content_hash,actual_realization_hash=self.content_hash,
            root_seed=self.plan.root_seed,realization_id=self.plan.realization_id,
            rng='philox4x64_absolute_5min_counter_box_muller_v1',numpy_version=np.__version__,
            pre_roll_days=PRE_ROLL_DAYS,prehistory_source='fixed_public_prior_synthetic_history',
            process_plan=plain(self.plan),checkpoints=plain(self.checkpoints),clip_counts=plain(self.clip_counts))


def generate_actual(plan):
    start=utc_seconds(plan.start_utc)-PRE_ROLL_DAYS*86400
    end=utc_seconds(plan.end_utc)
    seconds=np.arange(start,end,SUBSTEP_SECONDS,dtype=np.int64)
    mid=seconds+SUBSTEP_SECONDS/2
    dates,hours=local_features(mid,plan.timezone)
    date_labels=[d.date().isoformat() for d in dates]
    unique_dates=list(dict.fromkeys(date_labels))
    date_index={d:i for i,d in enumerate(unique_dates)}
    codes=np.array([date_index[d] for d in date_labels])
    first=start//SUBSTEP_SECONDS; n=len(seconds); delta=SUBSTEP_SECONDS/3600
    streams={}
    def stream(role,owner):
        key=(role,owner)
        if key not in streams:
            streams[key]=random_slots(plan.root_seed,plan.realization_id,'actual.'+role,owner,first,n)
        return streams[key]
    def correlated(role,r,rho):
        local=stream(role,r.resource_id)[0]
        group=stream(role+'.group',r.group_id or r.resource_id)[0]
        return np.sqrt(rho)*group+np.sqrt(1-rho)*local
    result={}; checkpoints={}; clipped={}
    for r in plan.resources:
        params=[resolve_actual(r.kind,plan.day_specs.get(d,{}).get(r.resource_id,plan.day_specs.get(d,{}).get(r.kind))) for d in unique_dates]
        defaults=resolve_actual(r.kind)
        # Prehistory NEVER uses first/future actual card; only fixed public priors.
        for j,d in enumerate(unique_dates):
            if d not in plan.day_specs: params[j]=resolve_actual(r.kind)
        spec=[params[j] for j in codes]
        out=np.empty(n); history={}; clips=0
        if r.kind=='wind':
            s=float(logit(r.public_params.get('public_prior',.5))); f=0.
            zs=stream('wind.slow',r.resource_id)[0]; zg=stream('wind.slow.group',r.group_id or r.resource_id)[0]
            zf=stream('wind.fast',r.resource_id)[0]; zfg=stream('wind.fast.group',r.group_id or r.resource_id)[0]
            for j,p in enumerate(spec):
                target=p['target']
                if 'target_end' in p:
                    target+=(p['target_end']-target)*min(max((hours[j]-p['event_hour'])/p['duration_h'],0),1)
                a=math.exp(-delta/p['slow_h']); b=math.exp(-delta/p['fast_h'])
                m=math.log(target/(1-target))
                s=m+a*(s-m)+p['slow_sigma']*math.sqrt(1-a*a)*(math.sqrt(p['rho_slow'])*zg[j]+math.sqrt(1-p['rho_slow'])*zs[j])
                f=b*f+p['fast_sigma']*math.sqrt(1-b*b)*(math.sqrt(p['rho_fast'])*zfg[j]+math.sqrt(1-p['rho_fast'])*zf[j])
                out[j]=r.scale/(1+math.exp(-max(-700,min(700,s+f))))
                if (j+1)%288==0: history[utc_iso(seconds[j]+300)]=dict(slow=s,fast=f)
        elif r.kind=='pv':
            c=float(logit(r.public_params.get('public_prior',.65))); q=0
            z=stream('pv.cloud',r.resource_id)[0]; zg=stream('pv.cloud.group',r.group_id or r.resource_id)[0]
            switch=stream('pv.switch',r.group_id or r.resource_id)[1]
            envelope=solar_envelope(r,mid,plan.timezone)
            for j,p in enumerate(spec):
                if p['cloud_mode']=='clear': q=0
                elif p['cloud_mode']=='shaded': q=1
                elif switch[j]<1-math.exp(-delta/p['shaded_h' if q else 'clear_h']): q=1-q
                target=p['shaded_k' if q else 'clear_k']; m=math.log(target/(1-target)); a=math.exp(-delta/p['response_h'])
                c=m+a*(c-m)+p['sigma']*math.sqrt(1-a*a)*(math.sqrt(p['rho'])*zg[j]+math.sqrt(1-p['rho'])*z[j])
                out[j]=envelope[j]/(1+math.exp(-max(-700,min(700,c))))
                if (j+1)%288==0: history[utc_iso(seconds[j]+300)]=dict(cloud_latent=c,cloud_state=q)
        elif r.kind=='load':
            s=f=shift=0.; shifts=np.empty(n); residual=np.empty(n)
            zs=stream('load.slow',r.resource_id)[0]; zf=stream('load.fast',r.resource_id)[0]
            bound=r.public_params.get('b_max',.35)
            for j,p in enumerate(spec):
                if abs(math.log(p['target']))>bound: raise ValueError('private load target exceeds declared public domain')
                a=math.exp(-delta/p['slow_h']); b=math.exp(-delta/p['fast_h']); m=math.log(p['target'])
                s=m+a*(s-m)+p['slow_sigma']*math.sqrt(1-a*a)*zs[j]
                f=b*f+p['fast_sigma']*math.sqrt(1-b*b)*zf[j]
                shift=p['peak_shift_h']+math.exp(-delta/2)*(shift-p['peak_shift_h'])
                residual[j]=max(-bound,min(bound,s+f)); clips+=int(abs(s+f)>bound); shifts[j]=shift
                if (j+1)%288==0: history[utc_iso(seconds[j]+300)]=dict(slow=s,fast=f,peak_shift_h=shift)
            out=load_baseline(r,mid,plan.timezone,shifts)*np.exp(residual)
        else:
            m=1.; x=0.; r1=r2=float(r.public_params.get('public_prior',r.scale))
            z=stream('inflow.forcing',r.resource_id)[0]; zg=stream('inflow.forcing.group',r.group_id or r.resource_id)[0]
            pulse=np.zeros(n)
            # Events persist beyond midnight. They have no effect before their effective time.
            for date,items in plan.day_specs.items():
                p=resolve_actual(r.kind,items.get(r.resource_id,items.get(r.kind)))
                if not p['amplitude']: continue
                midnight=datetime.fromisoformat(date).replace(tzinfo=ZoneInfo(plan.timezone)).timestamp()
                since=(mid-midnight)/3600-p['event_hour']-p['lag_h']
                active=(since>0)&(since<p['duration_h'])
                pulse[active]+=p['amplitude']*np.sin(np.pi*since[active]/p['duration_h'])**2
            if np.max(pulse,initial=0)>4.+1e-12: raise ValueError('overlapping inflow pulse amplitude exceeds supported bound')
            for j,p in enumerate(spec):
                a=math.exp(-delta/p['base_h']); m=p['target']+a*(m-p['target'])
                a=math.exp(-delta/p['noise_h']); x=a*x+math.sqrt(1-a*a)*(math.sqrt(p['rho'])*zg[j]+math.sqrt(1-p['rho'])*z[j])
                u=r.scale*(m+pulse[j])*math.exp(p['noise_amplitude']*math.tanh(x))
                a=math.exp(-delta/(p['rise_h'] if u>=r1 else p['fall_h'])); r1=a*r1+(1-a)*u
                a=math.exp(-delta/p['route_h']); r2=a*r2+(1-a)*r1
                out[j]=r2
                if (j+1)%288==0: history[utc_iso(seconds[j]+300)]=dict(base=m,noise=x,reservoir1_m3s=r1,reservoir2_m3s=r2)
        result[r.resource_id]=out.reshape(-1,3).mean(axis=1)
        checkpoints[r.resource_id]=history; clipped[r.resource_id]=clips
    return ActualStore(plan,seconds[::3],result,checkpoints,clipped)
