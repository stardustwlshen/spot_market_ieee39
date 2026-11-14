"""In-process source capability populated only after selected DA raw validation."""
from datetime import datetime,timedelta
from copy import deepcopy
import hashlib,json
_ACTIVE=None
_CHECKED=False
def configure(source,declaration):
 global _ACTIVE,_CHECKED
 _CHECKED=False
 _ACTIVE=(source,deepcopy(declaration))
def trusted():
 assert _ACTIVE is not None,'source not registered by prepare'
 global _CHECKED
 s,d=_ACTIVE
 if _CHECKED:return s,d
 from pathlib import Path
 for p,h in s['pins'].items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h,'selected source drift'
 _CHECKED=True
 return s,d
def apply(case,carry,declarations):
 s,d=trusted();assert declarations==d,'declaration not registered by prepare'
 assert carry['cutoff_tick']==525 and carry['delivery_tick']==528
 assert declarations['official_published_event']is False and declarations['safety_release']is False
 assert case['contract']['day_start']==s['origin'],'different source origin'
 return case,dict(future_target_envelopes=[],formal=False,original_DA_terminal_policy_preserved=True)
def plan_at(d,g,at):
 s,registered=trusted();assert d==registered
 t=datetime.fromisoformat(at);return d['days'][t.date().isoformat()]['artifact']['thermal_online'][g][(t.hour*60+t.minute)//5]
def stop_value(d,g,t):
 s,registered=trusted();assert d==registered
 return int(any(x['unit']==g and x['begin_tick']==t for x in d['existing_shutdown_permissions']))
def verify_past_target(uid,r,beginning):
 s,d=trusted();origin=datetime.fromisoformat(s['origin']);at=datetime.fromisoformat(r['terminal_at']);tick=int((at-origin).total_seconds()/300)-1
 assert (at-origin).total_seconds()%300==0 and 0<=tick<528,'historical target outside source'
 assert at<beginning and abs(s['values'][f'battery.energy[{uid},{tick}]']-r['terminal_energy_mwh'])<=1e-7,'historical target not satisfied'
 return True
