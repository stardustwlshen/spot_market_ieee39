"""Source-cell PRICE eligibility; fixed curve and mixed-quarter research mapping."""
import math
PROFILE='fixed_curve_15min_source_fixed_RESEARCH_v1'
def need(x,m):
 if not x:raise ValueError(m)
def derive(case,values,first,*,quarters=8,event_curves=None,profile=PROFILE):
 need(profile==PROFILE and type(first) is int and first>=0,'explicit quarter-aligned event PRICE research profile');out={};trace={};affected=[]
 for g,d in case['energy_inputs']['thermal'].items():
  def cell(t):
   power=values[f'power[{g},{t}]'];online=values[f'online[{g},{t}]'];stable=values[f'stable[{g},{t}]'];off=values.get(f'off[{g},{t}]',1-online)
   need(all(math.isfinite(v)for v in (power,online,stable,off)),'finite thermal state');need(all(abs(v-round(v))<=1e-6 and round(v)in (0,1)for v in (online,stable,off))and round(online)+round(off)==1 and stable<=online+1e-6,'inconsistent accepted thermal state')
   if off>.5:need(abs(power)<=1e-8,'off nonzero witness');return dict(state='off',mean=power,end=0.)
   if stable>.5:return dict(state='stable',mean=power,end=power)
   matches=[]
   if event_curves is not None:
    for ev in event_curves:
     if ev['unit']==g and ev['begin_tick']<=t<ev['begin_tick']+len(ev['rows']):matches.append((ev['phase'],ev['begin_tick'],ev['rows'][t-ev['begin_tick']]))
   else:
    cv=case['curves'][g];need(not cv.get('classes'),'unknown typed curve mapping; fixed declarations only')
    for phase,stem in [('startup','start'),('shutdown','stop')]:
     rows=cv[phase]
     for k in range(len(rows)):
      begin=t-k
      if begin>=0 and values.get(f'{stem}[{g},{begin}]',0)>.5:matches.append((phase,begin,rows[k]))
   need(len(matches)==1,'active cell lacks unique sourced fixed curve')
   phase,begin,row=matches[0];need(abs(power-row['mean'])<=1e-7,'active SCED power differs from declared curve');return dict(state=phase,begin_tick=begin,mean=power,end=row['end'],curve_row=dict(row))
  cells=[cell(t)for t in range(first,first+quarters)];prior=cell(first-1)if first else dict(state=d.get('initial_state_kind','stable'),mean=d['initial_power_mw'],end=d['initial_power_mw']);states=[]
  for q in range(quarters):
   chunk=cells[q:q+1];kinds={x['state']for x in chunk};status='pricing_stable'if kinds=={'stable'}else'off'if kinds=={'off'}else'fixed_nonpricing'
   need(status!='fixed_nonpricing'or any(x['state']in ('startup','shutdown')for x in chunk),'mixed off/stable without a sourced transition is unsupported');states.append(status)
  out[g]=states;trace[g]=dict(cells=cells,previous_cell=prior,quarter_means=[math.fsum(x['mean']for x in cells[q:q+1]) for q in range(quarters)])
  if any(s!='pricing_stable'for s in states)or prior['state']not in ('stable','off'):affected.append(g)
 return dict(profile=profile,ramp_mapping='affected units:8cell current/curve-endpoint physical interpolation; retain legacy quarter ramps on stable-to-stable legs and current incoming-state first-quarter anchor when initial quarter and prior cell are stable; RESEARCH',eligibility=out,trace=trace,affected_units=affected,mixed_quarter_mapping='any active curve cell fixes entire quarter to one accepted 15-minute mean-MW cell; conservative research mapping, not official implementation certification',source_cells_unchanged=True,formal=False)

def add_cell_ramps(model,g,d,values,agc,first,state):
 """Affected unit only: explicit8-cell physical interpolation, no ordinary quarter ramp clipping."""
 import pyomo.environ as p
 if not hasattr(model,'event_cells'):model.event_cells=p.Var(p.Any);model.event_rows=p.ConstraintList()
 cells=state['trace'][g]['cells'];statuses=state['eligibility'][g];prior=state['trace'][g]['previous_cell'];prev=prior['end']
 for i,z in enumerate(cells):
  t=first+i;q=i;v=model.event_cells[g,i]
  if statuses[q]!='pricing_stable':v.fix(z['mean'])
  else:v.setlb(d['min_mw'][t]+agc[g]['down_mw'][str(t)]);v.setub(d['max_mw'][t]-agc[g]['up_mw'][str(t)])
  # Shutdown begins at the sourced curve boundary, not at an unconstrained
  # optimized stable-cell endpoint. No ordinary ramp clips the curve itself.
  if z['state']=='shutdown' and z['begin_tick']==t:
   begin=z['curve_row']['begin']
   if i:model.event_rows.add(model.event_cells[g,i-1]==begin)
   else:need(abs(prior['end']-begin)<=1e-7,'shutdown initial endpoint mismatch')
  # Active trajectory is already fixed to the full raw curve; ordinary ramp applies
  # only to stable operation, using the prior curve endpoint on its first stable cell.
  if z['state']=='stable':
   anchor=prev if i==0 or cells[i-1]['state']=='stable'else cells[i-1]['end']
   model.event_rows.add(v-anchor<=d['ramp_up_mw_per_h'][t]/4);model.event_rows.add(anchor-v<=d['ramp_down_mw_per_h'][t]/4)
  prev=v if z['state']=='stable'else z['end']
 for q in range(len(statuses)):model.event_rows.add(model.P[g,q]==sum(model.event_cells[g,i]for i in range(q,q+1)))

def reference_and_ramp(model,names,specs,ramps,state,thermal_apply,ramp_apply,*,ramp_policy):
 # Never subject fixed trajectory means to stable Pmin/AGC bands or DA quarter ramp.
 for column,spec in specs.items():
  g,q=column[2:-1].rsplit(',',1);need(spec['status']==state['eligibility'][g][int(q)],'reference status differs from derived source cells')
 stable_specs={k:v for k,v in specs.items()if v['status']!='fixed_nonpricing'};new,trace=thermal_apply(model,names,stable_specs)
 ix={k:i for i,k in enumerate(names['variables'])}
 for k,v in specs.items():
  if v['status']=='fixed_nonpricing':
   c=new['variables'][ix[k]];need(c['lb']==c['ub']and abs(c['lb']-v['current_sced'])<=1e-8,'nonpricing curve not fixed to accepted mean');trace.append(dict(column=k,status='fixed_nonpricing',bounds=[c['lb'],c['ub']],profile=PROFILE))
 def keep_anchor(column):
  g=column.split('[',1)[1].rsplit(',',1)[0]
  return g not in state['affected_units']or(state['eligibility'][g][0]=='pricing_stable'and state['trace'][g]['previous_cell']['state']=='stable')
 allowed={k:v for k,v in ramps.items()if keep_anchor(k)};new,ramp_trace=ramp_apply(new,names,allowed,policy=ramp_policy)
 return new,trace,ramp_trace
