"""Explicit two-bus, 24 x 1-hour synthetic market, not IEEE39 acceptance."""
from copy import deepcopy
from pathlib import Path
import numpy as np
import pandas as pd
from smm.config import load_config
from smm.network import Network
from smm import hydro_commitment as hc
from smm.series import DayInputs,bid_hash
from smm.state_bridge import research_initial_state


def micro_day(date='2026-07-15',*,second_day=False,reverse_price=False,with_hydro=True,intraday=False):
    cfg=load_config();cfg.base.update(date=date,periods=24,dt_h=1.,simulation_timezone='UTC',line_scale={},line_capacity_multiplier=1.,forecast={'model':'legacy_v1'})
    cfg.base['solver'].update(time_limit_s=20,mip_rel_gap=1e-8,threads=4)
    cfg.mode['startup_cost_mode']='state_dependent'
    cfg.mode['auxiliary_pricing']['enabled']=False
    cfg.mode['regulation']['enabled']=False
    cfg.mode['bid_rules']={'profile':'generic','default':{'segments_min':1,'price_step':None,'min_segment_fraction':0}}
    cfg.mode['coal_first_segment']=None
    cfg.mode['reserve'].update(up_requirement_mode='formula',rho_L=0.,rho_RE=0.,rho_L_dn=0.,rho_RE_dn=0.,include_ctg=False,providers=[],storage_reserve=False,down_enabled=True,mu_RE=0.)
    cfg.mode['realtime'].update(interval_min=60,lookahead_h=2.,lock_types=['coal','gas_ccgt'])
    cfg.mode['realtime']['storage'].update(mode='value')
    cfg.mode['realtime']['hydro']='value'
    cfg.mode['intraday'].update(enabled=intraday,interval_h=6.,lead_h=0.,time_limit_s=15.,lock_types=['coal','gas_ccgt'])
    cfg.mode['pumped']['mode_step_periods']=1
    a=deepcopy(cfg.units['thermal'][0]);a.update(id='C1',bus=31,pmin=0.,pmax=100.,ramp=10000.,noload=1.,ut=4.,dt=3.,init={'online':True,'p0':50.,'hours':48.})
    b=deepcopy(a);b.update(id='C2',bus=32,pmin=20.,init={'online':False,'p0':0.,'hours':48.})
    s=deepcopy(cfg.units['storage'][0]);s.update(id='ES1',bus=32,pmax=5.,ecap=20.,soc0=.5,soc_min=.1,soc_max=.9)
    p=deepcopy(cfg.units['pumped'][0]);p.update(id='PS1',bus=32,n_units=1,unit_gen_max=5.,unit_gen_min=1.,unit_pump=5.,unit_pump_min=1.,variable_speed=True,
        e_gen=10.,e_pump=12.,v_total=300.,vup_min=80.,vup_max=280.,vdn_min=20.,vdn_max=220.,vup0=110.,vup_end_bid=None,init_mode='idle',switch_gap=3,max_gen_starts=20,max_pump_starts=20)
    hyd=[]
    if with_hydro:
        h=deepcopy(cfg.units['hydro'][0]);h.update(id='H1',bus=31,n_units=1,unit_pmax=5.,zones=[[0.,5.]],ramp=100.,water_rate=1.,init_units_on=0,init_p_unit=0.,ut=3.,dt=2.,regulation='reservoir')
        h['seasons']={k:dict(v0=100.,vmin=0.,vmax=200.,v_end=90.,qin=1.,qeco=0.,e_day=50.,e_day_min=None) for k in ('wet','dry')}
        h['commitment']=hc.research_initial(h,elapsed_h=1.)
        j=deepcopy(h);j.update(id='H2',bus=32,upstream='H1',lag_periods=3);j['commitment']=hc.research_initial(j,elapsed_h=1.)
        hyd=[h,j]
    cfg.units=dict(thermal=[a,b],storage=[s],pumped=[p],hydro=hyd,renewable=[],tie=[],_disabled=[])
    net=Network([31,32],pd.DataFrame([dict(branch_id='L',from_bus=31,to_bus=32,x_pu=.1,rate_mw=1000.)]),np.array([[0.,-1.]]),pd.Series([0.,1.],index=[31,32]),100.,ref_bus=31)
    load=np.full(24,60.) if second_day else np.r_[np.full(23,50.),150.]
    profiles=pd.DataFrame(dict(period=np.arange(1,25),**{f'load_{x}':load for x in ('actual','da','id','rt')}))
    prices={'C1':150. if reverse_price else 10.,'C2':5. if reverse_price else 100.}
    bids=dict(rule_set=cfg.rule_set,date=date,version=2 if second_day else 1,units={g:dict(segments=[dict(p_start=0.,p_end=100.,price=prices[g])],startup_cost=dict(hot=0.,warm=0.,cold=0.),noload_cost=1.) for g in ('C1','C2')})
    bids['units']['ES1']=dict(mode='price_quantity',dis_segments=[dict(p_start=0.,p_end=5.,price=500.)],ch_segments=[dict(p_start=0.,p_end=5.,price=0.)],soc_min=.1,soc_max=.9,soc_end=None)
    bids['units']['PS1']=dict(mode='price_quantity',gen_segments=[dict(p_start=0.,p_end=5.,price=50.)],pump_segments=[dict(p_start=0.,p_end=5.,price=0.)],vup_end=None)
    for h in hyd:bids['units'][h['id']]=dict(mode='price_quantity',segments=[dict(p_start=0.,p_end=5.,price=20.)])
    receipt=dict(date=date,version=bids['version'],confirmed=True,bid_hash=bid_hash(bids))
    day=DayInputs(cfg,net,profiles,bids,receipt)
    initial=research_initial_state(cfg,acknowledge_constant_prehistory=True)
    initial.storage['ES1']=12.3456789012345
    initial.pumped['PS1']['V']=115.1234567890123
    if with_hydro:
        initial.hydro.update(H1=101.1234567890123,H2=102.2345678901234)
        initial.continuation['day_initial_hydro']=deepcopy(initial.hydro)
    return day,initial
