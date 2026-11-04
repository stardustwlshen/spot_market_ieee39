"""Compare PV sizes and siting with identical profiles/offers and hard slack limits.

LP relaxation only, not executed dispatch. Source scenes are never modified.
Use --root for an archived input snapshot when reproducing a historical study.
"""
from pathlib import Path
import argparse,json,copy,time,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
import pyomo.environ as pyo
from smm.config import load_config,ROOT
from smm.forecast import generate_profiles
from smm.bids import load_bids,default_bids,effective_bids
from smm.case import build_da_case
from smm.network import build_network
from smm import scuc,regulation

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,default=ROOT)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
out=args.output;out.mkdir(parents=True,exist_ok=False)
records=[]
for add,buses in [(0,()),(1000,(4,8)),(1500,(33,33)),(1500,(4,27)),(1500,(8,27)),(1500,(4,8)),(2000,(4,8))]:
 for season,renew,load in [('wet',1.,1.),('wet',1.2,.9)]:
  start=time.perf_counter();cfg=load_config(root=args.root,season=season);cfg.units['renewable']=[u for u in cfg.units['renewable'] if u['id'] not in {'PV2','PV3'}];cfg.base['forecast']={'model':'multiscale_v2'};cfg.base['solver']['time_limit_s']=30
  for uid,bus in zip(['PV2','PV3'],buses):cfg.units['renewable'].append(dict(id=uid,type='pv',bus=bus,cap=add/2,profile='pv'))
  bids=load_bids(cfg,version=2);dflt=default_bids(cfg)
  for uid in ['PV2','PV3']:
   if uid in dflt['units']:bids['units'][uid]=dflt['units'][uid]
  profiles=generate_profiles(cfg)
  for stage in ['actual','da','id','rt']:
   profiles['load_'+stage]*=load
   for r in cfg.units['renewable']:profiles[r['id']+'_'+stage]=np.minimum(profiles[r['id']+'_'+stage]*renew,r['cap'])
  case=build_da_case(cfg,build_network(cfg),profiles,effective_bids(cfg,bids));m=scuc.build_model(case);regulation.prepare_energy(m)
  for name in ['shed','surplus','sl','sR_up','sR_dn']:
   for v in getattr(m,name).values():v.fix(0.)
  pyo.TransformationFactory('core.relax_integer_vars').apply_to(m);opt=scuc._solver(cfg,mip=False);res=opt.solve(m,load_solutions=False)
  term=str(res.solver.termination_condition);rec=dict(add_pv_mw=add,buses=buses,season=season,re_multiplier=renew,load_multiplier=load,termination=term)
  if term=='optimal':
   opt.load_vars();md=m._md;ren=list(md.ren);pv=[u for u in ren if md.ren[u]['type']=='pv'];wind=[u for u in ren if md.ren[u]['type']=='wind']
   rates=case.net.branches.set_index('branch_id').rate_mw.to_dict()
   gen=lambda ids:sum(pyo.value(m.Pw[u,t]) for u in ids for t in m.T)*cfg.dt
   avail=lambda ids,stage:sum(profiles[u+'_'+stage].sum() for u in ids)*cfg.dt
   thermal=sum(u['pmax'] for u in cfg.units['thermal']);hydro=sum(u['n_units']*u['unit_pmax'] for u in cfg.units['hydro']);rec.update(pv_cap=652+add,ren_installed_share=(2256+add)/(thermal+hydro+2256+add),
    actual_ren_available_mwh=avail(ren,'actual'),actual_pv_available_mwh=avail(pv,'actual'),actual_load_mwh=profiles.load_actual.sum()*cfg.dt,
    actual_re_available_to_demand_ratio=avail(ren,'actual')/(profiles.load_actual.sum()*cfg.dt),
    da_pv_available_mwh=avail(pv,'da'),da_pv_dispatch_mwh=gen(pv),da_pv_curtail_mwh=avail(pv,'da')-gen(pv),da_wind_curtail_mwh=avail(wind,'da')-gen(wind),
    max_loading=max(abs(pyo.value(m.flow[l,t]))/float(rates[l]) for l in m.L for t in m.T),lp_objective=pyo.value(m.obj))
  rec['scope']='integer_relaxed_economic_model_with_all_physical_slacks_fixed_zero_not_executable_dispatch'
  rec['elapsed_s']=time.perf_counter()-start;records.append(rec);(out/'results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2,default=float));print(rec,flush=True)
