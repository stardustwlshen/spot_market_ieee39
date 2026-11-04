"""No silent resource/load omission, capacity truncation or invented initial water."""
from copy import deepcopy
import numpy as np
import pandas as pd
import pyomo.environ as pyo
import pytest

from smm.config import load_config
from smm.input_validation import validate_physical_inputs
from smm.case import build_da_case
from smm.bids import default_bids
from smm.forecast import generate_profiles, validate_profiles
from smm.network import build_network
from smm import scuc, rolling
from test_da_stages import tiny_case
from test_hydro_rolling import hydro_case
from test_storage_reserve import pumped_case


@pytest.mark.parametrize('family,key,bad', [
    ('thermal','pmax',0.), ('thermal','pmin',700.), ('thermal','ramp',-1.),
    ('thermal','ut',-.1), ('thermal','dt',float('nan')), ('thermal','noload',-1.),
    ('thermal','bus',31.2), ('thermal','bus',True),
    ('renewable','cap',float('inf')), ('tie','pmax',-1.),
    ('storage','ecap',-1.), ('storage','soc_end_bid',.99),
    ('pumped','n_units',1.5), ('pumped','n_units',True), ('pumped','n_units',0),
    ('pumped','unit_gen_min',301.), ('pumped','vup0',500.),
    ('pumped','vup_min',451.), ('pumped','vdn_max',59.),
    ('pumped','v_total',100.), ('pumped','vup_end_bid',500.),
    ('pumped','switch_gap',1.5), ('pumped','max_gen_starts',-1),
    ('pumped','max_pump_starts',2.3), ('pumped','start_cost_pump',-1.),
    ('pumped','init_mode','pupm'), ('pumped','variable_speed','false'),
    ('hydro','n_units',1.5), ('hydro','n_units',0), ('hydro','unit_pmax',-1),
    ('hydro','init_units_on',4.5), ('hydro','init_units_on',5),
    ('hydro','init_p_unit',70.), ('hydro','ramp',True),
    ('hydro','zones',[[85,127],[45,60]]), ('hydro','zones',[[45,128]]),
    ('hydro','regulation','resrvoir'), ('hydro','qout_max',50.),
])
def test_invalid_technical_config_fails_without_modification(family,key,bad):
    cfg=load_config(); cfg.units[family][0][key]=bad
    with pytest.raises(ValueError):
        validate_physical_inputs(cfg)
    assert cfg.units[family][0][key] is bad or cfg.units[family][0][key] == bad


@pytest.mark.parametrize('key,bad',[('periods',np.inf),('periods',np.nan),('periods',True),('dt_h',0)])
def test_time_grid_rejects_nonfinite_nonintegral_and_zero(key,bad):
    cfg=load_config();cfg.base[key]=bad
    with pytest.raises(ValueError):validate_physical_inputs(cfg)


@pytest.mark.parametrize('state',[
    dict(online=False,p0=600.,hours=48), dict(online=True,p0=100.,hours=48),
    dict(online='false',p0=450.,hours=48),dict(online=True,p0=450.,hours=-1.),
])
def test_declared_thermal_state_consistency(state):
    cfg=load_config();cfg.units['thermal'][0]['init']=state
    with pytest.raises(ValueError,match='C1'):validate_physical_inputs(cfg)


@pytest.mark.parametrize('key,bad', [('v0',1e8),('vmin',19000.),('v_end',25000.),('qin',-1.),('e_day',-1.)])
def test_hydro_initial_and_seasonal_boundaries(key,bad):
    cfg=load_config();cfg.units['hydro'][0]['seasons']['wet'][key]=bad
    with pytest.raises(ValueError,match='H1'):validate_physical_inputs(cfg)


def test_variable_speed_pump_minimum_checked_only_when_active():
    cfg=load_config();p=cfg.units['pumped'][0];p['unit_pump_min']=400
    validate_physical_inputs(cfg)  # unused with a fixed-speed pump
    p['variable_speed']=True
    with pytest.raises(ValueError,match='unit_pump_min'):validate_physical_inputs(cfg)


def test_actual_hydro_water_outside_bounds_cannot_be_consumed_back_to_bounds():
    c=hydro_case(T=1);s=rolling.initial_state(c.cfg)
    c.init=dict(thermal=s.thermal,hydro={'H1':201.},hydro_p=s.hydro_p,hydro_online=s.hydro_online)
    with pytest.raises(ValueError,match='H1.*实际水库库容'):scuc.build_model(c)


def test_actual_pumped_water_replaces_unused_declared_initial_volume():
    c=pumped_case(initial_volume=130.,vdn_max=180.)
    assert c.cfg.units['pumped'][0]['vup0']==110.  # now below effective minimum120
    assert scuc.build_model(c)._md.pumped['PS1']['init']['V']==130.
    c.init=None
    with pytest.raises(ValueError,match='vup0'):scuc.build_model(c)


