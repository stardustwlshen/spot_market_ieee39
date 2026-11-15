from pathlib import Path
import sys,copy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import pyomo.environ as p
from gd_provincial15_model.pumped_storage import attach,executed_state
import pytest

def setup(n=2):
 m=p.ConcreteModel();m.B=p.Set(initialize=['2']);m.T=p.Set(initialize=range(n));m.other_injection=p.Expression(m.B,m.T,rule=lambda *_:0);m.other_cost=p.Expression(expr=0)
 params=dict(enabled=True,variable_speed=False,bus=2,n_units=1,unit_gen_min=150,unit_gen_max=300,unit_pump=300,e_gen=9.6,e_pump=12.6,v_total=520,vup_min=60,vup_max=450,vdn_min=60,vdn_max=470,start_cost_gen=2000,start_cost_pump=4000,switch_gap=1,max_gen_starts=4,max_pump_starts=2)
 inv={'PS1':dict(parameters=params,upper_m3=2000000,lower_m3=3200000,modes={'PS1_1':'off'})}
 dec={'PS1':dict(source='test assumptions',basis='research',gen_bid_yuan_mwh=10,pump_bid_yuan_mwh=0,band_m3=1e6,band_penalty_yuan_m3=1,da_upper_m3={t:2e6 for t in range(n)},da_lower_m3={t:3.2e6 for t in range(n)},initial_units={'PS1_1':dict(gen_starts_used=0,pump_starts_used=0,last_active_mode='off',opposite_mode_block_steps=0)})}
 return m,inv,dec

def solve(m):
 m.obj=p.Objective(expr=m.other_cost);return p.SolverFactory('appsi_highs').solve(m)

def test_generate_water_cost_and_net():
 m,i,d=setup(1);b=attach(m,i,d);b.gen['PS1','PS1_1',0].fix(150);solve(m)
 assert p.value(b.upper['PS1',0])==pytest.approx(2e6-150*.25*10000/9.6)
 assert p.value(b.upper['PS1',0]+b.lower['PS1',0])==pytest.approx(5.2e6)
 assert p.value(m.other_injection['2',0])==150
 assert p.value(m.other_cost)==pytest.approx(2375)
 state=executed_state(m,0)['PS1'];assert state['units']['PS1_1']['gen_starts_used']==1
 assert state['units']['PS1_1']['opposite_mode_block_steps']==1

def test_pump_fixed_and_volume_increases():
 m,i,d=setup(1);b=attach(m,i,d);b.pump_mode['PS1','PS1_1',0].fix(1);solve(m)
 assert p.value(b.pump['PS1','PS1_1',0])==300
 assert p.value(b.upper['PS1',0])==pytest.approx(2e6+300*.25*10000/12.6)
 assert p.value(m.other_injection['2',0])==-300

def test_simultaneous_and_direct_reversal_infeasible():
 m,i,d=setup();b=attach(m,i,d);b.gen_mode['PS1','PS1_1',0].fix(1);b.pump_mode['PS1','PS1_1',1].fix(1)
 with pytest.raises(RuntimeError):solve(m)
 m,i,d=setup(1);b=attach(m,i,d);b.gen_mode['PS1','PS1_1',0].fix(1);b.pump_mode['PS1','PS1_1',0].fix(1)
 with pytest.raises(RuntimeError):solve(m)

def test_da_band_soft_not_water_hard():
 m,i,d=setup(1);d['PS1']['band_m3']=0;b=attach(m,i,d);b.gen['PS1','PS1_1',0].fix(150);solve(m)
 assert p.value(b.band_excess['PS1',0])==pytest.approx(150*.25*10000/9.6)

def test_initial_off_still_carries_switch_debt():
 m,i,d=setup(1);d['PS1']['initial_units']['PS1_1'].update(last_active_mode='gen',opposite_mode_block_steps=1);b=attach(m,i,d)
 assert b.pump_mode['PS1','PS1_1',0].fixed and p.value(b.pump_mode['PS1','PS1_1',0])==0

def test_daily_budget_not_reset():
 m,i,d=setup(1);d['PS1']['initial_units']['PS1_1']['gen_starts_used']=4;b=attach(m,i,d);b.gen_mode['PS1','PS1_1',0].fix(1)
 with pytest.raises(RuntimeError):solve(m)

def test_missing_da_reference_rejected():
 m,i,d=setup();del d['PS1']['da_upper_m3'][1]
 with pytest.raises(ValueError,match='complete DA'):attach(m,i,d)

def test_invalid_efficiency_rejected():
 m,i,d=setup();i['PS1']['parameters']['e_gen']=20
 with pytest.raises(ValueError,match='efficiency'):attach(m,i,d)

def test_DA_terminal_water_and_segment_cost():
 m,i,d=setup(96);d['PS1'].update(stage='DA',terminal_upper_m3=2e6,terminal_lower_m3=3.2e6,gen_segments=[dict(width_mw=150,price_yuan_mwh=10),dict(width_mw=150,price_yuan_mwh=20)])
 b=attach(m,i,d);b.pump_mode['PS1','PS1_1',0].fix(1);solve(m)
 assert p.value(b.upper['PS1',95])==pytest.approx(2e6)
 assert p.value(b.lower['PS1',95])==pytest.approx(3.2e6)
 assert p.value(sum(b.gen['PS1','PS1_1',t]*.25 for t in m.T))==pytest.approx(75*9.6/12.6)
 assert all(p.value(b.band_excess['PS1',t])==0 for t in m.T)

