"""Independent 8-quarter PRICE research adapter, never consumes dispatch duals."""
from pathlib import Path
from datetime import datetime,timedelta
import json,hashlib,sys,math
ROOT=Path(__file__).resolve().parent / "vendor"
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
def need(ok,msg):
 if not ok:raise ValueError(msg)
def digest(z):return hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,sort_keys=True,allow_nan=False)
def accepted(path):
 a=read(path);need(a['schema']=='gd.research.generic_rt.accepted_pair.v1'and a['SCUC_primal_integer_gap_passed']is True and a['SCED_full_KKT_passed']is True,'actual accepted pair required')
 for p,h in a['pins'].items():need(sha(p)==h,'accepted evidence changed: '+p)
 r=read(a['request_path']);need(r['run_id']==a['run_id']and r['window']==a['window']and digest(r)==a['request_sha256'],'same run/window/request')
 n=read(a['names_path']);ed=read(a['SCED_response_path']);need(sha(a['SCED_response_path'])==a['SCED_response_sha256'],'SCED binding');need(ed['native_status']=='Optimal'and ed['has_primal']and ed['dual_valid'],'actual SCED optimal required')
 x=dict(zip(n['variables'],ed['primal']));need(len(x)==len(n['variables'])and all(math.isfinite(v)for v in x.values()),'full finite named vector')
 for key,v in list(x.items()):
  if key.startswith('battery.charge['):
   tail=key[len('battery.charge'):];x['battery.power'+tail]=x['battery.discharge'+tail]-v
 return a,r,x

def mode(x,u,t):return 'charge'if x[f'battery.charge_mode[{u},{t}]']>.5 else'discharge'if x[f'battery.discharge_mode[{u},{t}]']>.5 else'idle'
def band(ref,lo,hi,delta=.1):
 need(all(math.isfinite(v)for v in (ref,lo,hi,delta))and lo<=hi and 0<=delta<=1,'finite bounded band')
 lower=max(lo,ref-delta*abs(ref));upper=min(hi,ref+delta*abs(ref));need(lower<=upper+1e-9,'empty reference/new-mode intersection')
 if lower>upper:lower=upper # roundoff only, <=1e-9
 return lower,upper

