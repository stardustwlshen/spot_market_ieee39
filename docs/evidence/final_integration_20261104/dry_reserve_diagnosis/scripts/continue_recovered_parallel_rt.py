"""Recover exact recorded formal DA physics, certify, then execute new RT96.
Never runs DA SCUC. Local trusted checkpoint only; original archives are read-only.
"""
from pathlib import Path
import argparse,copy,hashlib,json,pickle,shutil,sys,time,traceback
import numpy as np,pandas as pd,pyomo.environ as pyo
ap=argparse.ArgumentParser();ap.add_argument('--trial',type=Path,required=True);ap.add_argument('--replay',type=Path,required=True);ap.add_argument('--known-formal',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--code-root',type=Path,required=True);a=ap.parse_args();sys.path.insert(0,str(a.code_root/'src'))
from smm import scuc,rolling,full,regulation,results as R
from smm.da import DAResult,_tables
from smm.auxiliary_pricing import run_auxiliary_pricing,numeric_diagnostics
from smm.live_runner import atomic_json,capture_provenance,ProgressWriter
from smm.pipeline import write_outputs
O=a.out;O.mkdir(exist_ok=False,parents=True);shutil.copytree(a.trial/'scenario_snapshot',O/'scenario_snapshot');shutil.copytree(a.trial/'da',O/'original_da_readonly_reference')
original_hashes={str(f.relative_to(a.trial)):hashlib.sha256(f.read_bytes()).hexdigest() for f in a.trial.rglob('*') if f.is_file()}
checkpoint=a.replay/'replayed_plan_checkpoint.pkl';assert hashlib.sha256(checkpoint.read_bytes()).hexdigest()==checkpoint.with_suffix('.pkl.sha256').read_text().strip();saved=pickle.loads(checkpoint.read_bytes());case=saved['case'];cfg=case.cfg;cfg.root=O/'scenario_snapshot';assert cfg.base['solver']['highs_extra']=={} and cfg.base['solver']['warm_start'] is False
assert cfg.mode['realtime']['mip_rel_gap']==.01 and cfg.mode['realtime']['time_limit_s']==180 and cfg.mode['realtime']['storage']['anchor']=='two_hour_eq'
trial=json.loads((a.trial/'result.json').read_text());stages=json.loads((a.trial/'da/stage_diagnostics.json').read_text())['da'];archived_aux=json.loads((a.trial/'da/auxiliary_pricing_evidence.json').read_text())['da'];known=json.loads(a.known_formal.read_text());allvals=json.loads((a.replay/'all_replayed_formal_variables.json').read_text());m=scuc.build_model(case);regulation.prepare_energy(m)
def idx(row):return tuple(row['index']) if isinstance(row['index'],list) else row['index']
for row in allvals:getattr(m,row['variable'])[idx(row)].set_value(row['value'])
scuc.fix_integers(m);previous_fixed={v.name:v.fixed for v in m.component_data_objects(pyo.Var)}
for row in known:getattr(m,row['variable'])[idx(row)].fix(row['value'])
tstart=time.perf_counter();recovery_info=scuc.solve_sced(m)
max_recorded=max(abs(pyo.value(getattr(m,r['variable'])[idx(r)])-r['value']) for r in known)
constraint_error=0.;bounds_error=0.
for con in m.component_data_objects(pyo.Constraint,active=True):
 v=pyo.value(con.body);constraint_error=max(constraint_error,pyo.value(con.lower)-v if con.has_lb() else 0.,v-pyo.value(con.upper) if con.has_ub() else 0.)
for v in m.component_data_objects(pyo.Var):bounds_error=max(bounds_error,v.lb-pyo.value(v) if v.has_lb() else 0.,pyo.value(v)-v.ub if v.has_ub() else 0.)
int_error=max(abs(pyo.value(getattr(m,r['variable'])[idx(r)])-round(pyo.value(getattr(m,r['variable'])[idx(r)]))) for r in stages['energy_sced']['integer_values'])
objective_error=abs(recovery_info['objective']-trial['sced']['objective'])
assert max_recorded<=1e-8 and constraint_error<=1e-6 and bounds_error<=1e-6 and int_error<=1e-8 and objective_error<=1e-5
# Remove recovery-only fixes after obtaining a complete valid formal solution.
for v in m.component_data_objects(pyo.Var):
 if not previous_fixed[v.name]:v.unfix()
# Original formal prices are archived/reproduced, not the meaningless duals of the recovery-fixed LP.
dispatch=saved['formal_prices'];dt=cfg.dt
for t in m.T:
 m.dual[m.c_bal[t]]=float(dispatch['energy'][t])*dt;m.dual[m.c_Rup[t]]=float(dispatch['reserve_up_price'][t])*dt
 for k,n in enumerate(case.net.buses):m.dual[m.c_inj[n,t]]=float(dispatch['lmp'][k,t])*dt
 for k,l in enumerate(case.net.branches.branch_id):
  m.dual[m.c_fup[l,t]]=-float(dispatch['mu_plus'][k,t])*dt;m.dual[m.c_fdn[l,t]]=-float(dispatch['mu_minus'][k,t])*dt
# Recreate the separate pricing model, never substitute its physical quantities.
aux=run_auxiliary_pricing(m);prices=R.clamp_prices(aux['auxiliary_prices']['raw'],cfg.mode['price_limits']['clear_min'],cfg.mode['price_limits']['clear_max'])
price_error=float(abs(np.asarray(prices['lmp'])-np.asarray(archived_aux['auxiliary_prices']['raw']['lmp'])).max());assert price_error<=1e-6,('Auxiliary price mismatch; do not silently change settlements',price_error)
tables=_tables(m,prices,cfg,dispatch_prices=dispatch,pricing_source='bounded_auxiliary_pricing');tables['lines']['flow_source']='standard_sced';tables['lines']['dual_source']='bounded_auxiliary_pricing'
file_map={'units':'da_unit_awards.csv','prices':'da_nodal_prices.csv','lines':'da_line_flows.csv','storage':'da_storage.csv','hydro':'da_hydro.csv','pumped':'da_pumped.csv','system':'da_system.csv'};csv_errors={}
for name,filename in file_map.items():
 original=pd.read_csv(a.trial/'da'/filename);new=pd.read_csv(__import__('io').StringIO(tables[name].to_csv(index=False,float_format='%.4f')));assert list(new.columns)==list(original.columns),(name,'columns')
 numeric=original.select_dtypes(include='number').columns;numeric_error=float(np.nanmax(abs(new[numeric].to_numpy()-original[numeric].to_numpy()))) if len(numeric) else 0.
 others=original.drop(columns=numeric).fillna('');assert others.equals(new.drop(columns=numeric).fillna('')),(name,'nonnumeric')
 assert numeric_error<=1e-7,(name,numeric_error);csv_errors[name]=numeric_error
snap=rolling.snapshot(m);plan_error=max(float(abs(snap[k][u]-saved['formal_snapshot'][k][u]).max()) for k in ['P','u','Pw','Ph','delta','Vh','Qout','Pd','Pc','E','u_ch','u_dis','Pg','Pp','xg','xp','ngen','npump','Vu'] for u in snap[k]);assert plan_error<=1e-8
certificate=dict(status='verified',scope='Exact recorded formal physical DA plan recovered; 12480 internal variable historical identities cannot all be proved.',recorded_formal_variable_count=len(known),unrecorded_historical_internal_variable_count=12480,all_model_variable_count=len(allvals),recorded_formal_max_error=float(max_recorded),plan_max_error=plan_error,objective_error=float(objective_error),constraint_error=float(constraint_error),bounds_error=float(bounds_error),integer_error=float(int_error),formal_csv_numeric_errors=csv_errors,auxiliary_price_error=price_error,DA_SCUC_reexecuted=False,original_scuc_time_s=trial['scuc']['time_s'],original_sced_time_s=trial['sced']['time_s'],integer_only_replay_LP_time_s=saved['formal_sced_info']['time_s'],recovery_fixed_formal_LP=recovery_info,RT_highs_extra=cfg.base['solver']['highs_extra'],RT_gap=cfg.mode['realtime']['mip_rel_gap'],RT_time_limit_s=cfg.mode['realtime']['time_limit_s'],pricing_reconstruction_time_s=aux['solve_info']['time_s'])
atomic_json(O/'recovery_certificate.json',certificate)
da=DAResult(case,trial['scuc'],trial['sced'],archived_aux,scuc.slack_summary(m),prices,tables,R.cost_breakdown(m),m,pricing_model=aux['model'],dispatch_prices=dispatch,stage_comparison=trial['stage_comparison'],stage_diagnostics=stages)
write_outputs(da,O/'da',make_plots=False);atomic_json(O/'resolved_bids.json',case.bids);case.profiles.to_csv(O/'profiles.csv',index=False);capture_provenance(O,cfg.root,source_root=a.code_root)
atomic_json(O/'all_recovered_formal_variables.json',[dict(variable=v.name,value=float(pyo.value(v)),fixed=bool(v.fixed)) for v in m.component_data_objects(pyo.Var)])
(O/'formal_plan_checkpoint.pkl').write_bytes(pickle.dumps(dict(case=case,plan=snap,all_variables=allvals,certificate=certificate)));(O/'formal_plan_checkpoint.pkl.sha256').write_text(hashlib.sha256((O/'formal_plan_checkpoint.pkl').read_bytes()).hexdigest())
progress=ProgressWriter(O,total=96);atomic_json(O/'continuation_status.json',dict(status='running_RT',DA_reused=True,new_DA_SCUC=False,RT_executed=0));print('RECOVERY VERIFIED',json.dumps(certificate),flush=True)
original_run_da=full.run_da
def reuse_da(*args,**kwargs):return da
full.run_da=reuse_da;start=time.perf_counter()
try:
 result=full.run_full(cfg,case,periods=96,progress_callback=progress);summary=full.write_full_outputs(result,O,make_plots=False)
 atomic_json(O/'continuation_timing.json',dict(DA_reused=True,new_DA_SCUC=False,original_scuc_s=trial['scuc']['time_s'],original_sced_s=trial['sced']['time_s'],recovery_LP_s=recovery_info['time_s'],new_RT_s=result['time_s']['realtime'],DA_injection_call_s=result['time_s']['da'],note='Near-zero DA injection time is reuse, not a fresh day-ahead solve.',new_full_phase_times=result['time_s']))
 assert original_hashes=={str(f.relative_to(a.trial)):hashlib.sha256(f.read_bytes()).hexdigest() for f in a.trial.rglob('*') if f.is_file()}
 atomic_json(O/'continuation_status.json',dict(status='completed',executed_periods=summary['executed_periods'],DA_reused=True,new_DA_SCUC=False,original_DA_archive_unchanged=True,elapsed_new_RT_and_outputs_s=time.perf_counter()-start));print('CONTINUATION COMPLETE',flush=True)
except Exception as e:
 atomic_json(O/'continuation_status.json',dict(status='failed',error_type=type(e).__name__,error=str(e),DA_reused=True,new_DA_SCUC=False,executed_periods=progress.state.get('executed_periods',0)));traceback.print_exc();raise
finally:full.run_da=original_run_da
