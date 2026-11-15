"""Same-node plant interlock, not a prohibition across independent resources."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import pyomo.environ as p
from gd_provincial15_model.pumped_storage import attach

def make(stage='RT'):
    n=96 if stage=='DA' else 1
    m=p.ConcreteModel();m.B=p.Set(initialize=['2']);m.T=p.Set(initialize=range(n));m.other_injection=p.Expression(m.B,m.T,rule=lambda *_:0);m.other_cost=p.Expression(expr=0)
    params=dict(enabled=True,variable_speed=False,bus=2,n_units=2,unit_gen_min=150,unit_gen_max=300,unit_pump=300,e_gen=9.6,e_pump=12.6,v_total=520,vup_min=60,vup_max=450,vdn_min=60,vdn_max=470,start_cost_gen=2000,start_cost_pump=4000,switch_gap=1,max_gen_starts=4,max_pump_starts=2)
    units=['PS1_1','PS1_2'];inv={'PS1':dict(parameters=params,upper_m3=2e6,lower_m3=3.2e6,modes={u:'off' for u in units})}
    decl={'PS1':dict(stage=stage,source='test assumptions',basis='research',gen_bid_yuan_mwh=420,pump_bid_yuan_mwh=300,band_m3=1e6,band_penalty_yuan_m3=1,da_upper_m3={t:2e6 for t in m.T},da_lower_m3={t:3.2e6 for t in m.T},terminal_upper_m3=2e6,terminal_lower_m3=3.2e6,initial_units={u:dict(gen_starts_used=0,pump_starts_used=0,last_active_mode='off',opposite_mode_block_steps=0,mode_age_steps=10,gen_mw=0.,pump_mw=0.) for u in units})}
    b=attach(m,inv,decl);m.obj=p.Objective(expr=m.other_cost);return m

def solve(m):
    return str(p.SolverFactory('appsi_highs').solve(m,load_solutions=False).solver.termination_condition)

def test_rt_mixed_units_rejected():
    m=make();m.pumped.gen['PS1','PS1_1',0].fix(200);m.pumped.pump['PS1','PS1_2',0].fix(300)
    assert solve(m)=='infeasible'

def test_fixed_integer_price_cannot_accept_mixed_witness():
    m=make();b=m.pumped
    for u,g,pump in [('PS1_1',1,0),('PS1_2',0,1)]:
        for name,v in [('gen_mode',g),('gen_start',g),('pump_mode',pump),('pump_start',pump)]:
            z=getattr(b,name)['PS1',u,0];z.fix(v);z.domain=p.Reals
    assert solve(m)=='infeasible'

def test_da_mixed_units_rejected():
    m=make('DA');m.pumped.gen['PS1','PS1_1',30].fix(200);m.pumped.pump['PS1','PS1_2',30].fix(300)
    assert solve(m)=='infeasible'

def test_parallel_generating_allowed():
    m=make()
    for u in ['PS1_1','PS1_2']:m.pumped.gen['PS1',u,0].fix(200)
    assert solve(m)=='optimal'

def test_parallel_pumping_allowed():
    m=make()
    for u in ['PS1_1','PS1_2']:m.pumped.pump['PS1',u,0].fix(300)
    assert solve(m)=='optimal'

def test_real_price_attach_rejects_old_mixed_witness(tmp_path):
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'engines/guangdong/guangdong_after_reset_r23_compressed_20261112/repo/src'))
    from smm.gd_october_job_codec import dumps
    from gd_provincial15_model.pumped_price import attach_from_prepared
    import hashlib
    original=make();z=original._pumped_data['PS1']
    inv={'PS1':dict(parameters=z['p'],upper_m3=z['up'],lower_m3=z['lo'],modes=z['initial_modes'])}
    case=dict(battery_contract={'storage':{'pumped_storage':{'inventory':inv}}},pumped_storage_declarations={'PS1':z['d']})
    path=tmp_path/'derived_case.json';path.write_bytes(dumps(case));absolute={}
    for u,g,pump in [('PS1_1',1,0),('PS1_2',0,1)]:
        for name,value in [('gen_mode',g),('gen_start',g),('pump_mode',pump),('pump_start',pump),('gen',g*200),('pump',pump*300)]:absolute[f'pumped.{name}[PS1,{u},0]']=value
    m=p.ConcreteModel();m.B=p.Set(initialize=['2']);m.T=p.Set(initialize=[0]);m.other_injection=p.Expression(m.B,m.T,rule=lambda *_:0);m.other_cost=p.Expression(expr=0)
    attach_from_prepared(m,tmp_path,absolute,0,.01,source_pins={str(path.resolve()):hashlib.sha256(path.read_bytes()).hexdigest()})
    m.obj=p.Objective(expr=m.other_cost)
    assert all(v.fixed for name in ('gen_mode','pump_mode','gen_start','pump_start') for v in getattr(m.pumped,name).values())
    assert solve(m)=='infeasible'

def test_different_plants_same_bus_can_use_opposite_modes():
    from copy import deepcopy
    original=make();z=original._pumped_data['PS1'];inventory={};declarations={}
    for station in ('A','B'):
        par=deepcopy(z['p']);par['n_units']=1;u=station+'_1'
        inventory[station]=dict(parameters=par,upper_m3=z['up'],lower_m3=z['lo'],modes={u:'off'})
        d=deepcopy(z['d']);d['initial_units']={u:deepcopy(z['d']['initial_units']['PS1_1'])};declarations[station]=d
    m=p.ConcreteModel();m.B=p.Set(initialize=['2']);m.T=p.Set(initialize=[0]);m.other_injection=p.Expression(m.B,m.T,rule=lambda *_:0);m.other_cost=p.Expression(expr=0)
    b=attach(m,inventory,declarations);b.gen['A','A_1',0].fix(200);b.pump['B','B_1',0].fix(300);m.obj=p.Objective(expr=m.other_cost)
    assert solve(m)=='optimal'
