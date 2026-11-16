"""Distinguish declared balance slack from unexplained physical imbalance."""
from decimal import Decimal as D
import re
STATUS='economics_unavailable_balance_slack'
def classify(physical,values):
 totals={z['tick']:[D(0),D(0)] for z in physical}
 for key,value in values.items():
  match=re.fullmatch(r"balance_(shortfall|surplus)\[(.+),(\d+)\]",key)
  if not match or int(match[3]) not in totals:continue
  amount=D(str(value))
  if not amount.is_finite() or amount < D('-1e-6'):raise ValueError('invalid balance slack')
  totals[int(match[3])][match[1]=='surplus']+=max(D(0),amount)
 rows=[]
 for z in physical:
  short,surplus=totals[z['tick']];residual=D(str(z['residual_MW']))
  if not residual.is_finite() or abs(residual+short-surplus)>=D('1e-6'):
   raise ValueError('unexplained physical imbalance; not a declared balance slack')
  rows.append(dict(z,shortfall_MW=short,surplus_MW=surplus))
 return rows if any(z['shortfall_MW']>D('1e-6') or z['surplus_MW']>D('1e-6') for z in rows) else None

def unavailable(hour):return hour.get('economic_status')==STATUS


def attach_accounts(result,diagnostics,hour):
 """Preserve resource accounts; never allocate virtual balancing quantities."""
 cells=diagnostics or result['physical_cells'];step=D(str(result['input_binding']['RT_physical_interval_minutes']))/60
 if step<=0 or len(cells)*step!=1:raise ValueError('complete one-hour system balance required')
 def energy(key):return sum((D(str(z.get(key,0)))for z in cells),D(0))*step
 supplied=energy('supply_MW');load=energy('load_MW');tie=energy('fixed_tie_MW')
 short=energy('shortfall_MW');surplus=energy('surplus_MW')
 actual=sum((D(str(r['simulated_executed_MWh']))for r in result['rows']),D(0))
 if abs(actual-supplied)>D('1e-6'):raise ValueError('resource executed energy differs from physical generation')
 closure=supplied+tie+short-surplus-load
 if abs(closure)>D('1e-6'):raise ValueError('system balance including declared slack does not close')
 amounts=[r.get('energy_amount_CNY')for r in result['rows']]
 subtotal=sum((D(str(v))for v in amounts),D(0)) if all(v is not None for v in amounts) else None
 if subtotal is not None and not subtotal.is_finite():raise ValueError('nonfinite resource account subtotal')
 result.update(hour=hour,system_balance=dict(resource_net_executed_MWh=actual,external_tie_MWh=tie,requested_load_MWh=load,unserved_MWh=short,virtual_surplus_MWh=surplus,physical_imbalance_MWh=supplied+tie-load,closure_including_slack_MWh=closure,resource_energy_accounts_subtotal_CNY=subtotal,system_cash_settlement_complete=False,virtual_energy_allocated_to_resources=False),total_energy_component_CNY=subtotal)
 if diagnostics:
  result.update(economic_status='simulation_accounts_with_balance_slack',balance_diagnostics=diagnostics,economic_reason='供需存在松弛；按机组实际执行电量和有效价格计算模拟收入。罚值可能影响电价，虚拟缺供或过剩不分配给机组、不作为真实电量或额外成本。不是正式结算。')
 return result
