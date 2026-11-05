from pathlib import Path
import sys,json,hashlib,importlib.util
import numpy as np,pandas as pd,yaml
ROOT=Path('/workspace/scratch/42c5fe32dad0/market_implementation_20261105/repo');OUT=Path(__file__).parent
sys.path.insert(0,str(ROOT/'src'))
from smm.forecast import forecast_view
D={f'{s}_{f}':ROOT/'outputs'/f'full_2026-07-15_MKT_{s}_v1_hydro_final_id_{f}_20261105' for s in ['wet','dry'] for f in ['on','off']}
def diff(a,b,path=''):
 if isinstance(a,dict) and isinstance(b,dict):return sum([diff(a.get(k),b.get(k),path+'.'+str(k)) for k in a.keys()|b.keys()],[])
 return [] if a==b else [(path,a,b)]
meta={};fingerprints={k:json.loads((p/'scenario_snapshot/source_fingerprint.json').read_text())['sha256'] for k,p in D.items()}
meta['same_source_hashes_all_four']=all(x==next(iter(fingerprints.values())) for x in fingerprints.values())
for season in ['wet','dry']:
 meta[season]={}
 for f in ['config/base.yaml','config/modes.yaml','config/units.yaml']:
  meta[season][f]=diff(*[yaml.safe_load((D[f'{season}_{k}']/'scenario_snapshot'/f).read_text()) for k in ['on','off']])
 for f in ['effective_profiles.csv','resolved_bids.json']:
  meta[season][f]={k:hashlib.sha256((D[f'{season}_{k}']/'scenario_snapshot'/f).read_bytes()).hexdigest() for k in ['on','off']}
 meta[season]['DA_tables_exactly_equal']={f:pd.read_csv(D[f'{season}_on']/f).equals(pd.read_csv(D[f'{season}_off']/f)) for f in ['da_unit_awards.csv','da_hydro.csv','da_storage.csv','da_pumped.csv']}
