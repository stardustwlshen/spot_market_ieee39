import importlib.util, pathlib, copy
import pyomo.environ as p
spec=importlib.util.spec_from_file_location('ps',pathlib.Path(__file__).resolve().parents[1]/'app/gd_provincial15_model/pumped_storage.py');ps=importlib.util.module_from_spec(spec);spec.loader.exec_module(ps)
params=dict(enabled=True,variable_speed=False,bus=2,n_units=1,unit_gen_min=150,unit_gen_max=300,unit_pump=300,e_gen=9.6,e_pump=12.6,v_total=520,vup_min=60,vup_max=450,vdn_min=60,vdn_max=470,start_cost_gen=2000,start_cost_pump=4000,switch_gap=1,max_gen_starts=4,max_pump_starts=2,min_gen_time_h=1,min_pump_time_h=1,min_off_time_h=.5)
def model(n=3,mode='off',age=10,gen=0,pump=0,overrides=None):
 m=p.ConcreteModel();m.B=p.Set(initialize=['2']);m.T=p.Set(initialize=range(n));m.other_injection=p.Expression(m.B,m.T,rule=lambda *_:0);m.other_cost=p.Expression(expr=0)
 par=dict(params,**(overrides or {}));i={'PS1':dict(parameters=par,upper_m3=2e6,lower_m3=3.2e6,modes={'PS1_1':mode})}
 d={'PS1':dict(source='audit research inputs',basis='research',gen_bid_yuan_mwh=10,pump_bid_yuan_mwh=0,band_m3=1e6,band_penalty_yuan_m3=1,da_upper_m3={t:2e6 for t in m.T},da_lower_m3={t:3.2e6 for t in m.T},initial_units={'PS1_1':dict(gen_starts_used=int(mode=='gen'),pump_starts_used=int(mode=='pump'),last_active_mode=mode,opposite_mode_block_steps=0,mode_age_steps=age,gen_mw=gen,pump_mw=pump)})}
 b=ps.attach(m,i,d);m.obj=p.Objective(expr=m.other_cost);return m

def status(m):
 solver=p.SolverFactory('appsi_highs');r=solver.solve(m,load_solutions=False)
 if str(r.solver.termination_condition)=='optimal':solver.load_vars()
 return str(r.solver.termination_condition)
def fix(m,gen,pump):
 for t in m.T:m.pumped.gen['PS1','PS1_1',t].fix(gen[t]);m.pumped.pump['PS1','PS1_1',t].fix(pump[t])
results={}
m=model();fix(m,[300,0,300],[0,0,0]);assert status(m)=='infeasible';results['generation_chatter']='rejected'
m=model();fix(m,[0,0,0],[300,0,300]);assert status(m)=='infeasible';results['pumping_chatter']='rejected'
m=model(mode='off',age=1);fix(m,[300,300,300],[0]*3);assert status(m)=='infeasible';results['carried_off_debt']='rejected'
m=model(mode='gen',age=1,gen=300);fix(m,[0,0,0],[0]*3);assert status(m)=='infeasible';results['carried_gen_debt']='rejected'
m=model(n=2);fix(m,[300,300],[0]*2);assert status(m)=='optimal';z=ps.executed_state(m,0)['PS1']['units']['PS1_1'];assert z['mode_age_steps']==1 and z['gen_mw']==300;assert ps.executed_state(m,1)['PS1']['units']['PS1_1']['mode_age_steps']==2;results['first_only_carry']='age1_not_tail2'
m=model(n=1,mode='gen',age=1,gen=300);fix(m,[0],[0]);assert status(m)=='infeasible';results['new_process_residual']='rejected'
m=model(n=1,overrides={'gen_up_step_mw':200});fix(m,[300],[0]);assert status(m)=='infeasible';results['initial_generation_ramp']='rejected'
m=model(n=1,mode='pump',age=4,pump=300,overrides={'pump_release_step_mw':150});fix(m,[0],[0]);assert status(m)=='infeasible';results['signed_pump_ramp']='rejected'
m=model(n=2,mode='gen',age=4,gen=300);fix(m,[0,0],[0,0]);assert status(m)=='optimal';assert ps.executed_state(m,1)['PS1']['units']['PS1_1']['mode_age_steps']==2;results['shutdown_and_age']='accepted'
import json;print(json.dumps(results,indent=2))
# Independent raw-SCED carry extraction agrees with component executed_state.
spec2=importlib.util.spec_from_file_location('psbase',pathlib.Path(__file__).resolve().parents[1]/'app/gd_provincial15_model/pumped_base.py');base=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(base)
m=model(n=2);fix(m,[300,300],[0,0]);assert status(m)=='optimal'
z=m._pumped_data['PS1'];case={'battery_contract':{'storage':{'pumped_storage':{'inventory':{'PS1':{'parameters':z['p'],'modes':z['initial_modes'],'upper_m3':z['up'],'lower_m3':z['lo']}}}}},'pumped_storage_declarations':{'PS1':z['d']}}
variables=list(m.component_data_objects(p.Var));names={'variables':[v.name for v in variables]};raw={'has_primal':True,'primal':[p.value(v) for v in variables]}
actual=base.derive_pumped_carry(case,raw,names)['PS1'];expected=ps.executed_state(m,0)['PS1']
assert actual['units']==expected['units'];assert actual['upper_m3']==expected['upper_m3'];print('raw_SCED_first_step_carry: PASS')
