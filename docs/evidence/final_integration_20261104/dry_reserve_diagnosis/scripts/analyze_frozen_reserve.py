from pathlib import Path
import argparse,json,sys
import numpy as np,pandas as pd
ap=argparse.ArgumentParser();ap.add_argument('--source-run',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
P=args.source_run;O=args.out;O.mkdir(exist_ok=True,parents=True)
profile=pd.read_csv(P/'profiles.csv');rs=json.loads((P/'reserve_window_evidence.json').read_text());stage=json.loads((P/'stage_diagnostics.json').read_text());R=pd.DataFrame([row for w in rs['rt_windows'] for row in w['resources'] if row['relative_period']==0]);S=pd.DataFrame([row for w in rs['rt_windows'] for row in w['system'] if row['relative_period']==0]) if 'system' in rs['rt_windows'][0] else pd.read_csv(P/'rt_reserve_system.csv')
print('system',S.columns.to_list());print(rs['rt_windows'][0].keys())
rows=[]
re=['W1','W2','PV1','PV2','PV3']
for t in range(96):
 r=R[R.period==t];sysrow=S[S.period==t].iloc[0]; req=float(sysrow['R_up_req']); short=float(sysrow['R_up_short']); avail=profile.load_actual.iloc[t]-profile.TIE_sched.iloc[t]-sum(profile[f'{x}_actual'].iloc[t] for x in re)
 da=profile.load_da.iloc[t]-profile.TIE_sched.iloc[t]-sum(profile[f'{x}_da'].iloc[t] for x in re)
 thermal=r[r.type.isin(['coal','gas_ccgt','gas_gt'])];hydro=r[r.type=='hydro'];storage=r[r.type=='storage'];ps=r[r.type=='pumped']
 capth=float(thermal[thermal['mode']=='online'].pmax_mw.sum());caph=float(hydro.pmax_mw.sum());capsto=400.;capps=float((ps.n_gen*ps.unit_gen_max_mw).sum());pump=float(ps.P_pump_mw.sum()); capbound=capth+caph+capsto+capps-avail-pump
 supply=float(r.r_up.sum()); unused=capbound-supply
 rows.append(dict(period=t,time=f'{t//4:02}:{t%4*15:02}',requirement_mw=req,provided_mw=supply,short_mw=short,net_actual_mw=float(avail),net_da_mw=float(da),net_error_mw=float(avail-da),online_thermal_cap_mw=capth,locked_hydro_zone_cap_mw=caph,battery_cap_mw=capsto,pumped_generation_online_cap_mw=capps,pumped_pump_mw=pump,current_modes_reserve_upper_bound_mw=capbound,unused_or_energy_limited_headroom_mw=unused,reserve_gap_due_to_current_online_modes_mw=max(req-capbound,0),battery_E_start_mwh=float(storage.E_start_mwh.sum()),pumped_V_start_1e4m3=float(ps.V_start_1e4m3.sum())))
out=pd.DataFrame(rows);out.to_csv(O/'baseline_period_diagnosis.csv',index=False);out[out.short_mw>1e-5].to_csv(O/'baseline_shortage_details.csv',index=False);R[R.period.isin(out.loc[out.short_mw>1e-5,'period'])].to_csv(O/'baseline_shortage_resources.csv',index=False)
report={'executed_shortage_periods':int((out.short_mw>1e-5).sum()),'executed_shortage_max_mw':out.short_mw.max(),'shortage_mw_h':out.short_mw.sum()*.25,'shortage_due_to_fixed_modes_lower_mw_h':out.reserve_gap_due_to_current_online_modes_mw.sum()*.25,'shortage_with_all_power_headroom_saturated_count':int(((out.short_mw>1e-5)&(out.unused_or_energy_limited_headroom_mw.abs()<1e-5)).sum()),'identity':'current-mode upper bound = online thermal capacity + locked hydro-zone ceiling + 400MW batteries + online PS generation capacity - actual net load - PS pumping. Storage headroom identity permits charge cessation; energy restrictions can lower it. All current shortage periods have zero renewable curtailment and zero shed/surplus.','warning':'This is an upper bound conditional on original online/mode choices, not proof that full MIP or day-ahead schedule cannot improve.'}
(O/'baseline_diagnosis_summary.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));print(out.loc[out.short_mw>1e-5,['time','short_mw','locked_hydro_zone_cap_mw','current_modes_reserve_upper_bound_mw','unused_or_energy_limited_headroom_mw']].round(6).to_string(index=False))