@pytest.mark.parametrize('family', ['thermal','renewable','storage','pumped','hydro','tie'])
def test_unknown_resource_bus_rejected_at_public_case_boundary(family):
    cfg=load_config();net=build_network(cfg);profiles=generate_profiles(cfg)
    cfg.units[family][0]['bus']=999
    with pytest.raises(ValueError,match='bus=999.*禁止静默'):
        build_da_case(cfg,net,profiles,default_bids(cfg))


def test_model_rechecks_mutated_resource_and_load_bus():
    c=tiny_case(reserve=0);c.cfg.units['thermal'][0]['bus']=999
    with pytest.raises(ValueError,match='bus=999'):scuc.build_model(c)
    c=tiny_case(reserve=0);c.load_bus[999]=np.array([10000.])
    with pytest.raises(ValueError,match=r'load_bus\[999\]'):scuc.build_model(c)


@pytest.mark.parametrize('field',['load_bus','R_up','R_dn'])
@pytest.mark.parametrize('values',[[np.nan],[-1.],[1.,2.],[],[True],['100'],[[100.]]])
def test_direct_case_arrays_must_have_exact_finite_nonnegative_numeric_grid(field,values):
    c=tiny_case(reserve=0)
    if field=='load_bus':c.load_bus={2:np.asarray(values)}
    else:setattr(c,field,np.asarray(values))
    with pytest.raises(ValueError,match='须为1段有限非负'):scuc.build_model(c)


@pytest.mark.parametrize('bad',[-1.,np.nan,np.inf,2000.,True,'100'])
def test_supply_schedule_validated_at_profile_boundary(bad):
    cfg=load_config();cfg.base['tie_line_mode']='supply';p=generate_profiles(cfg)
    p['TIE_sched']=bad
    with pytest.raises(ValueError,match='TIE_sched'):validate_profiles(cfg,p)


def test_supply_schedule_is_required_and_no_silent_missing_injection():
    cfg=load_config();cfg.base['tie_line_mode']='supply';net=build_network(cfg);p=generate_profiles(cfg)
    with pytest.raises(ValueError,match='TIE_sched'):
        build_da_case(cfg,net,p.drop(columns='TIE_sched'),default_bids(cfg))
    c=build_da_case(cfg,net,p,default_bids(cfg));c.tie={}
    with pytest.raises(ValueError,match='tie须完整'):scuc.build_model(c)


@pytest.mark.parametrize('problem',['duplicate_column','reverse_period','negative_load','nonfinite_renewable'])
def test_direct_profiles_do_not_bypass_file_validation(problem):
    cfg=load_config();net=build_network(cfg);p=generate_profiles(cfg)
    if problem=='duplicate_column':p=pd.concat([p,p[['load_da']]],axis=1)
    elif problem=='reverse_period':p=p.iloc[::-1]
    elif problem=='negative_load':p.loc[0,'load_da']=-1.
    else:p.loc[0,'W1_da']=np.nan
    with pytest.raises(ValueError):build_da_case(cfg,net,p,default_bids(cfg))


@pytest.mark.parametrize('problem',['nan_ptdf','wrong_shape','wrong_reference','duplicate_line','negative_rating','nonconservative_share'])
def test_mutable_network_cannot_omit_or_invent_flows(problem):
    c=tiny_case(reserve=0)
    if problem=='nan_ptdf':c.net.ptdf[0,1]=np.nan
    elif problem=='wrong_shape':c.net.ptdf=np.zeros((1,3))
    elif problem=='wrong_reference':c.net.ptdf[0,0]=1.
    elif problem=='duplicate_line':c.net.branches=pd.concat([c.net.branches]*2);c.net.ptdf=np.zeros((2,2))
    elif problem=='negative_rating':c.net.branches.loc[0,'rate_mw']=-1.
    else:c.net.load_share[2]=.5
    with pytest.raises(ValueError):scuc.build_model(c)


@pytest.mark.parametrize('value', [.4,2.,-1.,np.nan,'1'])
def test_fixed_commitments_cannot_be_rounded_or_exceed_domain(value):
    c=tiny_case(reserve=0);c.fixed={'u':{('CHEAP',0):value}}
    with pytest.raises(ValueError,match='fixed.u'):scuc.build_model(c)


@pytest.mark.parametrize('value',[-.1,np.nan,np.inf,'1'])
def test_fixed_power_cannot_be_silently_clipped(value):
    c=tiny_case(reserve=0);c.terminal={'fix_power':{'P':{('CHEAP',0):value}}}
    with pytest.raises(ValueError,match='fix_power.P'):scuc.build_model(c)


def test_valid_inputs_are_not_changed_and_real_model_still_solves():
    c=tiny_case(reserve=0);before=deepcopy((c.cfg.units,c.load_bus,c.net.ptdf,c.bids))
    m=scuc.build_model(c);scuc.solve_scuc(m);scuc.fix_integers(m);scuc.solve_sced(m)
    assert pyo.value(m.shed[2,0])==0
    assert pyo.value(m.P['CHEAP',0])==pytest.approx(50.)
    assert pyo.value(m.P['LOCAL',0])==pytest.approx(50.)
    assert c.cfg.units==before[0] and c.bids==before[3]
    np.testing.assert_array_equal(c.load_bus[2],before[1][2]);np.testing.assert_array_equal(c.net.ptdf,before[2])


