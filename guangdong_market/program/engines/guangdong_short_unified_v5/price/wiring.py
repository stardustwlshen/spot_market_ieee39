"""Explicit research model wiring. Never interprets fixed AC witness as marginal domain."""
from contracts import need

def validate_wiring(z):
 need(z['formal'] is False and z['safe_PRICE_certified'] is False,'research only')
 need(z['safety']['kind']=='unchecked','AC feedback not implemented: fixed witness cannot define a marginal domain')
 for name,c in z['constraints'].items():
  need(c['status']=='inapplicable' or (name in ['negative_reserve','primary_frequency_reserve','group_power','group_energy'] and c['status']=='required'),'mathematical implementation missing: '+name)
 for states in z['eligibility'].values():
  need(all(s in ['pricing_stable','fixed_nonpricing','off'] for s in states),'off/transition full reserve/ramp handling not implemented')
 need(all(z['penalties'][k]>0 for k in ['load','accident','network']),'positive slack penalties required')

def vre_cost(power,curtail,z,b,h):
 return h*(z['VRE_offers'][b]*power+z['penalties']['renewable_curtailment']*curtail)

def apply_slacks(base,names,mapping,resolved,parameters,as_of):
 # Retain the established, separately sourced allocation/effectivity contract.
 from pathlib import Path
 import sys
 sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'guangdong_rt_price_program_v22_20261112/gd_price'))
 from mechanisms import soften,validate_parameters
 rates=validate_parameters(parameters,as_of)
 need(rates=={k:resolved['penalties'][k] for k in rates},'two parameter documents disagree')
 return soften(base,names,mapping,parameters,as_of=as_of)
