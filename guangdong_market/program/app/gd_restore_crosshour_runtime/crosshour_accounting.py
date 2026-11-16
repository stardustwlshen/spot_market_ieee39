"""Independent absolute-sum checks for the bounded Q07 -> Q11 experiment."""
from pathlib import Path
from decimal import Decimal
import json,hashlib

def verify_accounting(out,w,ledger,previous):
 def need(ok,message):
  if not ok:raise ValueError(message)
 out=Path(out); expected=[0,1] if w<11 else [0,1,2]
 need(ledger['settled_hour_ids']==expected,'wrong completed hour set')
 need(ledger['unsettled_executed_ticks']==(list(range(8,w+1)) if w<11 else []),'wrong unsettled quarter set')
 need(ledger['included_ticks']==[w] and ledger['next_execute_tick']==w+1,'repeated or skipped execution')
 components={};total=Decimal(0)
 for h in expected:
  path=out/f'pipeline/price/HOUR{h}.json';raw=path.read_bytes();hour=json.loads(raw)
  need(hour['input_binding']['hour']==h and hour['executed_cells']==4,'wrong hour identity')
  if h<2:
   old=previous['settled_hours'][h]
   need(old['hour']==h and hashlib.sha256(raw).hexdigest()==old['sha256'],'historical hour changed')
  subtotal=Decimal(0)
  for row in hour['rows']:
   amount=Decimal(row['energy_amount_CNY']);parts={k:Decimal(v)for k,v in row['components_CNY'].items()}
   need(abs(sum(parts.values(),Decimal(0))-amount)<Decimal('0.000001'),'unit energy components differ')
   subtotal+=amount
   for k,v in parts.items():components[k]=components.get(k,Decimal(0))+v
  need(abs(subtotal-Decimal(hour['total_energy_component_CNY']))<Decimal('0.000001'),'hour total differs')
  total+=subtotal
 need(total==Decimal(ledger['research_energy_total_CNY']),'duplicated or omitted research energy')
 need(components=={k:Decimal(v)for k,v in ledger['research_energy_components_CNY'].items()},'cumulative components differ')
 if w<11:
  need(ledger['research_energy_total_CNY']==previous['research_energy_total_CNY'] and ledger['research_energy_components_CNY']==previous['research_energy_components_CNY'],'premature settlement')
 return {'window':w,'settled_hours':expected,'absolute_energy_sum_CNY':str(total),'not_formal_profit':True}


def verify_economics_arithmetic(settlement):
 """Check unit-level arithmetic without promoting estimates into formal revenue."""
 def need(ok,message):
  if not ok:raise ValueError(message)
 d=Decimal;seen=set()
 for r in settlement['unit_economics']['rows']:
  g=r['resource'];need(g not in seen,'duplicate economic unit');seen.add(g)
  need(d(r['spot_cost_CNY'])==d(r['generation_cost_CNY'])+d(r['executed_startup_cost_CNY']),'cost components differ '+g)
  margin=d(r['energy_revenue_CNY'])-d(r['spot_cost_CNY'])
  need(abs(d(r['energy_contribution_CNY'])-margin)<d('0.000001'),'energy margin differs '+g)
  if r.get('agc_estimate') is not None and r.get('reserve_estimate') is not None:
   agc=d(r['agc_estimate']['amount_CNY']);reserve=d(r['reserve_estimate']['amount_CNY'])
   need(abs(d(r['estimated_spot_margin_before_service_allocation_CNY'])-margin-agc-reserve)<d('0.000001'),'estimated margin differs '+g)
   need(r['service_allocation_included'] is False,'unsupported service allocation')
  if r['agc_revenue_CNY'] is None or r['reserve_revenue_CNY'] is None:
   need(r['simulated_spot_profit_CNY'] is None and bool(r['missing']),'missing formal service data was converted to zero')
 return {'units_checked':sorted(seen),'check':'arithmetic_only; source quantities/rates require separate validation','formal':False}
