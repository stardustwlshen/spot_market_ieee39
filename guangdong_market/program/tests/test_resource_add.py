from pathlib import Path
from copy import deepcopy
import sys,json
import pytest
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P/'app'))
from gd_resource_inputs import read_case
from gd_resource_add import add
from gd_resource_registry import thermal_types

def inputs():
 return read_case(P/'data/default_scenario'),json.loads((P/'data/default_scenario/seed/agc.json').read_text()),json.loads((P/'data/default_scenario/seed/request.json').read_text())
@pytest.mark.parametrize('kind,template,g,bus',[('coal','C3','NewCoal','14'),('gas','GT','NewGas','15'),('battery','ES1','ES6','12'),('pumped','PS1','PS2','13')])
def test_add(kind,template,g,bus):
 c,a,r=inputs();c,a,r=add(c,a,r,kind=kind,template=template,resource_id=g,bus=bus)
 if kind in ('coal','gas'):
  assert thermal_types(c)[g]==kind
  assert c['energy_inputs']['thermal'][g]['initial_online']==0
  assert c['energy_inputs']['thermal'][g]['initial_power_mw']==0
  assert a[g]['qualification']=='no_award' and not any(a[g]['up_mw'].values())
  assert g in c['curves'] and g in c['reserve']['contingency_eligible'] and g in r['preorigin_notices']
 elif kind=='battery':assert c['batteries'][g]['bus']==bus
 else:
  p=c['battery_contract']['storage']['pumped_storage']['inventory'][g]
  assert set(p['modes'])=={'PS2_1','PS2_2'} and p['parameters']['bus']==13
@pytest.mark.parametrize('changes',[dict(resource_id='C1'),dict(resource_id='bad,id'),dict(bus='999'),dict(kind='wind'),dict(template='GT')])
def test_reject(changes):
 c,a,r=inputs();orig=deepcopy((c,a,r));args=dict(kind='coal',template='C3',resource_id='NewCoal',bus='14');args.update(changes)
 with pytest.raises(ValueError):add(c,a,r,**args)
 assert (c,a,r)==orig

def test_offline_initial_contract():
 c,a,r=inputs()
 from gd_provincial15_model.short_energy_core import _thermal_inputs
 from gd_live_market_runtime.initial_state import from_declared
 z=c['energy_inputs']['thermal']['C1'];z.update(initial_online=0,initial_power_mw=0.,initial_state_kind='off',initial_state_age_h=.5,min_down_h=6.)
 ids=tuple(z['max_mw']);_thermal_inputs({'C1':z},c['energy_inputs']['buses'],ids,{'checks':[]})
 initial=from_declared(c,json.loads((P/'data/default_scenario/INITIAL_CARRY.json').read_text()))
 assert initial['thermal']['C1']['residual_down_ticks']==22
 for patch in (dict(initial_online=1),dict(initial_power_mw=1),dict(initial_state_kind='garbage')):
  bad=deepcopy(z);bad.update(patch)
  with pytest.raises(ValueError):_thermal_inputs({'C1':bad},c['energy_inputs']['buses'],ids,{'checks':[]})