resources=[];systems=[];summaries=[];late=[];forecasts=[]
for name,p in D.items():
 s=json.loads((p/'summary.json').read_text());e=json.loads((p/'reserve_window_evidence.json').read_text());hyd=json.loads((p/'hydro_window_evidence.json').read_text());states=json.loads((p/'rolling_actual_states.json').read_text())['windows']
 ss=[dict(scenario=name,**r) for w in e['rt_windows'] for r in w['system'] if r['relative_period']==0];systems+=ss
 for w in e['rt_windows']:
  for r0 in w['resources']:
   if r0['relative_period']!=0:continue
   r=dict(r0);typ=r['type'];duration=r['T_dur_h']
   if not r['reserve_eligible']:cap=0.;why='offline_or_pump_or_idle'
   elif typ=='storage':
    a=r['pmax_mw']-r['P_dis_mw']+r['P_ch_mw'];b=(r['E_end_mwh']-r['E_min_mwh'])*r['eta_d']/duration
    cap=min(a,b);why='energy' if b<a-1e-6 else 'power'
   elif typ=='pumped':
    a=r['unit_gen_max_mw']*r['n_gen']-r['P_gen_mw'];b=(r['V_end_1e4m3']-r['V_min_1e4m3'])*r['e_gen_mwh_per_1e4m3']/duration
    cap=min(a,b);why='energy' if b<a-1e-6 else 'power'
   else:cap=r['pmax_mw']-r['P_mw'];why='power'
   resources.append(dict(scenario=name,**r,capacity_up=cap,binding_limit=why,unused_headroom=cap-r['r_up']))
 for t in range(88,96):
  w=e['rt_windows'][t];sysr=w['system'][0];rs=[r for r in w['resources'] if r['relative_period']==0]
  cap=sum(r['pmax_mw']-r['P_mw'] for r in rs if r['type'] not in ['storage','pumped'])+sum(r['pmax_mw']-r['P_dis_mw']+r['P_ch_mw'] for r in rs if r['type']=='storage')
  ps=next(r for r in rs if r['type']=='pumped');gt=next(r for r in rs if r['unit']=='GT')
  short=max(300-(cap-sysr['R_up_req']),0.)
  late.append(dict(scenario=name,period=t,pump_mw=ps['P_pump_mw'],GT_power_mw=gt['P_mw'],
   max_reserve_power_upper=cap,current_requirement=sysr['R_up_req'],spare_upper=cap-sysr['R_up_req'],
   extra_300_pump_short_lower=short,extra_300_short_penalty_yuan=short*.25*100000,
   one_300_pump_terminal_cost_reduction_yuan=300*.25/12.6*9.6*1000,
   additional_pump_available=ps['P_pump_mw']<600-1e-6))
 for w in e['rt_windows'][88:]:
  future=[r for r in w['resources'] if r['unit']=='PS1'];vw=next(x for x in hyd['rt_windows'][w['window_start']]['value_window'] if x['unit']=='PS1')
  forecasts.append(dict(scenario=name,window_start=w['window_start'],planned_end=vw['state'],
    planned_shortfall=vw['slack_dn'],planned_cost=vw['terminal_penalty_cost'],executed_pump=future[0]['P_pump_mw'],
    planned_pump_slots=[dict(period=r['period'],pump_mw=r['P_pump_mw']) for r in future if r['P_pump_mw']>1e-6]))
 ps_end=hyd['rt_windows'][-1]['snapshot']['Vu']['PS1'][0];ps88=states[88]['pumped']['PS1']['V']
 summaries.append(dict(scenario=name,periods=s['executed_periods'],actual_reserve_short_mwh=sum(r['R_up_short'] for r in ss)*.25,
  actual_short_periods=[dict(period=r['period'],mw=r['R_up_short']) for r in ss if r['R_up_short']>1e-6],
  ID_success=s['intraday']['rounds'],ID_failed=s['intraday']['failure_count'],emergency_fallback=s['realtime']['fallback_count'],
  PS_volume_at_22=ps88,PS_actual_end=ps_end,PS_target=200,PS_actual_shortfall=200-ps_end,
  PS_actual_shortfall_discharge_mwh=(200-ps_end)*9.6,
  executed_terminal_records=s['checks']['executed_terminal_deviation_records'],
  physical_checked=s['checks']['physical_feasible_checked_constraints'],terminal_policy_compliant=s['checks']['terminal_policy_compliant']))
resource=pd.DataFrame(resources);system=pd.DataFrame(systems);resource.to_csv(OUT/'actual_reserve_capability.csv',index=False);system.to_csv(OUT/'actual_reserve_system.csv',index=False)
pd.DataFrame(late).to_csv(OUT/'late_pump_reserve_upper_bounds.csv',index=False)
resource.query("scenario in ['dry_on','dry_off'] and period==83").to_csv(OUT/'dry_2045_resource_comparison.csv',index=False)
(OUT/'forecast_pump_plans_NOT_executed_totals.json').write_text(json.dumps(forecasts,indent=2))
(OUT/'summary.json').write_text(json.dumps(summaries,indent=2,ensure_ascii=False));(OUT/'provenance.json').write_text(json.dumps(meta,indent=2,ensure_ascii=False))
for name in ['dry_on','dry_off']:
 rs=resource.query('scenario==@name and period==83');sy=system.query('scenario==@name and period==83').iloc[0]
 print('T83',name,'capacity',rs.capacity_up.sum(),'required',sy.R_up_req,'actual_short',sy.R_up_short,'supply',sy.R_up_supply)
 print(rs[['unit','P_mw','capacity_up','r_up','binding_limit']].to_string(index=False))
 if name=='dry_on':assert abs(rs.capacity_up.sum()+sy.R_up_short-sy.R_up_req)<1e-8
