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
