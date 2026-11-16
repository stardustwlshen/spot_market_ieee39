from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'app'))
import copy,json,tempfile,unittest
from pathlib import Path
from gd_provincial15_model.pumped_base import attach_initial,sha

def run(mode,mutate=None):
 with tempfile.TemporaryDirectory() as temp:
  root=Path(temp);out=root/'out';out.mkdir()
  p=dict(unit_gen_min=30.,unit_gen_max=100.,unit_pump=100.,n_units=1,v_total=10.)
  unit=dict(gen_mw=50. if mode=='gen'else 0.,pump_mw=100. if mode=='pump'else 0.,mode_age_steps=2,last_active_mode=mode,gen_starts_used=0,pump_starts_used=0,opposite_mode_block_steps=0)
  record=dict(parameters=p,upper_m3=50000.,lower_m3=50000.,net_power_mw=unit['gen_mw']-unit['pump_mw'],modes={'PS_1':mode})
  if mutate:mutate(record,unit)
  decl=root/'PUMPED_DECLARATIONS.json';decl.write_text(json.dumps({'PS':dict(initial_units={'PS_1':unit})}))
  physical=root/'physical.json';physical.write_text(json.dumps(dict(grid_minutes=15,physical_cells=[dict(pumped={'PS':dict(upper_m3=40000.+i,lower_m3=60000.-i)})for i in range(96)])))
  manifest=dict(source_pins={str(decl):sha(decl)},physical_solution=dict(path=str(physical),sha256=sha(physical)))
  case=dict(battery_contract=dict(storage=dict(pumped_storage=dict(inventory={'PS':record}))))
  result=attach_initial(dict(source_pins={}),case,manifest,out)
  return result['pumped_storage']['PS']

class PumpedInitialTests(unittest.TestCase):
 def test_three_modes_preserve_declared_t0(self):
  for mode in ('off','gen','pump'):
   with self.subTest(mode=mode):
    state=run(mode);self.assertEqual(state['units']['PS_1']['mode'],mode)
    self.assertEqual(state['upper_m3'],50000.);self.assertEqual(state['lower_m3'],50000.)
    self.assertEqual(state['net_power_mw'],{'off':0.,'gen':50.,'pump':-100.}[mode])
 def test_missing_power_rejected(self):
  with self.assertRaisesRegex(ValueError,'explicit per-unit'):run('gen',lambda r,u:u.pop('gen_mw'))
 def test_power_mode_contradiction_rejected(self):
  with self.assertRaises(ValueError):run('pump',lambda r,u:u.update(gen_mw=50.))
 def test_wrong_net_rejected(self):
  with self.assertRaisesRegex(ValueError,'net power'):run('gen',lambda r,u:r.update(net_power_mw=0.))
if __name__=='__main__':unittest.main(verbosity=2)
