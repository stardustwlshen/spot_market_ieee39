"""Explicit migration of a sourced 5-minute input window to eight quarters.
Not a migration of solved results. No old 5-minute checkpoint is accepted as 15-minute.
"""
from copy import deepcopy
import math,json

def convert_case(case, boundary, agc, *, quarter_count=8, convert_boundary=True):
    from smm.gd_horizon import EnergyHorizon,EnergyPeriod
    c=deepcopy(case);h=c['energy_inputs']['horizon'];hvars=h if isinstance(h,dict) else vars(h)
    periods=hvars['periods'];ids=[x['index'] if isinstance(x,dict) else x.index for x in periods]
    if ids!=list(range(len(ids))) or len(ids)<quarter_count*3:raise ValueError('full sourced first24 five-minute input cells required')
    trace=[]
    def reduce(v,path):
        if isinstance(v,dict):
            if v and all(isinstance(k,tuple) and len(k)==2 and isinstance(k[1],int) for k in v):
                units={k[0] for k in v}
                if all({k[1] for k in v if k[0]==u}==set(ids) for u in units):
                    return {(u,t):a for u in units for t,a in reduce({k[1]:a for k,a in v.items() if k[0]==u},path).items()}
            if set(v)==set(ids):
                out={}
                for q in range(quarter_count):
                    a=[v[t] for t in range(3*q,3*q+3)]
                    if all(type(x)is bool for x in a):out[q]=all(a)
                    elif all(isinstance(x,(int,float)) and not isinstance(x,bool) for x in a):
                        if path.endswith(('min_mw','min_flow_mw','minimum_mw')):out[q]=max(a)
                        elif path.endswith(('max_mw','max_flow_mw','ramp_up_mw_per_h','ramp_down_mw_per_h')):out[q]=min(a)
                        else:out[q]=math.fsum(a)/3
                    elif all(x==a[0] for x in a):out[q]=a[0]
                    else:raise ValueError('ambiguous categorical subquarter input '+path)
                trace.append(path);return out
            return {k:reduce(x,path+'.'+str(k)) for k,x in v.items()}
        if isinstance(v,list):return [reduce(x,path) for x in v]
        if isinstance(v,tuple):return tuple(reduce(x,path) for x in v)
        return v
    c=reduce(c,'case')
    c['energy_inputs']['horizon']=EnergyHorizon(tuple(EnergyPeriod(t,.25,.25,'delivery',t==0) for t in range(quarter_count)),'real_time' if quarter_count==8 else 'component','Guangdong2026 6.6.2.1 15min; 8-period horizon is declared research setting')
    for g,curves in c['curves'].items():
        for kind in ['startup','shutdown']:
            rows=curves[kind]
            if len(rows)%3:raise ValueError('transition duration not aligned to 15min: '+g)
            curves[kind]=[dict(begin=rows[i]['begin'],mean=math.fsum(x['mean'] for x in rows[i:i+3])/3,end=rows[i+2]['end'],cost=math.fsum(x['cost'] for x in rows[i:i+3])/3) for i in range(0,len(rows),3)]
    b=deepcopy(boundary)
    for g,v in (b.items() if convert_boundary else []):
        if v['event_elapsed_ticks']%3 or len(v['remaining_curve'])%3:raise ValueError('active event boundary not quarter-aligned: '+g)
        v['event_elapsed_ticks']//=3
        for k in ['residual_up_ticks','residual_down_ticks']:v[k]=math.ceil(v[k]/3)
        rows=v['remaining_curve'];v['remaining_curve']=[dict(begin=rows[i]['begin'],mean=math.fsum(x['mean'] for x in rows[i:i+3])/3,end=rows[i+2]['end'],cost=math.fsum(x['cost'] for x in rows[i:i+3])/3) for i in range(0,len(rows),3)]
    c['contract'].update(rt_clock_mode='provincial_15min_execution',near_far_mode='provincial_8_quarter_research',clock_interpretation_source='same-period execution simulation; provincial15min; 2h is research window')
    source_id=c['battery_contract']['storage']['battery_parameters_source_id'];c['battery_contract']['sources'][source_id]['artifact']=json.dumps(dict(batteries=c['batteries'],pumped_storage=c['battery_contract']['storage']['pumped_storage']),sort_keys=True)
    agc=deepcopy(agc)
    for row in agc.values():
        for side in ('up_mw','down_mw'):row[side]={int(k):v for k,v in row[side].items()}
    return c,b,reduce(agc,'agc'),dict(input_mapping='three5min means; conservative min/max capacity; exact categorical; event energy-preserving',mapped_series=trace,grid_minutes=15,horizon_periods=quarter_count,executed_periods=1,thermal_commitment_not_changed_here=True)

def convert_full_case(case):
    """Source-bound 15min input view; caller pins original file + this transform.
    Old source file stays unchanged. This is not a solved-result/carry conversion.
    """
    h=case['energy_inputs']['horizon'];p=h['periods'] if isinstance(h,dict) else h.periods
    if len(p)%3:raise ValueError('full source horizon not quarter-aligned')
    c,_,_,trace=convert_case(case,{}, {},quarter_count=len(p)//3,convert_boundary=False)
    c['_grid_transform']=trace
    return c
