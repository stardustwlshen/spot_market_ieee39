"""Independent disk-only checks of a complete DA+96RT archive, not a full physics certificate."""
import argparse, hashlib, json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml
p=argparse.ArgumentParser();p.add_argument('directory',type=Path);a=p.parse_args();root=a.directory
report={'scope':'disk_reloaded_complete_96_period_evidence;not_AC_Nminus1_or_all_constraints','errors':[],'checks':{},'metrics':{}}
def check(name,ok):
 report['checks'][name]=bool(ok)
 if not ok:report['errors'].append(name)
def metric(name,value):report['metrics'][name]=float(value)
def read(name):
 path=root/name
 if not path.exists():raise ValueError(f'Missing {name}')
 return pd.read_csv(path)
try:
 cfg=yaml.safe_load((root/'scenario_snapshot/config/base.yaml').read_text())
 units=yaml.safe_load((root/'scenario_snapshot/config/units.yaml').read_text())
 T=int(cfg['periods']);dt=float(cfg['dt_h']);tol=.001
 check('expected_full96',T==96)
 check('supply_mode',cfg.get('tie_line_mode','supply')=='supply')
 provenance=json.loads((root/'run_provenance.json').read_text())
 check('frozen_input_files_unchanged_since_invocation',all((root/'scenario_snapshot'/name).is_file() and hashlib.sha256((root/'scenario_snapshot'/name).read_bytes()).hexdigest()==digest for name,digest in provenance['inputs']['files'].items()))
 manifest=json.loads((root/'output_manifest.json').read_text());summary=json.loads((root/'summary.json').read_text())
 check('scope_full_day',manifest['executed_periods']==T and manifest['completed_scope']=='full_day')
 check('every_manifest_artifact_present_and_hash_matches',all(x['present'] and (root/x['file']).is_file() and hashlib.sha256((root/x['file']).read_bytes()).hexdigest()==x['sha256'] for x in manifest['artifacts']))
 profiles=read('profiles.csv');branches=pd.read_csv(root/'scenario_snapshot/data/case39/branches.csv');buses=pd.read_csv(root/'scenario_snapshot/data/case39/buses.csv').bus.tolist()
 rates=branches.set_index('branch_id').rate_mw.copy()
 for branch,factor in cfg.get('line_scale',{}).items():rates.loc[branch]*=factor
 rates*=cfg.get('line_capacity_multiplier',1.)
 # Separate reconstruction of the project's documented unit-tap lossless DC model.
 ix={int(b):i for i,b in enumerate(buses)};A=np.zeros((len(branches),len(buses)))
 for k,r in enumerate(branches.itertuples()):A[k,ix[int(r.from_bus)]]=1;A[k,ix[int(r.to_bus)]]=-1
 bd=np.diag(1/branches.x_pu.to_numpy());bb=A.T@bd@A;keep=[i for b,i in ix.items() if b!=31];inverse=np.zeros(bb.shape);inverse[np.ix_(keep,keep)]=np.linalg.inv(bb[np.ix_(keep,keep)]);ptdf=bd@A@inverse
 tables={}
 for stage in ['da','rt']:
  names={'units':'da_unit_awards.csv','prices':'da_nodal_prices.csv','lines':'da_line_flows.csv','system':'da_system.csv','storage':'da_storage.csv','pumped':'da_pumped.csv','reserve_system':'da_reserve_system.csv'} if stage=='da' else {k:f'rt_{k}.csv' for k in ['units','prices','lines','system','storage','pumped','reserve_system']}
  tab={k:read(v) for k,v in names.items()};tables[stage]=tab
  for k,key in [('units','unit'),('prices','bus'),('lines','line'),('storage','unit'),('pumped','unit')]:
   f=tab[k];check(f'{stage}_{k}_no_duplicate_period_keys',not f.duplicated([key,'period']).any())
   expected=({int(x) for x in buses} if k=='prices' else set(branches.branch_id) if k=='lines' else {u['id'] for g in (('storage',) if k=='storage' else ('pumped',) if k=='pumped' else ('thermal','renewable','hydro','storage','pumped','tie')) for u in units.get(g,[]) if u.get('enabled',True)})
   check(f'{stage}_{k}_complete_period_product',set(zip(f[key],f.period))=={(x,t) for x in expected for t in range(T)})
  check(f'{stage}_system_periods',tab['system'].period.tolist()==list(range(T)))
  check(f'{stage}_price_count',len(tab['prices'])==T*len(buses));check(f'{stage}_line_count',len(tab['lines'])==T*len(branches))
  power=tab['units'].groupby('period').P_mw.sum().reindex(range(T)).to_numpy()
  system=tab['system'].set_index('period').reindex(range(T))
  balance=power+system.shed_mw.to_numpy()-system.surplus_mw.to_numpy()-system.load_mw.to_numpy()
  metric(f'{stage}_balance_max_mw',np.max(np.abs(balance)));check(f'{stage}_balance',np.isfinite(balance).all() and np.max(np.abs(balance))<=tol)
  flow=tab['lines'].pivot(index='line',columns='period',values='flow_mw').reindex(branches.branch_id).to_numpy()
  injections=tab['prices'].pivot(index='bus',columns='period',values='inj_mw').reindex(buses).to_numpy()
  metric(f'{stage}_ptdf_reconstruction_max_mw',np.max(np.abs(flow-ptdf@injections)));check(f'{stage}_ptdf_reconstruction',np.max(np.abs(flow-ptdf@injections))<=tol)
  overload=np.maximum(np.abs(flow)-rates.reindex(branches.branch_id).to_numpy()[:,None],0)
  metric(f'{stage}_line_overload_max_mw',overload.max());metric(f'{stage}_shed_mwh',system.shed_mw.sum()*dt);metric(f'{stage}_surplus_mwh',system.surplus_mw.sum()*dt)
  rs=tab['reserve_system'];metric(f'{stage}_reserve_up_short_max_mw',rs.R_up_short.max());metric(f'{stage}_reserve_up_short_mwh',rs.R_up_short.sum()*dt)
  if 'curtail_mw' in tab['units']:metric(f'{stage}_curtail_mwh',tab['units'].curtail_mw.sum()*dt)
  if 'energy_mwh' in tab['units']:check(f'{stage}_energy_mwh',np.max(np.abs(tab['units'].energy_mwh-tab['units'].P_mw*dt))<=tol)
 for kind,col in [('storage','E_mwh'),('pumped','V_up')]:
  da=tables['da'][kind].set_index(['unit','period']);rt=tables['rt'][kind].set_index(['unit','period'])
  selected=[key for key in rt.index if (key[1]+1)%8==0]
  err=np.max(np.abs(rt.loc[selected,col].to_numpy()-da.loc[selected,col].to_numpy())) if selected else 0
  metric(f'{kind}_hard_anchor_max_error',err);check(f'{kind}_hard_anchor_at_all_12_times',err<=tol and len(selected)==12*len(rt.index.get_level_values(0).unique()))
 rt=tables['rt'];metric('actual_load_mwh',rt['system'].load_mw.sum()*dt)
 for kind in ['wind','pv']:
  metric('rt_'+kind+'_generation_mwh',rt['units'].loc[rt['units'].type==kind,'P_mw'].sum()*dt)
  metric('rt_'+kind+'_curtail_mwh',rt['units'].loc[rt['units'].type==kind,'curtail_mw'].sum()*dt)
 metric('rt_RE_generation_over_actual_demand_fraction',rt['units'].loc[rt['units'].type.isin(['wind','pv']),'P_mw'].sum()/rt['system'].load_mw.sum())
 check('rt_load_equals_actual_boundary',np.max(np.abs(rt['system'].load_mw-profiles.load_actual))<=tol)
 tieids={u['id'] for u in units.get('tie',[]) if u.get('enabled',True)}
 for uid in tieids:
  d=rt['units'][rt['units'].unit==uid].sort_values('period');check(f'tie_{uid}_once_each_period',len(d)==T and d.period.tolist()==list(range(T)));check(f'tie_{uid}_matches_plan',np.max(np.abs(d.P_mw.to_numpy()-profiles[f'{uid}_sched'].to_numpy()))<=tol)
 sto={s['id']:s for s in units['storage'] if s.get('enabled',True)}
 for uid,d in rt['storage'].groupby('unit'):
  d=d.sort_values('period');s=sto[uid];prev=np.r_[s['soc0']*s['ecap'],d.E_mwh.to_numpy()[:-1]]
  err=d.E_mwh.to_numpy()-prev-(s['eta_c']*d.P_ch.to_numpy()-d.P_dis.to_numpy()/s['eta_d'])*dt
  metric(uid+'_storage_energy_equation_max_mwh',np.max(np.abs(err)));check(uid+'_storage_energy_equation',np.max(np.abs(err))<=tol);check(uid+'_charge_discharge_exclusive',not ((d.P_ch>tol)&(d.P_dis>tol)).any())
 solver=read('solver_windows.csv');check('solver_97_records',len(solver)==T+1 and (solver.stage=='DA').sum()==1 and solver[solver.stage=='RT'].window_start.tolist()==list(range(T)));check('solver_status_and_gap_present',solver.termination.notna().all() and np.isfinite(solver.gap).all());metric('actual_da_mip_gap',solver.loc[solver.stage=='DA','gap'].iloc[0]);metric('actual_rt_max_mip_gap',solver.loc[solver.stage=='RT','gap'].max());metric('mip_windows_not_meeting_requested_gap',np.count_nonzero(~solver.requested_gap_met))
 cash=read('settlement_detail.csv');profits=read('operating_profit_by_unit.csv')
 check('settlement_finite_no_duplicate_entities',not cash.duplicated(['entity','period']).any() and np.isfinite(cash[['q_da','q_rt','lmp_da','lmp_rt','da_amount','rt_amount','total']]).all().all())
 # CSV quantities/prices have four decimals. Row cash tolerance covers only
 # propagated serialization rounding, not dispatch or settlement slack.
 casherr=max(np.max(np.abs(cash.da_amount-cash.q_da*cash.lmp_da*dt)),np.max(np.abs(cash.rt_amount-(cash.q_rt-cash.q_da)*cash.lmp_rt*dt)))
 metric('two_settlement_csv_reconstruction_max_yuan',casherr)
 check('two_settlement_signed_formula',casherr<=.15 and np.max(np.abs(cash.total-cash.da_amount-cash.rt_amount))<=.001)
 expected_profit={u['id'] for g in ('thermal','renewable','hydro','storage','pumped') for u in units.get(g,[]) if u.get('enabled',True)}
 check('profit_unit_coverage_and_finite_available_costs',set(profits.unit)==expected_profit and not profits.unit.duplicated().any() and profits.cost_available.all() and np.isfinite(profits[['cash_revenue_yuan','cash_operating_cost_yuan','simulated_operating_profit_yuan']]).all().all())
 cash_error=[]
 for r in profits.itertuples():
  entities=[r.unit+'_充电',r.unit+'_放电'] if r.type=='storage' else [r.unit+'_抽水',r.unit+'_发电'] if r.type=='pumped' else [r.unit]
  cash_error.append(abs(r.cash_revenue_yuan-cash[cash.entity.isin(entities)].total.sum()))
 metric('profit_revenue_reconciliation_max_yuan',max(cash_error));check('profit_revenue_includes_charging_once',max(cash_error)<=.02)
 check('profit_equals_signed_cash_minus_cost',np.max(np.abs(profits.simulated_operating_profit_yuan-profits.cash_revenue_yuan+profits.cash_operating_cost_yuan))<=.001)
 check('cost_components_reconcile',np.max(np.abs(profits.cash_operating_cost_yuan-profits.variable_cost_yuan-profits.fixed_online_cost_yuan-profits.startup_cost_yuan))<=.001)
 metric('resource_loss_count',np.count_nonzero(profits.simulated_operating_profit_yuan<0))
 metric('total_simulated_cash_operating_profit_yuan',profits.simulated_operating_profit_yuan.sum())
 report['loss_units']=[dict(unit=r.unit,simulated_operating_profit_yuan=float(r.simulated_operating_profit_yuan)) for r in profits.itertuples() if r.simulated_operating_profit_yuan<0]
 report['formal_summary_checks']=summary['checks'];report['all_requested_gap_met']=bool(solver.requested_gap_met.all())
 report['forecast_model']=json.loads((root/'da/profile_generation.json').read_text())['model']
except Exception as e:report['errors'].append(type(e).__name__+': '+str(e))
report['archive_checks_passed']=not report['errors']
(root/'independent_archive_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
print(json.dumps({'directory':str(root),'archive_checks_passed':report['archive_checks_passed'],'errors':report['errors'],'metrics':report['metrics']},ensure_ascii=False,indent=2))
raise SystemExit(0 if report['archive_checks_passed'] else 1)