def prepare(pair,case_path,agc_path,da_run,output,*,research_current_sced=False,boundary="DA_reference_R61_compatible_research", verified_inputs=None):
 need(verified_inputs is not None,"program verified inputs required");a,r,x=verified_inputs;need(boundary in ('current_sced_research','DA_reference_R61_compatible_research'),'explicit supported boundary');need(research_current_sced is True,'explicit independent research boundary opt-in required; official route checked separately')
 need(sha(case_path)==r['case_sha256']and sha(agc_path)==r['sources']['AGC']['sha256'],'same case/AGC');sys.path.insert(0,str(ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'))
 from smm.gd_october_job_codec import loads
 from smm.highs_solver import serialize_model,validate_options
 c=loads(Path(case_path).read_bytes());e=c['energy_inputs'];need(not e['sections'],'section constraints require explicit PRICE adapter');agc=read(agc_path);first=r['delivery_tick'];day=datetime.fromisoformat(r['origin']);start=day+timedelta(minutes=5*first);need(first%3==0 and first+24<=576,'quarter aligned full source horizon')
 need(datetime.fromisoformat(r['cutoff'])==start-timedelta(minutes=15),'15-minute issue lead')
 for z in r['sources'].values():need(datetime.fromisoformat(z['available_at'])<=datetime.fromisoformat(r['cutoff'])and z['uses_future_actual']is False,'causal known source only')
 da=Path(da_run);ar=read(da/'ARTIFACTS.json');need(sha(da/ar['inputs']['case']['path'])==r['case_sha256'],'DA comparison must match actual case')
 need(ar['stages']['da_sced']['acceptance']['sha256']==r['DA_acceptance_sha256'] and sha(da/ar['stages']['da_sced']['acceptance']['path'])==r['DA_acceptance_sha256'],'same accepted DA reference, not merely same case');phref=ar['stages']['da_sced']['physical_solution'];ph=da/phref['path'];need(sha(ph)==phref['sha256'],'actual DA physical comparison');old=read(ph)['physical_cells'];conflicts=[]
 for u in c['batteries']:
  for t in range(first,first+24):
   previous=next(k for k,v in old[t]['battery'][u]['permission'].items()if v);new=mode(x,u,t)
   if previous!=new:conflicts.append(dict(unit=u,cell=t,DA_mode=previous,new_RT_mode=new))
 xr=dict(x)
 if boundary=='DA_reference_R61_compatible_research':
  for t,cell in enumerate(old):
   for g,z in cell['thermal'].items():xr[f'power[{g},{t}]']=z['mean_mw']
   for u,z in cell['battery'].items():
    for name,field in [('charge','charge_mw'),('discharge','discharge_mw'),('energy','energy_mwh')]:xr[f'battery.{name}[{u},{t}]']=z[field]
    xr[f'battery.power[{u},{t}]']=z['discharge_mw']-z['charge_mw']
    xr[f'battery.charge_mode[{u},{t}]']=z['permission']['charge'];xr[f'battery.discharge_mode[{u},{t}]']=z['permission']['discharge']
   for b,z in cell['renewable'].items():xr[f"renewable_power['{b}',{t}]"]=z['dispatched_mw']
 # Standalone price-domain model: all inputs are from this pair/case; old DA is diagnostic only.
 import pyomo.environ as p
 m=p.ConcreteModel('new_RT_combination_independent_research_PRICE');G=list(e['thermal']);U=list(c['batteries']);N=list(e['buses']);L=list(e['lines']);T=range(8);m.P=p.Var(G+U,T);m.energy=p.Var(U,range(9));m.angle=p.Var(N,T);m.flow=p.Var(L,T);m.vre=p.Var(N,T,domain=p.NonNegativeReals);m.curtail=p.Var(N,T,domain=p.NonNegativeReals);m.accident=p.Var(G,T,domain=p.NonNegativeReals);m.rows=p.ConstraintList();m.zero=p.Var(bounds=(0.,0.));m.zero.fix(0)
 mean=lambda vals:math.fsum(vals)/len(vals)
 price_refs={};mode_trace={};cost=0.;segments={};descs={};H=.25
 for g,d in e['thermal'].items():
  prev=x[f'power[{g},{first-1}]']if first else d['initial_power_mw']
  for q in T:
   ts=range(first+3*q,first+3*q+3);need(all(x[f'stable[{g},{t}]']>.5 for t in ts),'transition PRICE not implemented, do not invent eligibility')
   lo=mean([d['min_mw'][t]+agc[g]['down_mw'][str(t)]for t in ts]);hi=mean([d['max_mw'][t]-agc[g]['up_mw'][str(t)]for t in ts]);ref=mean([xr[f'power[{g},{t}]']for t in ts]);lb,ub=band(ref,lo,hi);m.P[g,q].setlb(lb);m.P[g,q].setub(ub);price_refs[g,q]=(ref,lo,hi)
   ru=min(d['ramp_up_mw_per_h'][t]for t in ts);rd=min(d['ramp_down_mw_per_h'][t]for t in ts);m.rows.add(m.P[g,q]-prev<=ru*H);m.rows.add(prev-m.P[g,q]<=rd*H);prev=m.P[g,q]
   m.accident[g,q].setub(int(c['reserve']['contingency_eligible'][g])*ru/6);m.rows.add(m.accident[g,q]<=hi-m.P[g,q])
   base=d['bid_base_mw'];quotes=[(base,mean([d['base_bid_yuan_per_h'][t]/base for t in ts]))]
   for z in d['segments']:quotes.append((z['width_mw'],mean([z['price_yuan_per_mwh'][t]for t in ts])))
   descs[g,q]=(1,quotes)
 for u,d in c['batteries'].items():
  eta=math.sqrt(d['roundtrip_efficiency']);initial=xr[f'battery.energy[{u},{first-1}]']if first else d['initial_energy_mwh'];m.energy[u,0].fix(initial);prev=x[f'battery.power[{u},{first-1}]']if first else d['initial_power_mw'];used={}
  for t in range(first):
   date=(day+timedelta(minutes=5*t)).date().isoformat();used[date]=used.get(date,0.)+(eta*xr[f'battery.charge[{u},{t}]']+xr[f'battery.discharge[{u},{t}]']/eta)/12
  for date,z in d['cycle_budget'].items():used[date]=used.get(date,0.)+z['used_internal_mwh']
  for q in T:
   ts=list(range(first+3*q,first+3*q+3));modes=[mode(xr,u,t)for t in ts];active=set(modes)-{'idle'};need(len(active)<=1,'mixed charge/discharge inside quarter unsupported');md=next(iter(active),'idle');sign=-1 if md=='charge'else 1;count=sum(z==md for z in modes)if md!='idle'else 0;cap=d['charge_max_mw'if md=='charge'else'discharge_max_mw']*count/3;lo,hi=(-cap,0.)if sign==-1 else(0.,cap);ref=mean([xr[f'battery.power[{u},{t}]']for t in ts]);lb,ub=band(ref,lo,hi);m.P[u,q].setlb(lb);m.P[u,q].setub(ub);price_refs[u,q]=(ref,lo,hi);mode_trace[u,q]=dict(mode=md,cell_modes=modes,active_fraction=count/3,initial_SOC=initial,used=used)
   coef=eta if sign==-1 else 1/eta;m.energy[u,q+1].setlb(d['energy_min_mwh']);m.energy[u,q+1].setub(d['energy_max_mwh']);m.rows.add(m.energy[u,q+1]==m.energy[u,q]-coef*m.P[u,q]*H);m.rows.add(m.P[u,q]-prev<=d['ramp_up_mw_per_h']*H);m.rows.add(prev-m.P[u,q]<=d['ramp_down_mw_per_h']*H);prev=m.P[u,q]
   if start+timedelta(minutes=15*(q+1))==datetime.fromisoformat(d['terminal_at']):m.rows.add(m.energy[u,q+1]==d['terminal_energy_mwh'])
   cv=d['charge_curve'if sign==-1 else'discharge_curve'];descs[u,q]=(sign,[(cv['breakpoints_mw'][k+1]-cv['breakpoints_mw'][k],v)for k,v in enumerate(cv['prices_yuan_per_mwh'])])
  dates={(start+timedelta(minutes=15*q)).date().isoformat()for q in T}
  for date in dates:
   need(date in d['cycle_budget'],'explicit dated budget');limit=2*d['energy_capacity_mwh']*d['cycle_budget'][date]['limit_cycles'];terms=[]
   for q in T:
    if (start+timedelta(minutes=15*q)).date().isoformat()==date:terms.append((-eta if mode_trace[u,q]['mode']=='charge'else 1/eta)*m.P[u,q]*H)
   m.rows.add(sum(terms)+m.zero<=limit-used.get(date,0.))
 keys=[(u,q,k)for(u,q),(sign,cv)in descs.items()for k in range(len(cv))];m.segment=p.Var(keys,domain=p.NonNegativeReals)
 for(u,q),(sign,cv)in descs.items():
  m.rows.add(sign*m.P[u,q]==sum(m.segment[u,q,k]for k in range(len(cv))))
  for k,(width,price)in enumerate(cv):m.segment[u,q,k].setub(width);cost+=sign*price*H*m.segment[u,q,k]
 load={};tie={}
 for b in N:
  for q in T:
   ts=range(first+3*q,first+3*q+3);load[b,q]=mean([e['load_mw'][b,t]for t in ts]);tie[b,q]=mean([e['physical_tie_mw'][b,t]for t in ts]);A=mean([e['renewable_mw'][b,t]for t in ts]);ref=mean([xr[f"renewable_power['{b}',{t}]"]for t in ts]);lb,ub=band(ref,0.,A);m.vre[b,q].setlb(lb);m.vre[b,q].setub(ub);m.curtail[b,q].setub(A);m.rows.add(m.vre[b,q]+m.curtail[b,q]==A);cost+=H*(-50*m.vre[b,q]+1000*m.curtail[b,q]);m.angle[b,q].setlb(-math.pi);m.angle[b,q].setub(math.pi)
 for b in e['reference_buses']:
  for q in T:m.angle[b,q].fix(0)
 for l,z in e['lines'].items():
  for q in T:
   ts=range(first+3*q,first+3*q+3);m.flow[l,q].setlb(max(z['lower_mw'][t]for t in ts));m.flow[l,q].setub(min(z['upper_mw'][t]for t in ts));m.rows.add(m.flow[l,q]==z['susceptance_mw_per_rad']*(m.angle[z['from_bus'],q]-m.angle[z['to_bus'],q]))
 def balance(_,b,q):return sum(m.P[g,q]for g in G if e['thermal'][g]['bus']==b)+sum(m.P[u,q]for u in U if c['batteries'][u]['bus']==b)+m.vre[b,q]+tie[b,q]-load[b,q]==sum(m.flow[l,q]for l,z in e['lines'].items()if z['from_bus']==b)-sum(m.flow[l,q]for l,z in e['lines'].items()if z['to_bus']==b)
 m.balance=p.Constraint(N,T,rule=balance)
 for q in T:
  m.rows.add(sum(price_refs[g,q][2]-m.P[g,q]for g in G)>=400);m.rows.add(sum(m.accident[g,q]for g in G)>=400)
 m.objective=p.Objective(expr=cost)
 payload,vs,cs=serialize_model(m,validate_options(dict(threads=1,time_limit=3600.,mip_rel_gap=.01,mip_abs_gap=0.,output_flag=False)));payload['options'].update(dual_feasibility_tolerance=1e-9,simplex_scale_strategy=0);payload.update(dual=True,warmstart=False,preparation_elapsed_s=0.);payload.pop('deadline',None)
 out=Path(output);out.mkdir(parents=True,exist_ok=False);names=dict(variables=[v.name for v in vs],constraints=[z.name for z in cs]);context=dict(schema='gd.new_RT.independent_PRICE.research.v1',run_id=a['run_id'],window=a['window'],delivery_tick=first,day_quarter=first//3,start=start.isoformat(),end=(start+timedelta(minutes=15)).isoformat(),cutoff=r['cutoff'],SCED_response_sha256=a['SCED_response_sha256'],RT_dual_used=False,formal_market_price=None,official_route_status='interpretation_unresolved_not_official_infeasibility',current_mode_DA_domain_intersection_conflicts=conflicts,current_mode_binding_is_research_interpretation=True,boundary_mode=boundary,research_boundary=('current accepted SCED reference +/-0.1, new SCUC modes, current issued-prefix SOC/dated usage; NOT official DA-reference pricing'if boundary=='current_sced_research'else'original DA SCED reference +/-0.1 and DA storage pricing modes/SOC/dated usage; current projected previous power for ramps; independent R61-style research PRICE, NOT current RT execution modes or formal price'),delta=.1,quarter_SOC_domain=True,five_minute_dispatch_not_modified=True,hard_network_no_official_slack_parameters=True,original_400_each=True,source_pins={str(Path(z).resolve()):sha(z)for z in [pair,case_path,agc_path,ph,da/'ARTIFACTS.json',Path(__file__)]},native_calls=0)
 for f,z in [('template.json',payload),('names.json',names),('CONTEXT.json',context),('MODE_TRACE.json',{str(k):v for k,v in mode_trace.items()})]:write(out/f,z)
 write(out/'READY.json',dict(columns=len(vs),rows=len(cs),context=context,artifact_pins={z.name:sha(z)for z in out.iterdir()},native_calls=0));return dict(columns=len(vs),rows=len(cs),official_conflicts=len(conflicts),native_calls=0)
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--pair',required=True);p.add_argument('--case',required=True);p.add_argument('--agc',required=True);p.add_argument('--da-run',required=True);p.add_argument('--output',required=True);p.add_argument('--research-current-sced',action='store_true');p.add_argument('--boundary',default='current_sced_research');a=p.parse_args();print(json.dumps(prepare(a.pair,a.case,a.agc,a.da_run,a.output,research_current_sced=a.research_current_sced,boundary=a.boundary)))
