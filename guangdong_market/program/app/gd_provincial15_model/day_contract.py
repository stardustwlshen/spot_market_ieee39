"""Explicit published research-day declarations; no official default or future-plan inference."""
from datetime import datetime,timedelta
from copy import deepcopy
import hashlib,json,math

def stamp(x):
 d=datetime.fromisoformat(x)
 if d.utcoffset()!=timedelta(hours=8):raise ValueError('aware UTC+8 absolute timestamp required')
 return d

def digest(z):return hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def apply(case,carry,declarations):
 c=deepcopy(case);d=declarations
 if d.get('schema')!='gd.short.day_declarations.research.v1'or d.get('mode')!='hypothetical_published_research':raise ValueError('formal/unsourced future day rejected')
 cut=stamp(carry['cutoff']);begin=stamp(carry['boundary_at']);origin=stamp(carry['origin']);day_end=origin+timedelta(days=1);n=min(8,int((day_end-begin).total_seconds()/900));
 if not 1<=n<=8:raise ValueError('single-day window outside delivery day')
 end=begin+timedelta(minutes=15*n);units=set(c['energy_inputs']['thermal']);bats=set(c['batteries']);dates=sorted({(begin+timedelta(minutes=15*t)).date().isoformat()for t in range(n)})
 for date in dates:
  if date not in d['days']:raise ValueError('missing covered day declaration/plan '+date)
 proof=dict(satisfied_targets=[],future_target_envelopes=[],published_plan_days=[],formal=False,future_network_feasibility_proven=False)
 for date,day in sorted(d['days'].items()):
  if stamp(day['available_at'])>cut or stamp(day['published_at'])>cut:raise ValueError('future publication cannot be consumed')
  if day['basis']!='explicit_new_research_assumption' or not day['source'] or day['artifact_sha256']!=digest(day['artifact']):raise ValueError('unbound research-day artifact')
  a=day['artifact']
  if set(a['storage'])!=bats or set(a['thermal_online'])!=units:raise ValueError('complete day resources required')
  if any(len(v) not in (96,288) or any(type(x)not in(int,bool) or x not in(0,1)for x in v)for v in a['thermal_online'].values()):raise ValueError('complete96quarter plan or explicitly legacy288cell plan')
  for u,z in a['storage'].items():
   at=stamp(z['day_end_at']);expected=stamp(date+'T00:00:00+08:00')+timedelta(days=1)
   if at!=expected or not math.isfinite(z['energy_mwh']):raise ValueError('dated end target')
   if not(c['batteries'][u]['energy_min_mwh']<=z['energy_mwh']<=c['batteries'][u]['energy_max_mwh']):raise ValueError('target bounds')
  if date in dates:proof['published_plan_days'].append(date)
 for u,b in c['batteries'].items():
  state=carry['storage'][u];candidates=[]
  for date,day in sorted(d['days'].items()):
   z=day['artifact']['storage'][u];at=stamp(z['day_end_at'])
   if at==begin:
    if abs(state['initial_energy_mwh']-z['energy_mwh'])>1e-6:raise ValueError('midnight target missed; cannot reset SOC')
    proof['satisfied_targets'].append(dict(unit=u,at=at.isoformat(),energy=state['initial_energy_mwh'],declared=z['energy_mwh']))
   elif at>begin:candidates.append((at,z,date))
  if not candidates:raise ValueError('no subsequent sourced day-end target')
  at,z,date=min(candidates,key=lambda x:x[0]);b['terminal_at']=at.isoformat();b['terminal_energy_mwh']=z['energy_mwh']
  for date in dates:
   declared=d['days'][date]['artifact']['storage'][u]['limit_cycles']
   if date not in state['cycle_budget']:raise ValueError('missing dated carry throughput ledger')
   if state['cycle_budget'][date]['limit_cycles']!=declared:raise ValueError('day cycle declaration vs carried limit mismatch')
  for future,z,date in candidates:
   if future>end:proof['future_target_envelopes'].append(dict(unit=u,at=future.isoformat(),target_mwh=z['energy_mwh'],remaining_hours=(future-end).total_seconds()/3600))
 return c,proof

def plan_at(declarations,unit,at):
 x=stamp(at);day=declarations['days'][x.date().isoformat()]['artifact'];plan=day['thermal_online'][unit];step=15 if len(plan)==96 else 5
 i=(x.hour*60+x.minute)//step
 return plan[i]
