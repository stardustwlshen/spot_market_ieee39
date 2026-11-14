"""Fail closed until generic thermal trajectory ages have independent support."""
import math

def stable_only_carried_age(previous_phase,next_phase,old_age_h,unit,values,carried_cells=3):
 if carried_cells!=3:raise ValueError('age guard currently covers exactly three carried five-minute cells')
 if previous_phase!='stable' or next_phase!='stable':
  raise ValueError('thermal age carry supports unchanged stable-online only; off/active/changed boundary requires independent trajectory-age support')
 if not isinstance(old_age_h,(int,float)) or isinstance(old_age_h,bool) or not math.isfinite(old_age_h) or old_age_h<0:
  raise ValueError('invalid inherited thermal age')
 for i in range(carried_cells):
  # Reject even a stop/start excursion that returns to the same end state.
  for name,expected in (('online',1),('stable',1),('start',0),('stop',0)):
   key=f'{name}[{unit},{i}]'
   if key not in values or not math.isfinite(values[key]) or abs(values[key]-expected)>1e-8:
    raise ValueError('thermal trajectory-age support missing: changed/nonstable carried cell '+key)
 return old_age_h+carried_cells/12
