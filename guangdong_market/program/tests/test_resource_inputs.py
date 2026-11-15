"""Editable storage declarations must reach physics, not only widgets."""
from pathlib import Path
from copy import deepcopy
import sys,json
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
from gd_resource_inputs import apply_resource_edits,read_case

@pytest.fixture
def case():return read_case(ROOT/'data/default_scenario')

def test_battery_capacity_state_quotes_and_initialization(case):
 from gd_live_market_runtime.initial_state import from_declared
 old=deepcopy(case['batteries']['ES1']);edit=dict(energy_capacity_mwh=240,energy_min_mwh=24,energy_max_mwh=216,initial_energy_mwh=120,terminal_energy_mwh=120,charge_max_mw=110,discharge_max_mw=110)
 apply_resource_edits(case,{'battery':{'ES1':edit}})
 z=case['batteries']['ES1'];assert all(z[k]==v for k,v in edit.items())
 for side in ('charge','discharge'):
  assert z[side+'_curve']['breakpoints_mw'][-1]==110
  assert z[side+'_curve']['prices_yuan_per_mwh']==old[side+'_curve']['prices_yuan_per_mwh']
 assert z['ramp_up_mw_per_h']==old['ramp_up_mw_per_h'] # independent technical capability
 initial=from_declared(case,json.loads((ROOT/'data/default_scenario/INITIAL_CARRY.json').read_text()))
 assert initial['storage']['ES1']['initial_energy_mwh']==120
 bc=case['battery_contract'];bound=json.loads(bc['sources'][bc['storage']['battery_parameters_source_id']]['artifact'])
 assert bound['batteries']['ES1']['initial_energy_mwh']==120

def test_ps_capacity_water_targets_bids_reserve(case):
 from gd_provincial15_model.pumped_declarations import day_ahead
 from gd_provincial15_model.resource_reserve import pumped_response
 apply_resource_edits(case,{'pumped':{'PS1':dict(unit_gen_max=310,vup0=210,vup_end_bid=215)}})
 inv=case['battery_contract']['storage']['pumped_storage']['inventory'];z=inv['PS1'];p=z['parameters']
 assert z['upper_m3']==2100000 and z['lower_m3']==3100000
 d=day_ahead(inv,bid_source=ROOT/'src/smm/bids.py')['PS1']
 assert d['terminal_upper_m3']==2150000 and d['terminal_lower_m3']==3050000
 assert sum(s['width_mw']for s in d['gen_segments'])==620
 assert pumped_response(p,2)[0]==pytest.approx(620*10/15)

@pytest.mark.parametrize('edit',[{'battery':{'ES1':{'initial_energy_mwh':-1}}},{'battery':{'ES1':{'energy_max_mwh':1000}}},{'battery':{'ES1':{'energy_capacity_mwh':float('nan')}}},{'battery':{'ES1':{'charge_max_mw':0}}},{'pumped':{'PS1':{'vup0':500}}},{'pumped':{'PS1':{'unit_gen_max':100}}},{'pumped':{'PS1':{'unit_gen_min':0}}}])
def test_reject_atomic(case,edit):
 original=deepcopy(case)
 with pytest.raises(ValueError):apply_resource_edits(case,edit)
 assert case==original
