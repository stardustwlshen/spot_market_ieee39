from copy import deepcopy
import pytest
from test_thermal_capacity_editor import case
from gd_provincial_inputs import apply_thermal_edits

def fixture():
 c=case();c['no_load_cost']={'G':100.,'H':100.};c['energy_inputs']['horizon']={'periods':[{'duration_h':1/12}for _ in range(2)]}
 for g,d in c['energy_inputs']['thermal'].items():
  d['ramp_up_mw_per_h']=d['ramp_down_mw_per_h']={0:2000.,1:2000.}
  c['curves'][g].update(basis='research',source='linear research fixture')
  for kind in ('startup','shutdown'):c['curves'][g][kind][0]['cost']=500.
 return c

@pytest.mark.parametrize('minimum,maximum',[(10.,100.),(30.,100.),(110.,150.)])
def test_minimum_complete_rebuild(minimum,maximum):
 c=fixture();old=deepcopy(c);power=max(50.,minimum)
 apply_thermal_edits(c,{'G':{'min_mw':minimum,'max_mw':maximum,'initial_power_mw':power}})
 d=c['energy_inputs']['thermal']['G'];curve=c['curves']['G'];assert d['bid_base_mw']==minimum and d['min_mw'][0]==minimum
 assert d['base_bid_yuan_per_h'][0]==minimum*40.
 assert sum(s['width_mw']for s in d['segments'])==pytest.approx(maximum-minimum)
 assert curve['startup'][-1]['end']==minimum and curve['shutdown'][0]['begin']==minimum
 assert curve['startup'][0]['cost']==100.+40.*minimum/2
 assert d['startup_output_limit_mw'][0]==minimum and d['startup_yuan']==old['energy_inputs']['thermal']['G']['startup_yuan']
 assert d['ramp_up_mw_per_h']==old['energy_inputs']['thermal']['G']['ramp_up_mw_per_h']
 assert c['energy_inputs']['thermal']['H']==old['energy_inputs']['thermal']['H']

@pytest.mark.parametrize('bad',['nonlinear','cost','varying_base','ramp','initial','agc','minimum_zero'])
def test_reject_unverified_or_inconsistent(bad):
 c=fixture();d=c['energy_inputs']['thermal']['G'];edit={'min_mw':30.};agc=None
 if bad=='nonlinear':c['curves']['G']['startup'][0]['mean']=8.
 if bad=='cost':c['curves']['G']['startup'][0]['cost']=499.
 if bad=='varying_base':d['base_bid_yuan_per_h'][1]=999.
 if bad=='ramp':d['ramp_up_mw_per_h']={0:100.,1:100.}
 if bad=='initial':edit['min_mw']=60.
 if bad=='agc':agc={'G':dict(up_mw={0:40.},down_mw={0:40.})}
 if bad=='minimum_zero':edit['min_mw']=0.
 old=deepcopy(c)
 with pytest.raises(ValueError):apply_thermal_edits(c,{'G':edit},agc=agc)
 assert c==old

def test_off_boundary():
 c=fixture();apply_thermal_edits(c,{'G':{'min_mw':30.,'initial_online':0,'initial_power_mw':0.}})
 assert c['energy_inputs']['thermal']['G']['initial_power_mw']==0.

@pytest.mark.parametrize('bad',['fixed','special_limit','unknown_basis','min_equals_max','off_nonzero'])
def test_physical_dependencies_fail_atomic(bad):
 c=fixture();d=c['energy_inputs']['thermal']['G'];edit={'min_mw':30.}
 if bad=='fixed':d['fixed_power_mw'][0]=25.
 if bad=='special_limit':d['startup_output_limit_mw'][0]=25.
 if bad=='unknown_basis':c['curves']['G']['basis']='external'
 if bad=='min_equals_max':edit['min_mw']=100.
 if bad=='off_nonzero':edit.update(initial_online=0,initial_power_mw=1.)
 old=deepcopy(c)
 with pytest.raises(ValueError):apply_thermal_edits(c,{'G':edit})
 assert c==old
