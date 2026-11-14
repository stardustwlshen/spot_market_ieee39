"""Bounded source adapter, configured only in isolated reference build process."""
from datetime import datetime
CONFIG=None
def apply(case,carry,declarations):
 assert CONFIG is not None and carry['cutoff_tick']==282 and carry['delivery_tick']==288
 assert declarations==CONFIG['declaration'] and declarations['official_publication'] is False
 return case,dict(future_target_envelopes=[],formal=False,original_DA_terminal_policy_preserved=True,new_D2_terminal=False)
def plan_at(d,g,at):
 t=datetime.fromisoformat(at);return d['days'][t.date().isoformat()]['artifact']['thermal_online'][g][(t.hour*60+t.minute)//5]
def stop_value(d,g,t):return 0
def verify_past_target(uid,r,beginning):
 assert beginning.isoformat()==CONFIG['boundary']['boundary_at'] and r['terminal_at']==beginning.isoformat()
 assert abs(CONFIG['values'][f'battery.energy[{uid},287]']-r['terminal_energy_mwh'])<=1e-7
 return True
