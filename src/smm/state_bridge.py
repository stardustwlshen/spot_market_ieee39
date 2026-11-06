"""Full-precision actual state bridges. No forecasts or rounded CSV recovery.

A bridge keeps daily arrays indexed from zero and carries explicit, absolute
pre-day history separately. Only hydro energy and pumped start *day budgets*
reset at a business-day boundary. A missing value is never inferred as zero.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import asdict, fields
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import numpy as np

SCHEMA = 1


def plain(value):
    """Strict JSON numerical representation, retaining Python float precision."""
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ValueError('State JSON keys must be strings')
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Non-finite state/checkpoint value')
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError(f'Unsupported state type {type(value).__name__}')


def digest(value):
    return hashlib.sha256(json.dumps(plain(value), sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def day_start(cfg):
    raw = str(cfg.base['date'])
    if len(raw) != 10:
        raise ValueError('Business date must be YYYY-MM-DD')
    return datetime.fromisoformat(raw).replace(tzinfo=ZoneInfo(cfg.base.get('simulation_timezone', 'UTC'))).astimezone(timezone.utc)


def at(cfg, t=0):
    return (day_start(cfg) + timedelta(hours=float(t)*cfg.dt)).isoformat()


def _date(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('State/history clock must include timezone')
    return result.astimezone(timezone.utc)


def state_to_case_init(state):
    return {f.name: copy.deepcopy(getattr(state, f.name)) for f in fields(state) if f.name != 't'}


def state_from_case_init(cfg, init):
    from .rolling import SysState
    required = {'thermal', 'storage', 'hydro', 'hydro_used', 'pumped', 'hydro_hist',
                'hydro_p', 'hydro_online', 'pumped_used', 'pumped_hist'}
    if not required <= set(init):
        raise ValueError('Explicit actual state is incomplete: ' + ', '.join(sorted(required-set(init))))
    known = {f.name for f in fields(SysState)} - {'t'}
    state = SysState(t=0, **{k: copy.deepcopy(v) for k,v in init.items() if k in known})
    for h,v in state.hydro_hist.items(): state.hydro_hist[h] = np.asarray(v, float)
    for p,v in state.pumped_hist.items():
        state.pumped_hist[p] = {k: np.asarray(a, float) for k,a in v.items()}
    validate_system_state(cfg, state, expected_t=0)
    return state


def _ids(cfg, kind):
    return {d['id'] for d in cfg.units.get(kind, [])}


def _set(value, expected, name):
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f'{name}: exact resource set required; missing or unexpected resources')


def _finite(value, name, nonnegative=False):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int,float,np.number)) or not np.isfinite(value):
        raise ValueError(f'{name}: finite numeric value required')
    if nonnegative and value < -1e-6: raise ValueError(f'{name}: negative value')
    return float(value)


def validate_system_state(cfg, state, *, expected_t=None, continuation_required=False):
    from .input_validation import validate_physical_inputs
    t = state.t
    if isinstance(t, bool) or not isinstance(t, (int,np.integer)) or not 0 <= t <= cfg.T or (expected_t is not None and t != expected_t):
        raise ValueError('Actual state period is missing, invalid, or not the expected period')
    for name,kind in (('thermal','thermal'),('storage','storage'),('hydro','hydro'),('hydro_p','hydro'),
                      ('hydro_online','hydro'),('hydro_used','hydro'),('pumped','pumped'),
                      ('hydro_hist','hydro'),('pumped_used','pumped'),('pumped_hist','pumped')):
        _set(getattr(state,name), _ids(cfg,kind), name)
    if t == 0:
        state.day_initial_hydro = copy.deepcopy(state.hydro)
    if state.day_initial_hydro:
        _set(state.day_initial_hydro, _ids(cfg,'hydro'), 'day_initial_hydro')
    validate_physical_inputs(cfg, init=state_to_case_init(state))
    for spec in cfg.units.get('hydro', []):
        h=spec['id'];x=_finite(state.hydro_used[h], f'{h}.hydro_used', True)
        cap=spec['seasons'][cfg.base['season']].get('e_day')
        if spec.get('regulation') != 'run_of_river' and cap is not None and x > float(cap)+1e-6:
            raise ValueError(f'{h}: executed hydro energy exceeds daily budget')
    for h,a in state.hydro_hist.items():
        a = np.asarray(a,float)
        if a.shape != (cfg.T,) or not np.isfinite(a[:t]).all() or (a[:t] < -1e-6).any():
            raise ValueError(f'{h}: complete finite executed hydro history required')
        if not np.isnan(a[t:]).all(): raise ValueError(f'{h}: future actual history must be unknown')
    for spec in cfg.units.get('pumped',[]):
        p=spec['id']; _set(state.pumped_used[p], {'gen','pump'}, f'{p}.pumped_used')
        _set(state.pumped_hist[p], {'xg','xp'}, f'{p}.pumped_hist')
        for kind in ('gen','pump'):
            x=_finite(state.pumped_used[p][kind], f'{p}.used.{kind}',True)
            if abs(x-round(x)) > 1e-6 or x > spec['n_units']*spec['max_'+kind+'_starts']+1e-6:
                raise ValueError(f'{p}: invalid/exceeded daily startup count')
        for key,a in state.pumped_hist[p].items():
            a=np.asarray(a,float)
            if a.shape != (cfg.T,) or not np.isin(a[:t],[0,1]).all() or not np.isnan(a[t:]).all():
                raise ValueError(f'{p}: complete binary executed pumped history required')
    continuation = getattr(state,'continuation',{})
    if continuation_required and not continuation: raise ValueError('Continuation provenance is required')
    if continuation:
        if continuation.get('day_start') != at(cfg): raise ValueError('State day/date mismatch')
        if continuation.get('schema_version') != SCHEMA: raise ValueError('Unknown continuation schema')
        for h,n in history_requirements(cfg)[0].items():
            for index in range(-n,0): hydro_before(cfg,state,h,index)
        for p,n in history_requirements(cfg)[1].items():
            for index in range(-n,0):
                for key in ('xg','xp'): pumped_before(cfg,state,p,key,index)
        _set(state.storage_dispatch,_ids(cfg,'storage'),'storage_dispatch')
        _set(state.pumped_dispatch,_ids(cfg,'pumped'),'pumped_dispatch')
        for family,rows in (('storage',state.storage_dispatch),('pumped',state.pumped_dispatch)):
            for unit,row in rows.items():
                if row.get('source') == 'not_provided_initial':
                    if continuation.get('source') != 'research_assumption' or t != 0:
                        raise ValueError('Missing executed last-period dispatch')
                    continue
                keys=('P_ch','P_dis','u_ch','u_dis') if family=='storage' else ('P_gen','P_pump','xg','xp')
                for key in keys: _finite(row[key],f'{unit}.{key}',True)
    if hasattr(state,'hydro_commitment'):
        from .hydro_commitment import normalize_state
        state.hydro_commitment = normalize_state(cfg,state.hydro_commitment,t0=t,online_counts=state.hydro_online)
    return state


def history_requirements(cfg):
    hydro={}
    for d in cfg.units.get('hydro',[]):
        if d.get('upstream'):
            hydro[d['upstream']]=max(hydro.get(d['upstream'],0),int(d.get('lag_periods',0)))
    return hydro,{p['id']:int(p.get('switch_gap',1)) for p in cfg.units.get('pumped',[])}


def _get(value,key,default=None):
    return value.get(key,default) if isinstance(value,dict) else getattr(value,key,default)


def _history_value(cfg, state, family, unit, index, key=None):
    if index >= 0: raise ValueError('Pre-day query must use a negative index')
    record=_get(state,family+'_pre_history',{}).get(unit)
    if record is None: raise ValueError(f'Missing cross-day {family} history for {unit}[{index}]')
    if float(record.get('dt_h',-1)) != cfg.dt: raise ValueError('History time step changed')
    offset=((_date(at(cfg,index))-_date(record['start'])).total_seconds()/3600)/cfg.dt
    if abs(offset-round(offset)) > 1e-8: raise ValueError('History does not align to day grid')
    k=int(round(offset)); values=record['values'] if key is None else record['values'][key]
    if k < 0 or k >= len(values) or len(record.get('sources',[])) != len(values):
        raise ValueError(f'Missing cross-day {family} history for {unit}[{index}]')
    if record['sources'][k] not in ('executed','research_assumption'):
        raise ValueError('Forecast/unknown history cannot be consumed as actual')
    x=_finite(values[k],f'{unit}.history[{index}]',True)
    if key is not None and x not in (0.,1.): raise ValueError('Nonbinary pumped history')
    return x


def hydro_before(cfg, state, upstream, index):
    if _get(state,'continuation',{}) or upstream in _get(state,'hydro_pre_history',{}):
        return _history_value(cfg,state,'hydro',upstream,index)
    # Compatibility for explicitly legacy single-day callers only.
    d=next(h for h in cfg.units['hydro'] if h['id']==upstream)
    return float(d['water_rate']*d['init_units_on']*d['init_p_unit'])


def pumped_before(cfg,state,unit,key,index):
    if _get(state,'continuation',{}) or unit in _get(state,'pumped_pre_history',{}):
        return int(_history_value(cfg,state,'pumped',unit,index,key))
    d=next(p for p in cfg.units['pumped'] if p['id']==unit)
    return int(d.get('init_mode','idle') == ('gen' if key=='xg' else 'pump'))


def research_initial_state(cfg, *, acknowledge_constant_prehistory=False):
    """Explicit first-day assumption, never an implicit series fallback."""
    if acknowledge_constant_prehistory is not True:
        raise ValueError('Explicit acknowledgment of configured constant prehistory is required')
    from .rolling import initial_state
    state=initial_state(cfg)
    state.continuation=dict(schema_version=SCHEMA,source='research_assumption',day_start=at(cfg),
                            day_initial_hydro=copy.deepcopy(state.hydro),previous_checkpoint_hash=None)
    hs,ps=history_requirements(cfg)
    for h,n in hs.items():
        d=next(d for d in cfg.units['hydro'] if d['id']==h)
        state.hydro_pre_history[h]=dict(start=at(cfg,-n),dt_h=cfg.dt,
            values=[float(d['water_rate']*d['init_units_on']*d['init_p_unit'])]*n,sources=['research_assumption']*n)
    for p,n in ps.items():
        state.pumped_pre_history[p]=dict(start=at(cfg,-n),dt_h=cfg.dt,
            values={k:[int(state.pumped[p][k])]*n for k in ('xg','xp')},sources=['research_assumption']*n)
    state.storage_dispatch={s:dict(source='not_provided_initial') for s in state.storage}
    state.pumped_dispatch={p:dict(source='not_provided_initial') for p in state.pumped}
    return validate_system_state(cfg,state,expected_t=0,continuation_required=True)


def pack_state(cfg,state):
    validate_system_state(cfg,state)
    data=state_to_case_init(state); data['t']=int(state.t)
    data['hydro_hist']={h:np.asarray(a)[:state.t].tolist() for h,a in state.hydro_hist.items()}
    data['pumped_hist']={p:{k:np.asarray(a)[:state.t].tolist() for k,a in v.items()} for p,v in state.pumped_hist.items()}
    return dict(schema_version=SCHEMA,date=str(cfg.base['date']),state_at=at(cfg,state.t),periods=cfg.T,dt_h=cfg.dt,state=plain(data))


def unpack_state(cfg,packed):
    from .rolling import SysState
    if packed.get('schema_version')!=SCHEMA or packed.get('date')!=str(cfg.base['date']) or packed.get('dt_h')!=cfg.dt or packed.get('periods')!=cfg.T:
        raise ValueError('State schema/date/grid mismatch')
    data=copy.deepcopy(packed['state']);t=data['t']
    if packed.get('state_at')!=at(cfg,t): raise ValueError('State timestamp mismatch')
    for h,a in data['hydro_hist'].items():
        if len(a)!=t: raise ValueError('Truncated or overlong actual history')
        data['hydro_hist'][h]=np.r_[np.asarray(a,float),np.full(cfg.T-t,np.nan)]
    for p,v in data['pumped_hist'].items():
        for k,a in v.items():
            if len(a)!=t: raise ValueError('Truncated or overlong actual history')
            v[k]=np.r_[np.asarray(a,float),np.full(cfg.T-t,np.nan)]
    return validate_system_state(cfg,SysState(**data))


def physical_identity(cfg,net=None):
    units=copy.deepcopy(cfg.units)
    for group in ('thermal','storage','hydro','pumped'):
        for d in units.get(group,[]):
            for k in ('init','soc0','vup0','init_mode','init_units_on','init_p_unit','vup_end','vup_end_bid','max_gen_starts','max_pump_starts'):
                d.pop(k,None)
            if 'commitment' in d:
                records=d['commitment'].pop('initial_units',[])
                d['commitment']['unit_ids'] = sorted(row['unit_id'] for row in records)
            for row in d.get('seasons',{}).values():
                for k in ('qin','e_day','e_day_min','v_end','v0'): row.pop(k,None)
    result=dict(units=units,mode=cfg.mode,dt_h=cfg.dt,periods=cfg.T,rule_set=cfg.rule_set,
                simulation_timezone=cfg.base.get('simulation_timezone','UTC'),
                base_physics={key:cfg.base.get(key) for key in ('tie_line_mode','n1_loss_mw')},
                solver_physics={key:cfg.base.get('solver',{}).get(key) for key in ('storage_binary','commit_step_periods','tighten')})
    if net is not None:
        result['network']=dict(buses=net.buses,branches=net.branches.to_dict('records'),ptdf=net.ptdf.tolist(),
             load_share={str(k):float(v) for k,v in net.load_share.items()},ref_bus=net.ref_bus)
    return digest(result)


def export_checkpoint(result, *, input_hash, bid_hash, previous_hash=None):
    from .settlement import validate_execution_order
    from .rolling import advance
    cfg=result['da'].case.cfg;rr=result['rr']
    validate_execution_order(rr.rt,expected_periods=cfg.T,states=rr.states,allow_legacy=False)
    if len(rr.rt)!=cfg.T or len(rr.states)!=cfg.T+1:
        raise ValueError('Only complete actual days can create a boundary checkpoint')
    validate_system_state(cfg,rr.states[0],expected_t=0,continuation_required=True)
    # Independent recurrence over detached first-period quantities, not the ID/DA tail.
    for t,r in enumerate(rr.rt):
        expected=advance(rr.states[t],r['snap'],cfg)
        if digest(pack_state(cfg,expected))!=digest(pack_state(cfg,rr.states[t+1])):
            raise ValueError(f'Actual state replay mismatch after period {t}')
    payload=dict(schema_version=SCHEMA,kind='completed_actual_day',date=str(cfg.base['date']),
                 state_at=at(cfg,cfg.T),state=pack_state(cfg,rr.states[-1]),
                 initial_state_hash=digest(pack_state(cfg,rr.states[0])),
                 physical_identity=physical_identity(cfg,result['da'].case.net),input_hash=input_hash,bid_hash=bid_hash,
                 previous_checkpoint_hash=previous_hash,executed_periods=cfg.T,
                 units=dict(energy='MWh',power='MW',volume='1e4m3',outflow='m3/s',duration='h'),
                 source_cfg=dict(base=cfg.base,units=cfg.units,mode=cfg.mode),
                 pending=terminal_obligations(cfg,rr.states[-1]))
    return dict(payload,checkpoint_hash=digest(payload))


def next_day_state(checkpoint,cfg,*,net=None,expected_previous_hash=None):
    from .config import Config
    payload={k:v for k,v in checkpoint.items() if k!='checkpoint_hash'}
    if checkpoint.get('checkpoint_hash')!=digest(payload): raise ValueError('Checkpoint hash mismatch')
    if checkpoint.get('kind')!='completed_actual_day': raise ValueError('Not an actual complete-day checkpoint')
    if expected_previous_hash is not None and checkpoint['checkpoint_hash']!=expected_previous_hash:
        raise ValueError('Wrong predecessor checkpoint')
    old=Config(**checkpoint['source_cfg'])
    if cfg.dt*cfg.T!=24 or old.dt*old.T!=24: raise ValueError('Series requires complete fixed 24h production days')
    if day_start(cfg)!=day_start(old)+timedelta(hours=24) or _date(checkpoint['state_at'])!=day_start(cfg):
        raise ValueError('Next date is not the adjacent 24h business day (DST shifts are unsupported)')
    if physical_identity(cfg,net)!=checkpoint['physical_identity']:
        raise ValueError('Physical identity/network/rule configuration changed; start a new series branch')
    state=unpack_state(old,checkpoint['state']); validate_system_state(old,state,expected_t=old.T,continuation_required=True)
    hs,ps=history_requirements(cfg)
    for h,n in hs.items():
        values=[(hydro_before(old,state,h,k) if k<0 else float(state.hydro_hist[h][k])) for k in range(old.T-n,old.T)]
        previous=state.hydro_pre_history.get(h,{})
        sources=[('executed' if k>=0 else previous['sources'][int(round(((_date(at(old,k))-_date(previous['start'])).total_seconds()/3600)/old.dt))]) for k in range(old.T-n,old.T)]
        state.hydro_pre_history[h]=dict(start=at(cfg,-n),dt_h=cfg.dt,values=values,sources=sources)
    for p,n in ps.items():
        values={key:[(pumped_before(old,state,p,key,k) if k<0 else float(state.pumped_hist[p][key][k])) for k in range(old.T-n,old.T)] for key in ('xg','xp')}
        previous=state.pumped_pre_history[p]
        sources=[('executed' if k>=0 else previous['sources'][int(round(((_date(at(old,k))-_date(previous['start'])).total_seconds()/3600)/old.dt))]) for k in range(old.T-n,old.T)]
        state.pumped_pre_history[p]=dict(start=at(cfg,-n),dt_h=cfg.dt,values=values,sources=sources)
    state.t=0
    state.hydro_used={h:0. for h in state.hydro}; state.pumped_used={p:dict(gen=0.,pump=0.) for p in state.pumped}
    state.hydro_hist={h:np.full(cfg.T,np.nan) for h in state.hydro}
    state.pumped_hist={p:{k:np.full(cfg.T,np.nan) for k in ('xg','xp')} for p in state.pumped}
    state.continuation=dict(schema_version=SCHEMA,source='executed',day_start=at(cfg),day_initial_hydro=copy.deepcopy(state.hydro),
                            previous_checkpoint_hash=checkpoint['checkpoint_hash'],
                            reset_fields=['hydro_used','pumped_used'])
    return validate_system_state(cfg,state,expected_t=0,continuation_required=True)


def terminal_obligations(cfg,state):
    pending=[]
    for d in cfg.units.get('thermal',[]):
        v=state.thermal[d['id']];minimum=float(d['ut'] if v['online'] else d['dt'])
        if v['hours']+1e-9<minimum:
            pending.append(dict(unit=d['id'],kind='thermal_minimum_up' if v['online'] else 'thermal_minimum_down',
                                remaining_h=minimum-float(v['hours']),status='pending_future_execution_not_verified'))
    if getattr(state,'hydro_commitment',{}):
        from .hydro_commitment import pending as hydro_pending
        pending.extend(dict(row,kind='hydro_unit_duration',status='pending_future_execution_not_verified')
                       for row in hydro_pending(state.hydro_commitment) if row['remaining_h'] > 0)
    for d in cfg.units.get('pumped', []):
        p=d['id'];gap=int(d.get('switch_gap',1))
        for key,target in (('xg','pump'),('xp','gen')):
            for age in range(1,gap+1):
                index=state.t-age
                value=(state.pumped_hist[p][key][index] if index>=0 else pumped_before(cfg,state,p,key,index))
                if value:
                    pending.append(dict(unit=p,kind='pumped_switch_gap',blocked_mode=target,
                        remaining_periods=gap-age+1,remaining_h=(gap-age+1)*cfg.dt,
                        status='pending_future_execution_not_verified'))
                    break
    return pending


def thermal_duration_audit(cfg,initial,rt):
    violations=[]
    for d in cfg.units.get('thermal',[]):
        g=d['id']; on=bool(initial.thermal[g]['online']);hours=float(initial.thermal[g]['hours'])
        for r in rt:
            actual=float(r['snap']['u'][g][0])
            if actual not in (0.,1.): raise ValueError('Nonbinary actual thermal commitment')
            current=bool(actual)
            if current!=on:
                minimum=float(d['ut'] if on else d['dt'])
                if hours+1e-9<minimum:
                    violations.append(dict(unit=g,period=r['t'],actual_h=hours,required_h=minimum))
                on=current;hours=0.
            hours+=cfg.dt
    return violations


def pumped_execution_audit(cfg, initial, rt):
    violations=[];used=copy.deepcopy(initial.pumped_used);prior=copy.deepcopy(initial.pumped)
    for r in rt:
        t=r['t'];sn=r['snap']
        for d in cfg.units.get('pumped',[]):
            p=d['id'];xg=float(sn['xg'][p][0]);xp=float(sn['xp'][p][0])
            if xg not in (0,1) or xp not in (0,1) or xg+xp>1:
                violations.append(dict(unit=p,period=t,kind='invalid_mode'))
            for age in range(1,int(d.get('switch_gap',1))+1):
                for old,new in (('xg',xp),('xp',xg)):
                    k=t-age
                    previous=(float(rt[k]['snap'][old][p][0]) if k>=0 else pumped_before(cfg,initial,p,old,k))
                    if previous+new>1:
                        violations.append(dict(unit=p,period=t,kind='switch_gap',history_period=k))
            for kind,key,snapshot in (('gen','ng','ngen'),('pump','np','npump')):
                n=float(sn[snapshot][p][0]);used[p][kind]+=max(n-float(prior[p][key]),0.)
                if used[p][kind]>float(d['n_units']*d['max_'+kind+'_starts'])+1e-6:
                    violations.append(dict(unit=p,period=t,kind='daily_start_budget'))
                prior[p][key]=n
    return dict(valid=not violations,violations=violations,executed_start_counts=used)
