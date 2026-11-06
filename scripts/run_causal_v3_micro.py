"""Real one-day, 96x15min causal-input model acceptance on a small two-bus market.

This is not the separate full IEEE39/two-day state integration acceptance.
"""
from pathlib import Path
import sys,copy,json,time
import pandas as pd
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'src'),str(Path(__file__).resolve().parents[1]/'tests')]
from test_hydro_rolling import hydro_case
from smm.config import load_config
from smm.causal_types import MODEL,ResourceSpec
from smm.causal_processes import ProcessPlan
from smm.causal_provider import compile_provider,save_bundle
from smm.case import build_da_case
from smm.full import run_full,write_full_outputs
from smm.hydro_daily_energy import audit_exported
from smm.forecast import forecast_view

start='2026-07-15T00:00:00Z';old=hydro_case(T=96);cfg=old.cfg
cfg.base['forecast']={'model':MODEL};cfg.base['solver']['threads']=1
cfg.base['solver']['time_limit_s']=20
cfg.units['hydro'][0].update(e_day_min=200.,ramp=100.)
cfg.mode['realtime']['hydro']='value';cfg.mode['realtime']['storage']['mode']='value'
cfg.mode['realtime']['value_method']['terminal_policy']={'mode':'soft','penalty_yuan_per_discharge_mwh':1000.}
cfg.mode['realtime']['value_method']['hydro_lookahead']='reference_continuation'
cfg.mode['intraday'].update(enabled=True,interval_h=4.,time_limit_s=20.)
cfg.mode['auxiliary_pricing']['enabled']=True
renewables=[]
for kind,uid,cap in [('wind','W1',20.),('pv','PV1',10.)]:
 r=copy.deepcopy(next(r for r in load_config().units['renewable'] if r['type']==kind));r.update(id=uid,bus=2,cap=cap)
 renewables.append(r);old.bids['units'][uid]=dict(segments=[dict(p_start=0.,p_end=cap,price=0.)],avail_pmax=None)
cfg.units['renewable']=renewables
resources=(ResourceSpec('load','load',120.),ResourceSpec('W1','wind',20.),ResourceSpec('PV1','pv',10.),ResourceSpec('H1','natural_inflow',50.))
plan=ProcessPlan(start,1,'UTC',resources,{'2026-07-15':dict(wind='wind_ramp',pv='pv_broken_cloud',load='load_peak_shift',natural_inflow='inflow_rising')},root_seed='42')
actual,provider=compile_provider(plan,forecast_policy_by_date={'2026-07-15':dict(wind='baseline',pv='delayed',load='responsive',natural_inflow='baseline')})
case=build_da_case(cfg,old.net,pd.DataFrame({'period':range(1,97)}),old.bids,input_provider=provider,day_start_utc=start)
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False);save_bundle(out/'input_bundle',actual,forecast_policy_by_date=provider.forecast_policy_by_date)
t=time.perf_counter();result=run_full(cfg,case,log=print, retain_id_models=False)
summary=write_full_outputs(result,out,make_plots=False)
evidence=json.loads((out/'hydro_window_evidence.json').read_text());states=json.loads((out/'rolling_actual_states.json').read_text())
audit=audit_exported(cfg,evidence,states,bids=case.bids)
report=dict(model=MODEL,grid='96x15min',network='two_bus_test_market',stage='full_day_real_optimization',
    periods=len(result['rr'].rt),id_rounds=len(result['rr'].id_rounds),id_failures=result['rr'].id_failures,
    elapsed_seconds=time.perf_counter()-t,provider=provider.source_manifest(),checks=result['checks'],exported_daily_energy_audit=audit,
    first_current='explicit current_interval_perfect_measurement research assumption',
    not_claimed='IEEE39 or multi-day state carryover acceptance')
(out/'causal_micro_acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
print(json.dumps(dict(periods=report['periods'],id_rounds=report['id_rounds'],elapsed_seconds=report['elapsed_seconds'],
    exported_daily_energy_evidence_valid=audit['hydro_daily_minimum_evidence_valid']),indent=2))
