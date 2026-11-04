"""Re-solve only formal DA LP with archived integer values. Never runs SCUC or RT."""
from pathlib import Path
import argparse,json,pickle,shutil,sys,time,hashlib
import numpy as np,pyomo.environ as pyo
ap=argparse.ArgumentParser();ap.add_argument('--trial',type=Path,required=True);ap.add_argument('--known-formal',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--code-root',type=Path,required=True);a=ap.parse_args();sys.path.insert(0,str(a.code_root/'src'))
from smm.config import load_config
from smm.pipeline import prepare
from smm import scuc,rolling,regulation
from smm.da import _tables
from smm.results import clamp_prices
from smm.live_runner import atomic_json
O=a.out;O.mkdir(exist_ok=False,parents=True);shutil.copytree(a.trial/'scenario_snapshot',O/'scenario_snapshot')
cfg=load_config(root=O/'scenario_snapshot');assert cfg.base['solver']['highs_extra']=={} and cfg.mode['realtime']['mip_rel_gap']==.01 and cfg.mode['realtime']['time_limit_s']==180
case=prepare(cfg,read_only=True,verbose=False);m=scuc.build_model(case);regulation.prepare_energy(m)
stages=json.loads((a.trial/'da/stage_diagnostics.json').read_text())['da'];formal=stages['energy_sced'];known=json.loads(a.known_formal.read_text());reference={(r['variable'],tuple(r['index']) if isinstance(r['index'],list) else r['index']):r['value'] for r in known}
for row in formal['integer_values']:
 idx=tuple(row['index']) if isinstance(row['index'],list) else row['index'];getattr(m,row['variable'])[idx].set_value(row['value'])
scuc.fix_integers(m);start=time.perf_counter();info=scuc.solve_sced(m);snap=rolling.snapshot(m)
diffs=[]
for row in known:
 idx=tuple(row['index']) if isinstance(row['index'],list) else row['index'];actual=float(pyo.value(getattr(m,row['variable'])[idx]));diffs.append(dict(variable=row['variable'],index=row['index'],reference=row['value'],replayed=actual,difference=actual-row['value']))
max_by={}
for r in diffs:max_by[r['variable']]=max(max_by.get(r['variable'],0.),abs(r['difference']))
# Recover original flows solely from recorded formal generation/storage/shed and frozen nodal data.
refinj={n:-np.array(case.load_bus.get(n,np.zeros(cfg.T)),float) for n in m.N}
for group,var in [('thermal','P'),('renewable','Pw'),('hydro','Ph'),('storage','Pd'),('pumped','Pg')]:
 for unit in cfg.units.get(group,[]):
  uid,bus=unit['id'],unit['bus'];refinj[bus]+=np.array([reference[(var,(uid,t))] for t in m.T])
  ch={'storage':'Pc','pumped':'Pp'}.get(group)
  if ch:refinj[bus]-=np.array([reference[(ch,(uid,t))] for t in m.T])
for uid,(bus,arr) in case.tie.items():refinj[bus]+=arr
for n in m.N:refinj[n]+=np.array([reference[('shed',(n,t))] for t in m.T])
refinj[case.net.ref_bus]-=np.array([reference[('surplus',t)] for t in m.T]);ref_flow=case.net.ptdf@np.array([refinj[n] for n in case.net.buses]);flow=np.array([snap['flow'][l] for l in case.net.branches.branch_id]);flow_delta=float(abs(ref_flow-flow).max())
objective_ref=float(formal['summary']['objective']);integer_error=max((abs(r['difference']) for r in diffs if r['variable'] in scuc.INT_VARS),default=0.)
allvals=[dict(variable=var.name,index=list(idx) if isinstance(idx,tuple) else idx,value=float(pyo.value(var[idx]))) for var in m.component_objects(pyo.Var,active=True) for idx in var];viol=[]
for con in m.component_data_objects(pyo.Constraint,active=True):
 value=float(pyo.value(con.body));err=max(float(pyo.value(con.lower))-value if con.has_lb() else 0.,value-float(pyo.value(con.upper)) if con.has_ub() else 0.,0.)
 if err>1e-6:viol.append(dict(constraint=con.name,violation=err))
prices=clamp_prices(scuc.extract_prices(m),cfg.mode['price_limits']['clear_min'],cfg.mode['price_limits']['clear_max']);tables=_tables(m,prices,cfg)
for name,table in tables.items():table.to_csv(O/f'replayed_{name}.csv',index=False)
report=dict(scope='Reoptimized formal SCED with identical archived integers. Original continuous quantities not forced; no DA MIP and no RT executed.',source_trial=str(a.trial),LP=info,original_formal_objective=objective_ref,objective_difference=info['objective']-objective_ref,integer_max_difference=integer_error,max_difference_by_recorded_variable=max_by,derived_original_flow_max_difference_mw=flow_delta,flow_reference_source='Full precision archived formal powers/slacks plus frozen nodal loads/tie/PTDF; original flow CSV only has4decimal places.',all_recorded_formal_values_match_1e_minus6=max(max_by.values())<=1e-6,all_critical_plan_values_match_1e_minus6=max(max_by[x] for x in ['P','u','Pw','Ph','delta','V','Qspill','Pd','Pc','E','u_ch','u_dis','Pg','Pp','xg','xp','ngen','npump','Vu'])<=1e-6,constraint_violations_over_1e_minus6=viol,slack=scuc.slack_summary(m),RT_default_highs_extra=cfg.base['solver']['highs_extra'],rt_mip_gap=cfg.mode['realtime']['mip_rel_gap'],rt_time_limit_s=cfg.mode['realtime']['time_limit_s'],elapsed_s=time.perf_counter()-start,all_variables_now_saved=len(allvals))
atomic_json(O/'recovery_audit.json',report);atomic_json(O/'recorded_variable_differences.json',diffs);atomic_json(O/'all_replayed_formal_variables.json',allvals)
# Trusted locally produced numeric checkpoint. Do not load external untrusted pickle files.
checkpoint=dict(case=case,formal_snapshot=snap,formal_prices=prices,formal_tables=tables,formal_sced_info=info,original_parallel_scuc_info=json.loads((a.trial/'result.json').read_text())['scuc'],original_stages=stages)
(O/'replayed_plan_checkpoint.pkl').write_bytes(pickle.dumps(checkpoint));(O/'replayed_plan_checkpoint.pkl.sha256').write_text(hashlib.sha256((O/'replayed_plan_checkpoint.pkl').read_bytes()).hexdigest())
print(json.dumps(report,indent=2),flush=True)
