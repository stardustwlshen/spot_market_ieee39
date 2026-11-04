"""Conditional RT probes: same exact exported states and DA commitments/anchors.
Force first reserve shortage zero and no energy/network slack; no relaxation.
This is a local feasibility test, not a full-day counterfactual or new policy.
"""
import argparse,json,sys,copy,traceback
from pathlib import Path
import numpy as np,pandas as pd
import pyomo.environ as pyo
ap=argparse.ArgumentParser();ap.add_argument('--source-run',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--code-root',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--periods',default='34,87');args=ap.parse_args()
ROOT=args.code_root;sys.path.insert(0,str(ROOT/'src'))
from smm.config import load_config
from smm.case import build_da_case
from smm.network import build_network
from smm import rolling,scuc
from smm.results import reserve_snapshot
P=args.source_run;O=args.out;O.mkdir(exist_ok=False,parents=True)
cfg=load_config(root=P/'scenario_snapshot');c=build_da_case(cfg,build_network(cfg),pd.read_csv(P/'profiles.csv'),json.loads((P/'resolved_bids.json').read_text()))
stages=json.loads((P/'stage_diagnostics.json').read_text());da=stages['da']['energy_sced'];hyd=json.loads((P/'hydro_window_evidence.json').read_text())['da'];states=json.loads((P/'rolling_actual_states.json').read_text())['windows'];plan={}
def assign(key,uid,t,v):
 plan.setdefault(key,{}).setdefault(uid,np.full(96,np.nan))[t]=v
for row in da['integer_values']:
 idx=row['index']; key=row['variable'];uid=tuple(idx[:-1]) if len(idx)>2 else idx[0];assign(key,uid,idx[-1],row['value'])
for row in da['units']:
 uid,t,typ=row['unit'],row['period'],row['type']
 if typ in ['coal','gas_gt','gas_ccgt']:assign('P',uid,t,row['P_mw'])
 if typ in ['wind','pv']:assign('Pw',uid,t,row['P_mw'])
 if typ=='storage':
  for key,field in [('E','E_mwh'),('Pd','P_dis_mw'),('Pc','P_ch_mw')]:assign(key,uid,t,row[field])
 if typ=='pumped':
  for key,field in [('Vu','V_up_1e4m3'),('Pg','P_gen_mw'),('Pp','P_pump_mw')]:assign(key,uid,t,row[field])
for key in ['Ph','Vh','Qout']:plan[key]={uid:np.array(arr) for uid,arr in hyd[key].items()}
assert all(np.isfinite(arr).all() for group in plan.values() for arr in group.values())
original=rolling.clear_window
for t in [int(x) for x in args.periods.split(',')]:
 result={'period':t,'time':f'{t//4:02}:{t%4*15:02}','scope':'Exact exported state/DA plan. Impose first reserve shortage=0 and shed/surplus/network slacks=0 throughout current window. Future reserve short allowed and original objective retained. Not full-day validation.','base_RT_solver':cfg.mode['realtime']}
 captured={}
 def probe(case,log=lambda *a:None):
  m=scuc.build_model(case);captured['model']=m;m.audit_zero=pyo.ConstraintList();m.audit_zero.add(m.sR_up[0]==0)
  for k in m.T:
   m.surplus[k].fix(0)
   for n in m.N:m.shed[n,k].fix(0)
   for l in m.L:m.sl[l,k].fix(0)
  m._solver_budget=scuc.SolverBudget(total_s=float(cfg.mode['realtime']['time_limit_s']),reserve_lp_s=10.)
  info=scuc.solve_scuc(m);scuc.fix_integers(m);lp=scuc.solve_sced(m)
  captured.update(info=info,lp=lp,snapshot=reserve_snapshot(m),slack=scuc.slack_summary(m),plan=rolling.snapshot(m),budget=m._solver_budget.report())
  # Stop after retaining conditional result, before unneeded pricing/rolling reporting.
  raise StopIteration('diagnostic solve captured')
 rolling.clear_window=probe
 s=copy.deepcopy(states[t])
 for h,arr in s['hydro_hist'].items():s['hydro_hist'][h]=np.r_[arr,np.full(96-t,np.nan)]
 for uid,h in s['pumped_hist'].items():
  for key,arr in h.items():h[key]=np.r_[arr,np.full(96-t,np.nan)]
 try:rolling.run_rt_period(c,plan,plan,cfg,rolling.SysState(**s),t)
 except Exception as e:
  if 'info' in captured:result.update(status='solved',mip=captured['info'],sced=captured['lp'],reserve=captured['snapshot'],slack=captured['slack'],solver_budget=captured['budget'],first_units={key:{u:float(arr[0]) for u,arr in captured['plan'][key].items()} for key in ['P','Ph','Pd','Pc','Pg','Pp','E','Vu','ngen','npump']})
  else:result.update(status='infeasible' if 'kInfeasible' in str(e) else 'failed_or_unknown',error=str(e),solver_budget=captured['model']._solver_budget.report() if captured.get('model') else None)
 finally:rolling.clear_window=original
 (O/f't{t}_zero_first_shortage.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,default=float));print(json.dumps({k:v for k,v in result.items() if k in ['period','status','mip','sced','slack','error']},ensure_ascii=False),flush=True)
