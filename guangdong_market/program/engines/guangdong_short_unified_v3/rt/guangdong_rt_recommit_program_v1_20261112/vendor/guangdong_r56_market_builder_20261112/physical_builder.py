"""Free quarter-aligned events on a real five-minute physical chain.
DA98 is an output projection, not equivalence to official representative-point costs.
No solver, authenticated service qualification or execution authority.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import math
import pyomo.environ as pyo
from smm.gd_energy_core import build_energy_core, assert_linear_energy_model
from smm.gd_da_battery_candidate import validate_batteries, _attach_batteries
from smm.gd_r23_scuc_contract import validate_contract
DT = 1/12

def require(ok, message):
    if not ok: raise ValueError(message)

def validate_curves(curves, thermal, clock):
    require(set(curves)==set(thermal), 'every thermal unit needs explicit startup/shutdown curves')
    out=deepcopy(curves)
    for g,c in out.items():
        require(c['basis']=='research', 'only explicit research curves supported by this prototype')
        require(isinstance(c['source'],str) and bool(c['source']), 'curve source required')
        require(datetime.fromisoformat(c['available_at'])<=datetime.fromisoformat(clock['cutoff']), 'curve unavailable at declaration cutoff')
        d=thermal[g]; minimum=d['min_mw'][0]
        require(all(v==minimum for v in d['min_mw'].values()), 'time-varying curve endpoint unsupported')
        for kind in ('startup','shutdown'):
            rows=c[kind]; require(isinstance(rows,list) and rows, 'complete nonempty transition curve')
            previous=0. if kind=='startup' else minimum
            for row in rows:
                require(set(row)=={'begin','mean','end','cost'}, 'explicit curve begin/mean/end/cost required')
                require(all(math.isfinite(v) for v in row.values()), 'finite curve coefficients')
                require(abs(row['begin']-previous)<=1e-9, 'curve endpoint gap')
                require(0<=min(row['begin'],row['end'])<=row['mean']<=max(row['begin'],row['end'])<=minimum, 'curve outside subminimum envelope')
                require((row['end']-row['begin'])*(1 if kind=='startup'else -1)>=0, 'curve reverses direction')
                previous=row['end']
            require(abs(previous-(minimum if kind=='startup'else 0.))<=1e-9, 'curve final endpoint')
    return out

def build_free_scuc(*,energy_inputs,reserve,curves,contract,batteries=None,battery_contract=None,no_load_cost=None,agc_awards=None):
    e=deepcopy(energy_inputs);ts=tuple(e['horizon'].ids);n=len(ts)
    require(ts==tuple(range(n)) and all(abs(x-DT)<1e-12 for x in e['horizon'].duration_h.values()), 'dense real five-minute chain required')
    clock=validate_contract(contract,n);curves=validate_curves(curves,e['thermal'],clock)
    require(e['balance_policy']=='hard' and e['renewable_mode']=='dispatchable_bus_aggregate', 'hard balance/dispatchable aggregate required')
    require(set(reserve)=={'positive_mw','negative_mw','primary_mw','contingency_mw','primary_fraction','contingency_eligible'}, 'reserve inventory')
    for key in ('positive_mw','negative_mw','primary_mw','contingency_mw'):
        require(set(reserve[key])==set(ts) and all(math.isfinite(x)and x>=0 for x in reserve[key].values()), 'complete nonnegative reserve series')
    require(set(reserve['primary_fraction'])==set(e['thermal'])==set(reserve['contingency_eligible']), 'reserve qualification inventory')
    no_load_cost=no_load_cost or {g:0. for g in e['thermal']}
    require(set(no_load_cost)==set(e['thermal']) and all(math.isfinite(x)and x>=0 for x in no_load_cost.values()), 'declared hourly no-load costs')
    agc=deepcopy(agc_awards)if agc_awards is not None else{g:dict(up_mw={t:0. for t in ts},down_mw={t:0. for t in ts},qualification='no_award',basis='research',source='explicit zero AGC research subset',available_at=contract['cutoff'])for g in e['thermal']}
    require(set(agc)==set(e['thermal']), 'AGC thermal inventory mismatch')
    for g,a in agc.items():
        require(a['basis']=='research'and a['qualification']in('no_award','explicit_synthetic_qualified'), 'real AGC qualification is not authenticated by this research input')
        require(bool(a['source'])and datetime.fromisoformat(a['available_at'])<=datetime.fromisoformat(clock['cutoff']), 'explicit available AGC source required')
        for key in ('up_mw','down_mw'):
            require(set(a[key])==set(ts)and all(math.isfinite(v)and v>=0 for v in a[key].values()), 'finite full AGC capacity series')
        for t in ts:
            require(a['up_mw'][t]+a['down_mw'][t]<=e['thermal'][g]['max_mw'][t]-e['thermal'][g]['min_mw'][t], 'AGC exceeds original stable power range')
            require(a['qualification']=='explicit_synthetic_qualified'or a['up_mw'][t]+a['down_mw'][t]==0, 'nonzero AGC lacks explicit synthetic qualification')
    m=build_energy_core(**e);ds=m._energy_inputs['thermal']
    for name in ('c_commitment','c_transition_exclusive','c_segment_capacity','c_power_sum','c_power_min','c_power_max','c_ramp_up','c_ramp_down','c_min_up','c_min_down','c_initial_min_duration','c_status','c_max_starts','c_max_stops'):
        getattr(m,name).deactivate()
    for component in (m.line_over,m.line_under,m.section_over,m.section_under):
        for v in component.values():v.fix(0.)
    m.stable=pyo.Var(m.G,m.T,domain=pyo.Binary,initialize=0)
    m.off=pyo.Var(m.G,m.T,domain=pyo.Binary,initialize=0)
    def active(g,t,kind,field=None):
        rows=curves[g][kind];var=m.start if kind=='startup'else m.stop
        return sum((1 if field is None else rows[k][field])*var[g,t-k]for k in range(min(len(rows),t+1)))
    m.transition_mean=pyo.Expression(m.G,m.T,rule=lambda _,g,t:active(g,t,'startup','mean')+active(g,t,'shutdown','mean'))
    m.transition_begin=pyo.Expression(m.G,m.T,rule=lambda _,g,t:active(g,t,'startup','begin')+active(g,t,'shutdown','begin'))
    m.transition_end=pyo.Expression(m.G,m.T,rule=lambda _,g,t:active(g,t,'startup','end')+active(g,t,'shutdown','end'))
    m.transition_active=pyo.Expression(m.G,m.T,rule=lambda _,g,t:active(g,t,'startup')+active(g,t,'shutdown'))
    m.end_power=pyo.Expression(m.G,m.T,rule=lambda _,g,t:m.power[g,t]-m.transition_mean[g,t]+m.transition_end[g,t])
    m.phase_balance=pyo.ConstraintList();m.event_availability=pyo.ConstraintList();m.residence=pyo.ConstraintList();m.physical_ramps=pyo.ConstraintList()
    m.event_cost=pyo.Expression(expr=sum(ds[g]['startup_yuan'][t]*m.start[g,t]+ds[g]['shutdown_yuan'][t]*m.stop[g,t]for g in m.G for t in ts))
    for g,d in ds.items():
        require(d['initial_state_kind']=='stable', 'unfinished initial curves require explicit future extension; not silently reset')
        ns,nd=len(curves[g]['startup']),len(curves[g]['shutdown'])
        big=max(d['max_mw'].values());u0=d['initial_online'];age=d['initial_state_age_h']
        for t in ts:
            prev_stable=u0 if t==0 else m.stable[g,t-1]
            prev_off=1-u0 if t==0 else m.off[g,t-1]
            completed_start=m.start[g,t-ns]if t>=ns else 0
            completed_stop=m.stop[g,t-nd]if t>=nd else 0
            m.phase_balance.add(m.stable[g,t]==prev_stable+completed_start-m.stop[g,t])
            m.phase_balance.add(m.off[g,t]==prev_off+completed_stop-m.start[g,t])
            m.phase_balance.add(m.stable[g,t]+m.off[g,t]+m.transition_active[g,t]==1)
            m.phase_balance.add(m.online[g,t]==1-m.off[g,t])
            m.event_availability.add(m.start[g,t]<=prev_off+completed_stop)
            m.event_availability.add(m.stop[g,t]<=prev_stable+completed_start)
            if t%3:m.start[g,t].fix(0);m.stop[g,t].fix(0)
            at=datetime.fromisoformat(clock['day_start'])+timedelta(minutes=5*t)
            if at<datetime.fromisoformat(clock['publication_at'])+timedelta(hours=curves[g]['notice_h']):m.start[g,t].fix(0)
            if age+t*DT<(d['min_up_h']if u0 else d['min_down_h'])-1e-9:
                (m.stop if u0 else m.start)[g,t].fix(0)
            # Up-time from startup BEGIN; downtime from shutdown END.
            up=range(max(0,t-math.ceil(d['min_up_h']/DT)+1),t+1)
            down=range(max(0,t-nd-math.ceil(d['min_down_h']/DT)+1),max(0,t-nd+1))
            m.residence.add(m.stop[g,t]+sum(m.start[g,j]for j in up)<=1)
            m.residence.add(m.start[g,t]+sum(m.stop[g,j]for j in down)<=1)
            status=d['status'][t]
            if status!='free':m.event_availability.add(m.online[g,t]==int(status=='on'))
            if agc[g]['up_mw'][t]+agc[g]['down_mw'][t]>0:m.event_availability.add(m.stable[g,t]==1)
            prev=d['initial_power_mw']if not t else m.end_power[g,t-1]
            m.physical_ramps.add(m.power[g,t]-prev<=DT*d['ramp_up_mw_per_h'][t]+big*(1-m.stable[g,t]))
            m.physical_ramps.add(prev-m.power[g,t]<=DT*d['ramp_down_mw_per_h'][t]+big*(1-m.stable[g,t]))
            m.physical_ramps.add(prev-m.transition_begin[g,t]<=big*(1-m.transition_active[g,t]))
            m.physical_ramps.add(m.transition_begin[g,t]-prev<=big*(1-m.transition_active[g,t]))
        for var,key in ((m.start,'max_starts'),(m.stop,'max_stops')):
            if d[key]is not None:m.event_availability.add(sum(var[g,t]for t in ts)<=d[key])
    m.free_segments=pyo.Constraint(m.GK,m.T,rule=lambda _,g,k,t:m.segment_power[g,k,t]<=ds[g]['segments'][k]['width_mw']*m.stable[g,t])
    m.free_power=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.power[g,t]==ds[g]['bid_base_mw']*m.stable[g,t]+sum(m.segment_power[g,k,t]for k in ds[g]['segments'])+m.transition_mean[g,t])
    m.free_lower=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.power[g,t]>=(ds[g]['min_mw'][t]+agc[g]['down_mw'][t])*m.stable[g,t]+m.transition_mean[g,t])
    m.free_upper=pyo.Constraint(m.G,m.T,rule=lambda _,g,t:m.power[g,t]<=(ds[g]['max_mw'][t]-agc[g]['up_mw'][t])*m.stable[g,t]+m.transition_mean[g,t])
    for g in m.G:
        for t in ts:
            m.online_min[g,t].set_value((ds[g]['min_mw'][t]+agc[g]['down_mw'][t])*m.stable[g,t]+m.transition_mean[g,t])
            m.online_max[g,t].set_value((ds[g]['max_mw'][t]-agc[g]['up_mw'][t])*m.stable[g,t]+m.transition_mean[g,t])
    if batteries:
        require(battery_contract is not None, 'bound battery declaration required')
        data,bc=validate_batteries(batteries,battery_contract,e['horizon'],e['buses'])
        _attach_batteries(m,data,bc,battery_contract)
    m.primary=pyo.Var(m.G,m.T,domain=pyo.NonNegativeReals,initialize=0)
    m.contingency=pyo.Var(m.G,m.T,domain=pyo.NonNegativeReals,initialize=0)
    m.reserves=pyo.ConstraintList()
    for t in ts:
        demand=sum(m.demand_mw[b,t]for b in m.B)
        fixed=sum(m.renewable_power[b,t]+m.physical_tie[b,t]+m.other_injection[b,t]for b in m.B)
        m.reserves.add(sum(m.online_max[g,t]for g in m.G)+fixed>=demand+reserve['positive_mw'][t])
        m.reserves.add(sum(m.online_min[g,t]for g in m.G)+fixed<=demand-reserve['negative_mw'][t])
        for g,d in ds.items():
            require(0<=reserve['primary_fraction'][g]<=1 and type(reserve['contingency_eligible'][g])is bool, 'explicit research reserve eligibility')
            for var in (m.primary,m.contingency):m.reserves.add(var[g,t]<=m.online_max[g,t]-m.power[g,t])
            m.reserves.add(m.primary[g,t]<=reserve['primary_fraction'][g]*d['max_mw'][t]*m.stable[g,t])
            m.reserves.add(m.contingency[g,t]<=int(reserve['contingency_eligible'][g])*d['ramp_up_mw_per_h'][t]/6*m.stable[g,t])
        m.reserves.add(sum(m.primary[g,t]for g in m.G)>=reserve['primary_mw'][t])
        m.reserves.add(sum(m.contingency[g,t]for g in m.G)>=reserve['contingency_mw'][t])
    m.free_thermal_cost=pyo.Expression(expr=m.event_cost+sum(DT*((d['base_bid_yuan_per_h'][t]+no_load_cost[g])*m.stable[g,t]+sum(s['price_yuan_per_mwh'][t]*m.segment_power[g,k,t]for k,s in d['segments'].items())+active(g,t,'startup','cost')+active(g,t,'shutdown','cost'))for g,d in ds.items()for t in ts))
    m.total_cost.set_value(m.free_thermal_cost+m.other_cost+m.renewable_curtailment_cost+m.network_penalty_cost+m.balance_penalty_cost)
    m._r23=dict(inputs=deepcopy(dict(energy_inputs=e,reserve=reserve,curves=curves,contract=contract,batteries=batteries,battery_contract=battery_contract,no_load_cost=no_load_cost,agc_awards=agc)),clock=clock,scope='free events; continuous physical research objective; DA98 output projection; explicit external synthetic AGC capacity sensitivity only, no auction or formal qualification')
    assert_linear_energy_model(m)
    return m
