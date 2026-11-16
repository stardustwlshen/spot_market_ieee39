"""PRICE/dispatch vintage identity and executed-data provenance. Never fills data."""
from pathlib import Path
import hashlib,math
from gd_forecast_inputs import FILE,apply_forecast,apply_actual

def _sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _need(ok,msg):
 if not ok:raise ValueError(msg)
def source_pin(seed,pins):
 p=(Path(seed)/FILE).resolve()
 if not p.exists():return {}
 _need(pins.get(str(p))==_sha(p),'forecast scenario absent from accepted source pins or changed')
 import gd_forecast_inputs,gd_forecast_scenarios
 files=[p,Path(__file__).resolve(),Path(gd_forecast_inputs.__file__).resolve(),Path(gd_forecast_scenarios.__file__).resolve()]
 return {str(f):_sha(f)for f in files}
def assert_same_window(forecast,accepted,first,count):
 a=forecast.get('_forecast_proof');b=accepted.get('_forecast_proof')
 _need(a is not None and b is not None,'accepted forecast vintage proof missing')
 _need(a==b and a['issue_tick']==first,'PRICE and SCED forecast vintage mismatch')
 for field in ('load_mw','renewable_mw','physical_tie_mw'):
  for bus in forecast['energy_inputs']['buses']:
   for t in range(count):
    x=forecast['energy_inputs'][field][bus,first+t];y=accepted['energy_inputs'][field][bus,t]
    _need(math.isfinite(x) and math.isfinite(y) and x==y,'PRICE/SCED forecast physical input differs: '+field+':'+str((bus,t)))
 return dict(vintage=a,checked_fields=['load_mw','renewable_mw','physical_tie_mw'],checked_periods=count,exact_equal=True)
def for_price(case,seed,request,*,pins,prepared,_case_snapshot=None):
 fp=source_pin(seed,pins)
 if not fp:return case,dict(mode='legacy_no_synthetic_forecast',source_pins={})
 from smm.gd_october_job_codec import loads
 p=(Path(prepared)/'derived_case.json').resolve();_need(pins.get(str(p))==_sha(p),'accepted forecast case not source-bound')
 apply_forecast(case,seed,request['delivery_tick'],_case_snapshot=_case_snapshot);accepted=loads(p.read_bytes());proof=assert_same_window(case,accepted,request['delivery_tick'],min(8,96-request['delivery_tick']))
 proof.update(mode='same_SCED_issue_tick_forecast',source_pins={**fp,str(p):_sha(p)},current_perfect_nowcast_is_research_assumption=True,future_actual_values_used=False)
 return case,proof
def for_execution(case,seed,*,pins):
 fp=source_pin(seed,pins)
 if not fp:return case,dict(mode='legacy_no_synthetic_forecast',source_pins={})
 apply_actual(case,seed)
 return case,dict(mode='executed_actual_for_balance_not_forecast',source_pins=fp,first_cell_nowcast_matches_actual_by_explicit_research_assumption=True)