@pytest.mark.parametrize('problem', ['fractional_bus','duplicate_bus','negative_load','zero_total','duplicate_branch','missing_endpoint','zero_reactance','nan_rating','boolean_multiplier'])
def test_network_source_csv_rejects_corruption(tmp_path,problem):
    import shutil
    cfg=load_config();shutil.copytree(cfg.root/'data',tmp_path/'data');cfg.root=tmp_path
    bp=tmp_path/'data/case39/buses.csv';lp=tmp_path/'data/case39/branches.csv'
    b=pd.read_csv(bp);l=pd.read_csv(lp)
    if problem=='fractional_bus':b['bus']=b.bus.astype(float);b.loc[0,'bus']=1.5
    elif problem=='duplicate_bus':b.loc[0,'bus']=2
    elif problem=='negative_load':b.loc[0,'load_mw']=-1.
    elif problem=='zero_total':b['load_mw']=0.
    elif problem=='duplicate_branch':l.loc[0,'branch_id']=l.loc[1,'branch_id']
    elif problem=='missing_endpoint':l.loc[0,'from_bus']=999
    elif problem=='zero_reactance':l.loc[0,'x_pu']=0.
    elif problem=='nan_rating':l.loc[0,'rate_mw']=np.nan
    else:cfg.base['line_capacity_multiplier']=True
    b.to_csv(bp,index=False);l.to_csv(lp,index=False)
    with pytest.raises(ValueError):build_network(cfg)


def test_direct_case_cannot_dispatch_unfiltered_disabled_resource():
    c=tiny_case(reserve=0);c.cfg.units['thermal'][0]['enabled']=False
    with pytest.raises(ValueError,match='停用资源'):scuc.build_model(c)


@pytest.mark.parametrize('family',['thermal','renewable','hydro','storage','pumped'])
def test_disabled_duplicate_does_not_replace_active_submitted_bid(family):
    from smm.config import filter_enabled
    from smm.bids import fill_missing_bids
    cfg=load_config();duplicate=deepcopy(cfg.units[family][0]);duplicate['enabled']=False
    cfg.units[family].append(duplicate);cfg.units=filter_enabled(cfg.units)
    uid=duplicate['id'];assert uid in cfg.units['_disabled']
    bids=default_bids(cfg);bids['units'][uid]['operator_note']='Explicit submitted bid, never replace'
    original=deepcopy(bids['units'][uid]);out=fill_missing_bids(cfg,deepcopy(bids))
    assert out['units'][uid]==original
    assert uid not in out.get('_filled_default',[])


def test_disabled_only_bid_is_still_removed():
    from smm.bids import fill_missing_bids
    cfg=load_config();bids=default_bids(cfg);bids['units']['H2']={'operator_note':'disabled'}
    cfg.units['_disabled']=['H2']
    assert 'H2' not in fill_missing_bids(cfg,bids)['units']


@pytest.mark.parametrize('content',[
    'bus,load_mw,load_mw\n1,100,999\n',
    'bus,load_mw\n1,100,999\n',
    'bus,load_mw\n1\n',
    'bus,\n1,100\n',
])
def test_csv_original_headers_and_widths_checked_before_pandas(tmp_path,content):
    from smm.input_validation import read_input_csv
    path=tmp_path/'bad.csv';path.write_text(content)
    with pytest.raises(ValueError,match='CSV'):read_input_csv(path)


def test_csv_utf8_bom_quoted_fields_and_blank_lines_supported(tmp_path):
    from smm.input_validation import read_input_csv
    path=tmp_path/'valid.csv';path.write_text('\ufeffbus,note,load_mw\n1,"研究,假设",100\n\n2,"两行\n说明",200\n')
    frame=read_input_csv(path)
    assert list(frame.bus)==[1,2] and frame.note.iloc[0]=='研究,假设'
    assert frame.note.iloc[1]=='两行\n说明'


@pytest.mark.parametrize('filename,duplicate', [('buses.csv','load_mw'),('branches.csv','x_pu')])
def test_network_rejects_original_duplicate_headers(tmp_path,filename,duplicate):
    import shutil
    cfg=load_config();shutil.copytree(cfg.root/'data',tmp_path/'data');cfg.root=tmp_path
    path=tmp_path/'data/case39'/filename
    lines=path.read_text().splitlines();lines[0]+=','+duplicate
    lines[1:]=[line+',123' for line in lines[1:]];path.write_text('\n'.join(lines)+'\n')
    with pytest.raises(ValueError,match='原始表头列名重复'):build_network(cfg)


def test_profile_override_file_rejects_original_duplicate_header(tmp_path):
    from smm.forecast import apply_profile_overrides
    cfg=load_config();base=generate_profiles(cfg);cfg.root=tmp_path;(tmp_path/'data').mkdir()
    (tmp_path/'data/profile_overrides.csv').write_text('load_da,load_da\n'+'100,999\n'*cfg.T)
    with pytest.raises(ValueError,match='原始表头列名重复'):apply_profile_overrides(cfg,base)
