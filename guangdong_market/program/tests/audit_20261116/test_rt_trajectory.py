from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
APP=ROOT/'app'
VENDOR=ROOT/'engines/guangdong_v3/guangdong_after_reset_r23_compressed_20261112/repo/src'
sys.path[:0]=[str(APP),str(VENDOR)]
import copy,unittest
from datetime import datetime,timedelta
import pyomo.environ as p
from smm.gd_horizon import EnergyHorizon,EnergyPeriod
from gd_provincial15_model.thermal_physical import build_free_scuc
from gd_provincial15_model.short_energy_core import build_energy_core,EnergyInputError

def fixture(phase,elapsed=1):
 n=3-elapsed; start='2026-01-01T23:'+('30' if n==2 else '45')+':00+08:00'; origin='2026-01-01T00:00:00+08:00'
 series=lambda v:{t:v for t in range(n)}
 curves={'startup':[dict(begin=10*i,mean=10*i+5,end=10*i+10,cost=1)for i in range(3)],'shutdown':[dict(begin=30-10*i,mean=25-10*i,end=20-10*i,cost=1)for i in range(3)]}
 rows=curves[phase][elapsed:]
 unit=dict(bus='B',bid_base_mw=30.,base_bid_yuan_per_h=series(300),segments=(dict(key='s',width_mw=70.,price_yuan_per_mwh=series(10.)),),min_mw=series(30.),max_mw=series(100.),ramp_up_mw_per_h=series(100.),ramp_down_mw_per_h=series(100.),startup_output_limit_mw=series(30.),shutdown_output_limit_mw=series(30.),startup_yuan=series(100.),shutdown_yuan=series(100.),min_up_h=1.,min_down_h=1.,initial_online=1,initial_power_mw=rows[0]['begin'],initial_state_age_h=elapsed*.25,initial_state_kind='research_assumption_trajectory',status=series('free'),fixed_power_mw=series(None),max_starts=None,max_stops=None,transition_model='instant_with_output_limits',terminal_policy='carry_out',source='test')
 e=dict(horizon=EnergyHorizon(tuple(EnergyPeriod(t,.25,.25,'delivery',t==0)for t in range(n)),'real_time','regression'),buses=('B',),thermal={'G':unit},load_mw={('B',t):r['mean']for t,r in enumerate(rows)},renewable_mw={('B',t):0. for t in range(n)},physical_tie_mw={('B',t):0. for t in range(n)},lines={},sections={},reference_buses=('B',),penalties_yuan_per_mwh=dict(line=10000.,section=10000.,balance_shortfall=100000.,balance_surplus=100000.),source_notes={key:'test' for key in ('thermal','transition','network','penalties','exogenous','boundary_basis','balance_policy','shortfall_cap_policy','renewable_policy')},balance_policy='hard',shortfall_cap_policy='unbounded_slack',renewable_mode='dispatchable_bus_aggregate',renewable_bid_yuan_per_mwh={('B',t):0. for t in range(n)},renewable_curtailment_penalty_yuan_per_mwh={('B',t):0. for t in range(n)})
 boundary={'G':dict(phase=phase,available_at=start,source='first executed period end',min_up_basis='startup_begin',min_down_basis='shutdown_end',residual_up_ticks=max(0,4-elapsed)if phase=='startup'else 0,residual_down_ticks=0,event_elapsed_ticks=elapsed,end_power_mw=rows[0]['begin'],event_id='event-1',remaining_curve=rows)}
 reserve={key:series(0.)for key in ('positive_mw','negative_mw','primary_mw','contingency_mw')};reserve.update(primary_fraction={'G':0.1},contingency_eligible={'G':True})
 return dict(energy_inputs=e,reserve=reserve,curves={'G':dict(**curves,basis='research',source='regression',available_at=origin)},contract=dict(schema='regression',origin=origin,cutoff=start,domain_start=start,publication_at=origin,rt_clock_mode='provincial_15min_execution'),boundary_states=boundary)

class TrajectoryTests(unittest.TestCase):
 def test_startup_and_shutdown_continuation(self):
  for phase in ('startup','shutdown'):
   with self.subTest(phase=phase):
    a=fixture(phase);m=build_free_scuc(**a);r=p.SolverFactory('appsi_highs').solve(m)
    self.assertEqual(str(r.solver.termination_condition),'optimal')
    first_end=p.value(m.end_power['G',0]);window_end=p.value(m.end_power['G',1]);self.assertNotEqual(first_end,window_end)
    for t,row in enumerate(a['boundary_states']['G']['remaining_curve']):
     self.assertAlmostEqual(p.value(m.power['G',t]),row['mean']);self.assertAlmostEqual(p.value(m.end_power['G',t]),row['end'])
     self.assertEqual(round(p.value(m.start['G',t])),0);self.assertEqual(round(p.value(m.stop['G',t])),0)
    b=fixture(phase,2);self.assertEqual(b['boundary_states']['G']['end_power_mw'],first_end)
    m2=build_free_scuc(**b);r=p.SolverFactory('appsi_highs').solve(m2);self.assertEqual(str(r.solver.termination_condition),'optimal')
    self.assertAlmostEqual(p.value(m2.power['G',0]),p.value(m.power['G',1]));self.assertAlmostEqual(p.value(m2.end_power['G',0]),window_end)
    # Forced off during an inherited transition must remain physically infeasible.
    m2.online['G',0].fix(0);r=p.SolverFactory('appsi_highs').solve(m2,load_solutions=False)
    self.assertEqual(str(r.solver.termination_condition),'infeasible')
 def test_bad_endpoint_and_reset_up_debt_rejected(self):
  for phase in ('startup','shutdown'):
   a=fixture(phase);a['boundary_states']['G']['end_power_mw']+=1
   with self.assertRaisesRegex(ValueError,'true boundary power'):build_free_scuc(**a)
  a=fixture('startup');a['boundary_states']['G']['residual_up_ticks']=0
  with self.assertRaisesRegex(ValueError,'debt cannot reset'):build_free_scuc(**a)
 def test_not_generic_trajectory_bypass(self):
  e=fixture('startup')['energy_inputs']
  with self.assertRaisesRegex(EnergyInputError,'unfinished initial'):build_energy_core(**e)
  with self.assertRaisesRegex(EnergyInputError,'unfinished initial'):build_energy_core(**e,_trajectory_initial_units=('OTHER',))
 def test_oversized_rt_still_denied(self):
  e=fixture('startup')['energy_inputs'];e['horizon']=EnergyHorizon(tuple(EnergyPeriod(t,.25,.25,'delivery',t==0)for t in range(9)),'real_time','regression')
  for key in ('load_mw','renewable_mw','physical_tie_mw','renewable_bid_yuan_per_mwh','renewable_curtailment_penalty_yuan_per_mwh'):e[key]={('B',t):0. for t in range(9)}
  with self.assertRaisesRegex(EnergyInputError,'short-domain'):build_energy_core(**e,_trajectory_initial_units=('G',))
 def test_da_mode_still_denied(self):
  e=fixture('startup')['energy_inputs'];e['horizon']=EnergyHorizon(tuple(EnergyPeriod(t,1/12,1/12,'delivery',True)for t in range(288)),'day_ahead_research_5min','regression')
  for key in ('load_mw','renewable_mw','physical_tie_mw','renewable_bid_yuan_per_mwh','renewable_curtailment_penalty_yuan_per_mwh'):e[key]={('B',t):0. for t in range(288)}
  with self.assertRaisesRegex(EnergyInputError,'short-domain'):build_energy_core(**e,_trajectory_initial_units=('G',))
if __name__=='__main__':unittest.main(verbosity=2)
