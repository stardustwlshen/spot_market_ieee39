import pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'app'))
import pyomo.environ as p
from gd_provincial15_model.resource_reserve import attach

def battery(mode,charge,discharge,energy):
 m=p.ConcreteModel();m.T=p.Set(initialize=[0]);b=m.battery=p.Block();b.BAT=p.Set(initialize=['B'])
 for name,value in [('charge',charge),('discharge',discharge),('discharge_mode',int(mode=='discharge')),('energy',energy)]:
  v=p.Var(b.BAT,m.T);setattr(b,name,v);v['B',0].fix(value)
 m._battery_inputs={'B':dict(_eta=.9,discharge_max_mw=100,energy_min_mwh=10,ramp_up_mw_per_h=600)}
 return m

def maximize(m,component):
 m.obj=p.Objective(expr=component,sense=p.maximize);r=p.SolverFactory('appsi_highs').solve(m);assert str(r.solver.termination_condition)=='optimal';return p.value(component)

def test_battery_soc_and_current_mode_limit():
 m=battery('discharge',0,40,15);b=attach(m);assert abs(maximize(m,b.up[0])-18)<1e-7
 assert p.value(b.primary[0])==0

def test_charging_reduction_not_charging_to_discharge_flip():
 m=battery('charge',30,0,100);b=attach(m);assert maximize(m,b.up[0])==30

def test_empty_battery_no_discharge_reserve():
 m=battery('discharge',0,0,10);b=attach(m);assert maximize(m,b.up[0])==0

def test_pumped_primary_online_not_nameplate_or_pump_trip():
 m=p.ConcreteModel();m.T=p.Set(initialize=[0]);b=m.pumped=p.Block();b.S=p.Set(initialize=['PS']);b.U=p.Set(initialize=[('PS','U')],dimen=2)
 for name,value in [('gen_mode',1),('gen',180)]:
  v=p.Var(b.U,m.T);setattr(b,name,v);v['PS','U',0].fix(value)
 for name,value in [('upper',110000),('lower',100000)]:
  v=p.Var(b.S,m.T);setattr(b,name,v);v['PS',0].fix(value)
 m._pumped_data={'PS':dict(names=['U'],p=dict(unit_gen_max=300,vup_min=10,vdn_max=20,e_gen=10))}
 extra=attach(m);assert maximize(m,extra.primary[0])==30

def test_joint_primary_and_contingency_cannot_double_use_water():
 m=p.ConcreteModel();m.T=p.Set(initialize=[0]);b=m.pumped=p.Block();b.S=p.Set(initialize=['PS']);b.U=p.Set(initialize=[('PS','U')],dimen=2)
 for name,value in [('gen_mode',1),('gen',180)]:
  v=p.Var(b.U,m.T);setattr(b,name,v);v['PS','U',0].fix(value)
 for name,value in [('upper',105000),('lower',100000)]:
  v=p.Var(b.S,m.T);setattr(b,name,v);v['PS',0].fix(value)
 m._pumped_data={'PS':dict(names=['U'],p=dict(unit_gen_max=300,vup_min=10,vdn_max=20,e_gen=10))}
 extra=attach(m)
 assert abs(maximize(m,extra.primary[0]+extra.up[0])-20)<1e-7

def test_pumped_response_uses_declared_dispatch_ramp():
 m=p.ConcreteModel();m.T=p.Set(initialize=[0]);b=m.pumped=p.Block();b.S=p.Set(initialize=['PS']);b.U=p.Set(initialize=[('PS','U')],dimen=2)
 for name,value in [('gen_mode',1),('gen',180)]:
  v=p.Var(b.U,m.T);setattr(b,name,v);v['PS','U',0].fix(value)
 for name,value in [('upper',190000),('lower',100000)]:
  v=p.Var(b.S,m.T);setattr(b,name,v);v['PS',0].fix(value)
 m._pumped_data={'PS':dict(names=['U'],p=dict(n_units=1,unit_gen_max=300,gen_up_step_mw=30,vup_min=10,vdn_max=20,e_gen=10))}
 extra=attach(m);assert abs(maximize(m,extra.up[0])-20)<1e-7

def test_response_fallback_marked_and_user_can_disable_or_lower():
 from gd_provincial15_model.resource_reserve import pumped_response
 p0={'unit_gen_max':300,'n_units':1}
 cap,proof=pumped_response(p0,1);assert cap==200 and 'not_certified' in proof['ramp_basis']
 assert pumped_response({**p0,'contingency_response_enabled':False},1)[0]==0
 assert pumped_response({**p0,'contingency_response_mw_10min':12},1)[0]==12
 assert pumped_response({**p0,'gen_up_step_mw':30,'contingency_response_mw_10min':90},1)[0]==20
