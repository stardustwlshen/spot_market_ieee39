from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'engines/guangdong_short_unified_v6/price')]
import unittest
import pyomo.environ as p
from test_rt_trajectory import fixture
from gd_provincial15_model.thermal_physical import build_free_scuc
from gd_provincial15_model.price_reserves import primary_fraction_capacities
from physical import add_physical

def dispatch():
 a=fixture('startup',2);d=a['energy_inputs']['thermal']['G'];d.update(initial_power_mw=50.,initial_state_kind='stable',initial_state_age_h=10.)
 a['energy_inputs']['load_mw'][('B',0)]=50.
 a['boundary_states']['G'].update(phase='stable',end_power_mw=50.,event_id=None,event_elapsed_ticks=0,remaining_curve=[],residual_up_ticks=0)
 a['reserve']['primary_fraction']['G']=.06;a['reserve']['primary_mw'][0]=6.
 a['agc_awards']={'G':dict(up_mw={0:20.},down_mw={0:0.},qualification='explicit_synthetic_qualified',basis='research',source='test',available_at=a['contract']['cutoff'])}
 m=build_free_scuc(**a);p.SolverFactory('appsi_highs').solve(m)
 for v in m.component_data_objects(p.Var):
  if v.is_integer():v.fix(round(p.value(v)));v.domain=p.Reals
 r=p.SolverFactory('appsi_highs').solve(m)
 return a,m,r

def price(load=50.,repaired=True,agc=20.,maximum=100.,requirement=6.,window=(0.,100.)):
 m=p.ConcreteModel();m.P=p.Var(['G'],[0],bounds=window);m.accident=p.Var(['G'],[0],bounds=(0.,0.));m.balance=p.Constraint(expr=m.P['G',0]==load)
 specs=dict(negative_reserve=dict(status='inapplicable'),primary_frequency_reserve=dict(status='required',data=dict(requirement_MW=[requirement],qualified_resources=['G'],fraction={'G':[.06]})),group_power=dict(status='inapplicable'),group_energy=dict(status='inapplicable'))
 headroom={('G',0):maximum-agc}
 cap=primary_fraction_capacities({'energy_inputs':{'thermal':{'G':{'max_mw':{0:maximum}}}}},0,[0],{'G':['pricing_stable']},headroom)if repaired else headroom
 add_physical(m,m.P,['G'],[0],{('G',0):30.},cap,{('G',0):1},{0:load},{0:0.},specs)
 m.joint=p.Constraint(expr=m.additional.primary['G',0]+m.accident['G',0]<=maximum-agc-m.P['G',0])
 m.obj=p.Objective(expr=.25*10.*m.P['G',0]);m.dual=p.Suffix(direction=p.Suffix.IMPORT)
 r=p.SolverFactory('appsi_highs').solve(m,load_solutions=False)
 if str(r.solver.termination_condition)=='optimal':m.solutions.load_from(r)
 return m,str(r.solver.termination_condition)

class PrimaryTests(unittest.TestCase):
 def test_same_sced_price_case(self):
  a,m,r=dispatch();self.assertEqual(str(r.solver.termination_condition),'optimal');self.assertAlmostEqual(p.value(m.primary['G',0]),6.)
  self.assertEqual(price(repaired=False)[1],'infeasible');self.assertEqual(price()[1],'optimal')
 def test_dual_and_finite_difference(self):
  m,s=price(window=(45.,55.));self.assertEqual(s,'optimal');mp,sp=price(load=50.001,window=(45.,55.));self.assertEqual(sp,'optimal')
  self.assertAlmostEqual(m.dual[m.balance]/.25,10.)
  self.assertAlmostEqual((p.value(mp.obj)-p.value(m.obj))/.001/.25,10.,places=6)
 def test_joint_headroom_not_relaxed(self):
  self.assertEqual(price(load=74.)[1],'optimal');self.assertEqual(price(load=74.001)[1],'infeasible')
 def test_no_agc_unchanged(self):
  for fix in (False,True):
   m,status=price(agc=0.,repaired=fix);self.assertEqual(status,'optimal');self.assertAlmostEqual(m.dual[m.balance]/.25,10.)
 def test_available_max_not_nameplate_or_price_band(self):
  # Derated available maximum 90, not nominal 100 or reference-window max 55.
  self.assertEqual(price(maximum=90.,requirement=5.4,window=(45.,55.))[1],'optimal')
  self.assertEqual(price(maximum=90.,requirement=5.401,window=(45.,55.))[1],'infeasible')
 def test_off_and_transition_unchanged(self):
  c={'energy_inputs':{'thermal':{'G':{'max_mw':{0:100.,1:100.}}}}}
  self.assertEqual(primary_fraction_capacities(c,0,[0,1],{'G':['off','fixed_nonpricing']},{('G',0):0.,('G',1):15.}),{('G',0):0.,('G',1):15.})
 def test_domain_wiring_keeps_joint_bound(self):
  from pathlib import Path
  s=(ROOT/'app/gd_live_market_runtime/domain_event.py').read_text()
  self.assertIn('add_physical(m,m.P,G,T,minimum,fraction_maximum,online,',s)
  self.assertIn('m.additional.primary[g,q]+m.accident[g,q]<=maximum[g,q]-m.P[g,q]',s)
if __name__=='__main__':unittest.main(verbosity=2)
