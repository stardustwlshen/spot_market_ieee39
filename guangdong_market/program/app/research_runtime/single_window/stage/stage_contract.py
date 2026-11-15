from datetime import datetime,timedelta
from copy import deepcopy

def checked_stage(s,c):
 s=deepcopy(s)
 if s['count']!=24 or s['block'] not in ('near','far'):raise ValueError('explicit24 block')
 offset=0 if s['block']=='near' else 24
 if s['start_tick']!=s['near_start_tick']+offset or c['delivery_tick']!=s['start_tick']:raise ValueError('stage carry tick')
 if s['near_start_tick']!=c['cutoff_tick']+s['anchor_ticks']:raise ValueError('cutoff cannot be advanced for predicted far')
 if s['anchor_ticks'] not in (3,6):raise ValueError('unsupported explicit historical anchor')
 if s['initial_kind']!=('issued_prefix_projected_boundary' if offset==0 else 'near_predicted_end23'):raise ValueError('boundary kind')
 if not offset:
  if not c.get('predicted_issued_prefix') or c['executed_end_exclusive']!=s['executed_end_exclusive'] or c['issued_end_exclusive']!=s['issued_end_exclusive'] or s['issued_end_exclusive']!=s['start_tick']:raise ValueError('issued-prefix projection must retain actual and issued boundary separately')
 if offset:
  proof=s['near_boundary_proof']
  if proof['end_tick']!=s['start_tick'] or not proof['SCED_raw_sha256'] or proof['carry_sha256']!=s['carry_sha256']:raise ValueError('predicted seam proof required')
 if not s['carry_sha256']:raise ValueError('carry pin required')
 for key in ('thermal','thermal_age_h','storage','notice_values'):
  if key not in c:raise ValueError('missing '+key)
 return s

def validate_clock(c,n):
 s=c['stage']
 if n!=24 or s['count']!=n:raise ValueError('stage size')
 if datetime.fromisoformat(c['domain_start']) != datetime.fromisoformat(c['cutoff'])+timedelta(minutes=5*(s['anchor_ticks']+(24 if s['block']=='far' else 0))):raise ValueError('stage absolute clock')
