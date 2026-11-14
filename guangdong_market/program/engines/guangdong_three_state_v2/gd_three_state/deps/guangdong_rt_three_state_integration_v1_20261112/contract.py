from component5 import StartupDTO,Curve,validate
from datetime import datetime
import math

def dto(g,s,e,original):
 if s.get('schema')!='gd.rt.three_state.physical.v1' or s.get('basis')!='research' or s.get('dt_minutes')!=5 or s.get('tail_policy')!='complete_all_curves_by_horizon':raise ValueError('explicit research schema/5min/tail required')
 u=s['units'][g];d=e['thermal'][g]
 if not u['source'] or not u['threshold_source']:raise ValueError('missing source')
 def cv(rows,fee,notice):return Curve(tuple(r['mean']for r in rows),tuple(r['begin']for r in rows),tuple(r['end']for r in rows),fee,notice,u['source'])
 age=d['initial_state_age_h']*12
 if abs(age-round(age))>1e-8:raise ValueError('initial elapsed age not exact5min')
 shutdown=set(d['shutdown_yuan'].values())
 if len(shutdown)!=1:raise ValueError('time-varying shutdown fee needs explicit extension')
 obj=StartupDTO('research',u['source'],5,'disconnect_to_start_curve_begin',u['threshold_source'],u['hot_upper_ticks'],u['warm_upper_ticks'],'stable'if d['initial_online']else'off',0 if d['initial_online']else int(round(age)),d['source'],None,None,'not official fees', {k:cv(z['rows'],z['fee_yuan'],z['notice_ticks'])for k,z in u['classes'].items()},cv(original[g]['shutdown'],next(iter(shutdown)),0),classification_boundary_policy='hot_lt_H_warm_lt_W')
 for z in u['classes'].values():
  for row in z['rows']:
   if set(row)!= {'begin','mean','end','cost'} or not all(math.isfinite(x)for x in row.values()):raise ValueError('complete physical rows and hourly curve cost required')
 if obj.curves['hot'].end_mw[-1]!=d['min_mw'][0]:raise ValueError('stable minimum mismatch')
 return validate(obj)

def from_uniform(case):
 e=case['energy_inputs'];units={}
 for g,d in e['thermal'].items():
  fee=set(d['startup_yuan'].values());lead=case['curves'][g]['notice_h']*12
  if len(fee)!=1 or abs(lead-round(lead))>1e-8:raise ValueError('uniform equivalence requires scalar fees/exact notice')
  units[g]=dict(source='explicit independent degenerate research: identical original curves/fees, not actual cold/warm/hot device data',threshold_source='synthetic regression boundaries60min/120min, not Guangdong parameters',hot_upper_ticks=12,warm_upper_ticks=24,preorigin_notices=[],classes={k:dict(rows=case['curves'][g]['startup'],fee_yuan=next(iter(fee)),notice_ticks=int(round(lead)))for k in ('hot','warm','cold')})
 return dict(schema='gd.rt.three_state.physical.v1',basis='research',dt_minutes=5,tail_policy='complete_all_curves_by_horizon',units=units)


def pre_notices(rows):
 if not isinstance(rows,(list,tuple)):raise ValueError('explicit negative-time notification inventory')
 result=[]
 for row in rows:
  if not isinstance(row,(list,tuple)) or len(row)!=2 or row[0]not in ('hot','warm','cold') or type(row[1])is not int or row[1]>=0:raise ValueError('invalid past class/notice tick')
  result.append(tuple(row))
 if len(set(result))!=len(result):raise ValueError('duplicate past notice')
 return tuple(result)
