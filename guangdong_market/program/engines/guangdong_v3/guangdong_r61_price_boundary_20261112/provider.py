"""C PRICE-only calendar boundaries; no runtime state or execution authority."""
from dataclasses import dataclass
from datetime import datetime
import math
@dataclass(frozen=True)
class PricingStateBasis:
 kind: str='DA_prefix_SOC_dated_usage_quarter_SOC_C_research'
 purpose: str='PRICE_only'
 formal: bool=False

def boundaries(inp,basis=PricingStateBasis()):
 if type(basis)is not PricingStateBasis or basis!=PricingStateBasis():raise ValueError('no official/RT boundary mapping')
 cells=inp['da_cells'];start=inp['window']['start'];dates=sorted({c['start'][:10]for c in cells});k=inp['window']['midnight_local_energy_index']
 assert len(cells)==24 and cells[0]['start']==start
 if k is not None:assert 0<k<=24 and k%3==0 and cells[k-1]['end'][:10]!=cells[0]['start'][:10]
 result={}
 for b,a in inp['projection']['state']['batteries'].items():
  usage={};derivation={}
  for date in dates:
   first=next(c for c in cells if c['start'][:10]==date);z=first['batteries'][b]
   assert min(z['charge_mw'],z['discharge_mw'])<=1e-9
   if date!=start[:10]:assert datetime.fromisoformat(first['start']).hour==datetime.fromisoformat(first['start']).minute==0
   increment=abs(z['energy_end_mwh']-z['energy_begin_mwh']);raw=z['daily_used_internal_mwh'][date]-increment
   assert math.isfinite(raw) and raw>=-1e-9
   usage[date]=max(0.,raw);derivation[date]=dict(first_cell_start=first['start'],end_usage=z['daily_used_internal_mwh'][date],internal_increment=increment,raw_initial_usage=raw,negative_zero_adjustment=max(0.,-raw))
   if date!=start[:10]:assert abs(usage[date]-inp['resources'][b]['cycle_budget'][date]['used_internal_mwh'])<=1e-8
  result[b]=dict(energy_mwh=cells[0]['batteries'][b]['energy_begin_mwh'],used_internal_mwh=usage,actual_energy_mwh=a['energy_mwh'],actual_usage_by_date={d:a['daily_used_internal_mwh'].get(d,0.)for d in dates},derivation=derivation)
 return result
