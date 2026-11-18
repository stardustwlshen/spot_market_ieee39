"""UI-only thermal drafts. Never write scenario files or launch a calculation."""
FIELDS = {
 'minimum': 'min_mw', 'capacity': 'max_mw', 'power': 'initial_power_mw',
 'age': 'initial_state_age_h', 'up': 'min_up_h', 'down': 'min_down_h',
}

def accepted_edit(base, saved):
 return {'parameters': {k: saved.get('parameters', {}).get(k, base[k])
                        for k in (*FIELDS.values(), 'initial_online')},
         'bid_factor': saved.get('bid_factor', 1.),
         'ramp_factor': saved.get('ramp_factor', 1.)}

def capture(unit, token):
 import streamlit as st
 key=lambda field: field+'_'+unit+'_'+token
 draft={'parameters':{name:st.session_state[key(field)] for field,name in FIELDS.items()},
        'bid_factor':st.session_state[key('bid')], 'ramp_factor':st.session_state[key('ramp')]}
 draft['parameters']['initial_online']=int(st.session_state[key('online')]=='开机')
 st.session_state.setdefault('thermal_drafts',{})[unit]=draft

def discard(unit, token, accepted):
 import streamlit as st
 for field,name in FIELDS.items():st.session_state[field+'_'+unit+'_'+token]=accepted['parameters'][name]
 for field,name in [('bid','bid_factor'),('ramp','ramp_factor')]:
  st.session_state[field+'_'+unit+'_'+token]=accepted[name]
 st.session_state['online_'+unit+'_'+token]='开机' if accepted['parameters']['initial_online'] else '停机'
 st.session_state.setdefault('thermal_drafts',{}).pop(unit,None)

def dirty_units(parameters, saved, drafts):
 return [unit for unit,draft in drafts.items()
         if unit in parameters and draft != accepted_edit(parameters[unit],saved.get(unit,{}))]
