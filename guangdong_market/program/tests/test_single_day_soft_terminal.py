import sys
from pathlib import Path
from datetime import datetime
import pyomo.environ as p
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from gd_provincial15_model.soft_terminal import attach

def test_rt_terminal_deviation_is_soft_but_physical_bounds_hard():
 m=p.ConcreteModel();m.battery=p.Block();b=m.battery;b.BAT=p.Set(initialize=['B']);b.energy=p.Var(b.BAT,[0],bounds=(0,100));b.c_terminal=p.Constraint(expr=b.energy['B',0]==50)
 case={'batteries':{'B':dict(terminal_at='2026-11-12T00:00:00+08:00',terminal_energy_mwh=50,energy_capacity_mwh=100,roundtrip_efficiency=1,charge_max_mw=20,discharge_max_mw=20)}}
 r=attach(m,case,begin=datetime.fromisoformat('2026-11-11T23:45:00+08:00'),periods=1)
 b.energy['B',0].fix(40);m.obj=p.Objective(expr=m.rt_terminal_target_cost)
 result=p.SolverFactory('appsi_highs').solve(m)
 assert str(result.solver.termination_condition)=='optimal'
 assert not b.c_terminal.active and b.energy['B',0].lb==0 and b.energy['B',0].ub==100
 assert p.value(m.rt_target_under['B'])==8 and p.value(m.rt_terminal_target_cost)==8000
 assert r['not_operating_cost'] is True

def test_window_end_and_target_at_midnight():
 m=p.ConcreteModel();m.battery=p.Block();b=m.battery;b.BAT=p.Set(initialize=['B']);b.energy=p.Var(b.BAT,[0,1],bounds=(0,100));b.c_terminal=p.ConstraintList()
 case={'batteries':{'B':dict(terminal_at='2026-11-12T00:00:00+08:00',terminal_energy_mwh=50,energy_capacity_mwh=100,roundtrip_efficiency=1,charge_max_mw=20,discharge_max_mw=20)}}
 r=attach(m,case,begin=datetime.fromisoformat('2026-11-11T23:30:00+08:00'),periods=2)
 assert r['resources']['B']['window_end_at']=='2026-11-12T00:00:00+08:00'

def test_actual_contract_truncates_last_windows_without_next_day():
 import ast
 from datetime import timedelta
 path=Path(__file__).resolve().parents[1]/'app/gd_provincial15_model/short_support.py'
 tree=ast.parse(path.read_text());node=next(x for x in tree.body if isinstance(x,ast.FunctionDef)and x.name=='validate_contract')
 ns=dict(datetime=datetime,timedelta=timedelta);exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),ns)
 origin=datetime.fromisoformat('2026-11-11T00:00:00+08:00')
 for w in (0,88,89,94,95):
  at=origin+timedelta(minutes=15*w);n=min(8,96-w);c=dict(schema='test',rt_clock_mode='provincial_15min_execution',origin=origin.isoformat(),cutoff=at.isoformat(),domain_start=at.isoformat(),publication_at=(origin-timedelta(hours=3)).isoformat())
  r=ns['validate_contract'](c,n)
  assert len(r['grid'])==n and at+timedelta(minutes=15*n)<=origin+timedelta(days=1)
  if w==95:
   import pytest
   with pytest.raises(ValueError,match='single-day exact truncated'):ns['validate_contract'](c,8)

def test_slack_penalty_coefficients_and_native_perturbation():
 from gd_provincial15_model.objective_components import append_slack_penalties
 from pyomo.repn.standard_repn import generate_standard_repn
 m=p.ConcreteModel();m.line_slack=p.Var(domain=p.NonNegativeReals);m.balance_slack=p.Var(domain=p.NonNegativeReals);m.reserve_slack=p.Var(domain=p.NonNegativeReals)
 m.network_penalty_cost=p.Expression(expr=.25*10000*m.line_slack)
 m.balance_penalty_cost=p.Expression(expr=.25*200000*m.balance_slack)
 m.reserve_penalty_cost=p.Expression(expr=.25*100000*m.reserve_slack)
 terms=[];names=append_slack_penalties(m,terms);m.obj=p.Objective(expr=sum(terms));rep=generate_standard_repn(m.obj.expr)
 coeff={v.name:float(c)for v,c in zip(rep.linear_vars,rep.linear_coefs)}
 assert coeff=={'line_slack':2500.,'balance_slack':50000.,'reserve_slack':25000.}
 m.force=p.Constraint(expr=m.line_slack>=2)
 result=p.SolverFactory('appsi_highs').solve(m)
 assert str(result.solver.termination_condition)=='optimal' and p.value(m.obj)==5000.
 assert p.value(m.balance_slack)==0 and p.value(m.reserve_slack)==0
