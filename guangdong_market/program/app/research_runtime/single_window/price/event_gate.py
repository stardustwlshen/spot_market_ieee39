"""Arithmetic eligibility gate, NOT raw acceptance or permission to execute.
Caller must first run the existing real-session accepted-source checks.
"""
from state_adapter import PROFILE,derive

def supported_fixed_event_state(*,case,values,first,event_profile=None):
 if event_profile not in (None,PROFILE):raise ValueError('unknown event PRICE qualification/profile')
 state=derive(case,values,first,quarters=8)
 active=any(z['state'] in ('startup','shutdown') for tr in state['trace'].values() for z in tr['cells'])
 if active and event_profile!=PROFILE:raise ValueError('affected units require explicit supported fixed-curve research profile')
 for g in state['trace']:
  cv=case['curves'][g]
  if active and cv.get('classes'):raise ValueError('typed hot/warm/cold classification is not covered by fixed-curve profile')
  for status in state['eligibility'][g]:
   if status not in ('pricing_stable','fixed_nonpricing','off'):raise ValueError('unknown price eligibility')
  prior=state['trace'][g]['previous_cell']['state']
  for z in state['trace'][g]['cells']:
   if {prior,z['state']}=={'stable','off'}:raise ValueError('off/stable transition lacks sourced active curve')
   prior=z['state']
   if z['state'] in ('startup','shutdown'):
    if not z.get('curve_row') or z.get('begin_tick')is None:raise ValueError('fixed event lacks unique sourced curve')
 return state

def bind_incoming(values,thermal,first):
 """Only stationary incoming state; no invented active trajectory history."""
 import math
 for g,d in thermal.items():
  phase=d['phase'];power=d['end_power_mw']
  if phase not in ('stable','off','offline') or d.get('remaining_curve'):raise ValueError('incoming active curve history unsupported')
  if not math.isfinite(power):raise ValueError('incoming power not finite')
  off=phase in ('off','offline')
  if off and abs(power)>1e-8:raise ValueError('incoming off power nonzero')
  for key,v in [('power',power),('online',int(not off)),('stable',int(not off)),('off',int(off)),('start',0),('stop',0)]:values[f'{key}[{g},{first-1}]']=v
 return values