def test_DA_missing_terminal_rejected():
 m,i,d=setup(96);d['PS1']['stage']='DA'
 with pytest.raises(KeyError):attach(m,i,d)

def test_new_DA_reference_binding_and_first_state(tmp_path):
 import json,hashlib
 from gd_provincial15_model.pumped_declarations import realtime
 m,i,d=setup(1)
 cells=[dict(tick=t,pumped={'PS1':dict(upper_m3=2000000+t,lower_m3=3200000-t)}) for t in range(96)]
 f=tmp_path/'physical.json';f.write_text(json.dumps(dict(schema='gd.provincial.da_physical.v1',grid_minutes=15,physical_cells=cells)));sha=hashlib.sha256(f.read_bytes()).hexdigest()
 state={'PS1':dict(upper_m3=1990000,lower_m3=3210000,net_power_mw=150,units={'PS1_1':dict(mode='gen',last_active_mode='gen',opposite_mode_block_steps=1,gen_starts_used=1,pump_starts_used=0)})}
 inv,decl=realtime(i,d,physical_path=f,expected_sha256=sha,first_tick=1,count=8,boundary=state,boundary_day_tick=0,band_m3=10000,band_penalty_yuan_m3=1)
 assert inv['PS1']['upper_m3']==1990000
 assert decl['PS1']['da_upper_m3'][0]==2000001
 assert decl['PS1']['da_upper_m3'][7]==2000008
 assert decl['PS1']['initial_units']['PS1_1']['gen_starts_used']==1
 with pytest.raises(ValueError,match='immediately'):
  realtime(i,d,physical_path=f,expected_sha256=sha,first_tick=1,count=8,boundary=state,boundary_day_tick=7,band_m3=10000,band_penalty_yuan_m3=1)
 f.write_text('{}')
 with pytest.raises(ValueError,match='changed'):
  realtime(i,d,physical_path=f,expected_sha256=sha,first_tick=1,count=8,boundary=state,boundary_day_tick=0,band_m3=10000,band_penalty_yuan_m3=1)

def test_raw_handoff_only_first_interval():
 from gd_provincial15_model.pumped_base import derive_pumped_carry
 m,i,d=setup(2);b=attach(m,i,d);b.gen['PS1','PS1_1',0].fix(150);b.gen['PS1','PS1_1',1].fix(300);solve(m)
 vs=list(m.component_data_objects(p.Var));names={'variables':[v.name for v in vs]};raw={'has_primal':True,'primal':[p.value(v) for v in vs]}
 case={'battery_contract':{'storage':{'pumped_storage':{'inventory':i}}},'pumped_storage_declarations':d}
 carry=derive_pumped_carry(case,raw,names)
 assert carry['PS1']['upper_m3']==pytest.approx(2e6-150*.25*10000/9.6)
 assert carry['PS1']['upper_m3']!=pytest.approx(p.value(b.upper['PS1',1]))
 assert carry['PS1']['units']['PS1_1']['gen_starts_used']==1
 with pytest.raises(ValueError,match='first'):derive_pumped_carry(case,raw,names,1)

def test_DA98_terminal_is_day95_not_representative97():
 m,i,d=setup(98);d['PS1'].update(stage='DA',terminal_upper_m3=2e6,terminal_lower_m3=3.2e6)
 b=attach(m,i,d)
 for t in range(96):
  b.gen_mode['PS1','PS1_1',t].fix(0);b.pump_mode['PS1','PS1_1',t].fix(0)
 b.pump_mode['PS1','PS1_1',96].fix(1);b.gen_mode['PS1','PS1_1',96].fix(0)
 b.pump_mode['PS1','PS1_1',97].fix(0);b.gen_mode['PS1','PS1_1',97].fix(0)
 solve(m)
 assert p.value(b.upper['PS1',95])==pytest.approx(2e6)
 assert p.value(b.upper['PS1',97])==pytest.approx(2e6+300*.25*10000/12.6)
 assert p.value(b.lower['PS1',97])==pytest.approx(3.2e6-300*.25*10000/12.6)
 # Model-indexed representative contributes to water and starts, not D terminal.
 assert p.value(b.pump_start['PS1','PS1_1',96])==1

def test_independent_PS_price_keeps_lp_modes_and_source(tmp_path):
 import hashlib
 sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engines/guangdong_short_unified_v6/rt/guangdong_rt_recommit_program_v1_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src'))
 from smm.gd_october_job_codec import dumps
 from gd_provincial15_model.pumped_price import attach_from_prepared
 m,i,d=setup(1);case={'battery_contract':{'storage':{'pumped_storage':{'inventory':i}}},'pumped_storage_declarations':d}
 f=tmp_path/'derived_case.json';f.write_bytes(dumps(case));pins={str(f.resolve()):hashlib.sha256(f.read_bytes()).hexdigest()}
 x={f'pumped.{name}[PS1,PS1_1,4]':v for name,v in {'gen_mode':1,'pump_mode':0,'gen_start':1,'pump_start':0,'gen':150,'pump':0}.items()}
 result=attach_from_prepared(m,tmp_path,x,4,.1,source_pins=pins)
 solve(m)
 assert p.value(m.pumped.gen['PS1','PS1_1',0])==pytest.approx(150)
 assert all(not v.is_integer() for v in m.component_data_objects(p.Var))
 assert result['trace'][0]['absolute_tick']==4
 m,i,d=setup(1)
 with pytest.raises(ValueError,match='source-bound'):attach_from_prepared(m,tmp_path,x,4,.1,source_pins={})