p=D['dry_on'];profiles=pd.read_csv(p/'scenario_snapshot/effective_profiles.csv');units=yaml.safe_load((p/'scenario_snapshot/config/units.yaml').read_text());load=forecast_view(profiles,'load',79,[83],False)[0];ren=sum(min(forecast_view(profiles,d['id'],79,[83],False)[0],d['cap']) for d in units['renewable'] if d.get('enabled',True));s=system.query("scenario=='dry_on' and period==83").iloc[0]
fc=dict(period=83,ID_round=80,forecast_issue=79,ID_load=load,ID_renewable=ren,actual_load=s.load_mw,actual_renewable=s.renewable_available_mw,
 ID_reserve_req=865+.03*load+.1*ren,actual_reserve_req=s.R_up_req,net_load_error=(s.load_mw-s.renewable_available_mw)-(load-ren))
(OUT/'id_forecast_at_2045.json').write_text(json.dumps(fc,indent=2));print('FORECAST',fc)
print('SUMMARY',json.dumps(summaries,ensure_ascii=False));print('PROVENANCE',json.dumps(meta,ensure_ascii=False))
# Read-only verification of the genuine 100-yuan failed ID round.
p=ROOT/'outputs/full_2026-07-15_MKT_dry_v1_hydro_p100_id_on_20261105'
spec=importlib.util.spec_from_file_location('validator_final',ROOT/'scripts/validate_runs.py');val=importlib.util.module_from_spec(spec);spec.loader.exec_module(val)
result=val.checks(p);summ=json.loads((p/'summary.json').read_text());h=json.loads((p/'hydro_window_evidence.json').read_text())
failed=dict(path=str(p),intraday=summ['intraday'],acceptance=result,
 post_failure_sources=[dict(t=w['t'],reference_metadata=w['reference_metadata'],thermal_reference_u=w['thermal_reference_u']) for w in h['rt_windows'] if w['t']>=92])
(OUT/'p100_failed_id_readonly_verification.json').write_text(json.dumps(failed,indent=2,ensure_ascii=False))
print('P100',summ['intraday'],'validation',result['验收通过'],result.get('日内失败回退合规'),result.get('水电参考续程证据合规'))
fc.update(fixed_online_power_capacity_mw=2236+441+400,fixed_tie_mw=1003.75)
fc['ID_conditional_reserve_power_upper']=fc['fixed_online_power_capacity_mw']-(fc['ID_load']-fc['ID_renewable']-fc['fixed_tie_mw'])
fc['ID_conditional_power_margin']=fc['ID_conditional_reserve_power_upper']-fc['ID_reserve_req']
fc['actual_requirement_minus_ID']=fc['actual_reserve_req']-fc['ID_reserve_req']
fc['reconstructed_actual_shortfall']=fc['net_load_error']+fc['actual_requirement_minus_ID']-fc['ID_conditional_power_margin']
(OUT/'id_forecast_at_2045.json').write_text(json.dumps(fc,indent=2))
da=h['da'];delta={(x['station'],x['zone']):np.array(x['values']) for x in da['delta']};numeric=[]
for w in h['rt_windows'][91:]:
 t=w['t'];m=w['reference_metadata'];row=dict(t=t,V_source=m['V']['H1']['trajectory_source'],Vu_source=m['Vu']['PS1']['trajectory_source'],GT_source=m['u']['GT']['source'])
 if t>=92:
  row['max_thermal_DA_error']=float(max(np.max(np.abs(np.array(a)-np.array(da['u'][u][t:t+len(a)]))) for u,a in w['thermal_reference_u'].items()))
  row['max_delta_DA_error']=float(max(np.max(np.abs(np.array(x['values'])-delta[(x['station'],x['zone'])][t:t+len(x['values'])])) for x in w['hydro_reference_delta']))
  row['max_value_window_DA_reference_error']=max(abs(x['reference_state']-da[{'E':'E','Vu':'Vu','V':'Vh'}[x['kind']]][x['unit']][x['period']]) for x in w['value_window'])
 numeric.append(row)
(OUT/'p100_post_failure_DA_numeric_checks.json').write_text(json.dumps(numeric,indent=2))
