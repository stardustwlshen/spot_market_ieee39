from copy import deepcopy
import numpy as np
import pytest
from smm.config import load_config
from smm.forecast import generate_profiles,apply_profile_overrides
from smm.network import build_network
from smm.input_validation import validate_physical_inputs

@pytest.mark.parametrize('key,value',[('phi',1.2),('phi',float('nan')),('sigma',-1)])
def test_bad_forecast_parameters(key,value):
    c=load_config();c.base['forecast']={key:{'wind':value if key=='phi' else {'da':value}}}
    with pytest.raises(ValueError):generate_profiles(c)

def test_generated_profiles_validated_without_override(tmp_path):
    c=load_config();p=generate_profiles(c);c.root=tmp_path;p.loc[0,'W1_da']=np.nan
    with pytest.raises(ValueError,match='有限非负'):apply_profile_overrides(c,p)

@pytest.mark.parametrize('problem',['reverse','duplicate','unknown'])
def test_override_schema_and_order(problem):
    c=load_config();p=generate_profiles(c);o=p[['period','W1_rt']].copy()
    if problem=='reverse':o=o.iloc[::-1]
    elif problem=='duplicate':o.loc[1,'period']=1
    else:o['W1_realtime']=o.pop('W1_rt')
    with pytest.raises(ValueError,match='period|未知列'):apply_profile_overrides(c,p,o)

@pytest.mark.parametrize('value',[-1,0,float('nan'),True])
def test_line_factor_invalid(value):
    c=load_config();c.base.setdefault('line_scale',{})['L1-2']=value
    with pytest.raises(ValueError,match='line_scale'):build_network(c)

@pytest.mark.parametrize('value',[2,0,-1,float('nan'),True])
def test_efficiency_invalid(value):
    c=load_config();c.units['storage'][0]['eta_d']=value
    with pytest.raises(ValueError):validate_physical_inputs(c)

def test_missing_rolling_states_rejected():
    c=load_config()
    # Isolate missing inventory families, not the independent hydro-power gate.
    state={'thermal':{g['id']:deepcopy(g['init']) for g in c.units['thermal']},
           'hydro_p':{h['id']:h['init_units_on']*h['init_p_unit'] for h in c.units['hydro']},
           'hydro_online':{h['id']:h['init_units_on'] for h in c.units['hydro']}}
    with pytest.raises(ValueError,match='storage'):
        validate_physical_inputs(c,state)
    state['storage']={s['id']:s['ecap']*s['soc0'] for s in c.units['storage']}
    with pytest.raises(ValueError,match='pumped'):validate_physical_inputs(c,state)


def test_optimization_boundary_rechecks_mutated_efficiency():
    from test_storage_reserve import battery_case
    from smm.scuc import build_model
    c=battery_case();c.cfg.units['storage'][0]['eta_d']=2
    with pytest.raises(ValueError,match='效率'):build_model(c)

@pytest.mark.parametrize('family',['storage','pumped'])
def test_optimization_boundary_rejects_missing_actual_state(family):
    from test_storage_reserve import battery_case,pumped_case
    from smm.scuc import build_model
    c=battery_case() if family=='storage' else pumped_case()
    c.stage='RT';c.init={'thermal':{g['id']:deepcopy(g['init']) for g in c.cfg.units['thermal']},'hydro':{}}
    with pytest.raises(ValueError,match=family):build_model(c)


def test_optimization_boundary_rejects_missing_thermal_actual():
    from test_da_stages import tiny_case
    from smm.scuc import build_model
    c=tiny_case(reserve=0);c.stage='RT';c.init=dict(thermal={},storage={},pumped={},hydro={})
    with pytest.raises(ValueError,match='thermal'):build_model(c)


def test_explicit_zero_outflow_limit_is_not_omitted():
    from test_hydro_rolling import hydro_case
    from smm.scuc import build_model
    import pyomo.environ as pyo
    c=hydro_case(T=1);c.cfg.units['hydro'][0]['qout_max']=0
    # A zero release limit requires a consistent zero ecological minimum.
    for season in c.cfg.units['hydro'][0]['seasons'].values():
        season['qeco']=0
    m=build_model(c)
    assert ('H1',0) in m.c_qout
    assert pyo.value(m.c_qout['H1',0].upper)==0
    c.cfg.units['hydro'][0]['qout_max']=None
    assert len(build_model(c).c_qout)==0

@pytest.mark.parametrize('bad',[-1,float('nan'),float('inf')])
def test_invalid_outflow_limit_rejected(bad):
    c=load_config();c.units['hydro'][0]['qout_max']=bad
    with pytest.raises(ValueError,match='qout_max'):validate_physical_inputs(c)

@pytest.mark.parametrize('bad',[-1,float('nan'),float('inf'),True])
def test_effective_dispatch_penalty_rejected_before_solve(bad):
    from test_da_stages import tiny_case
    from smm.scuc import build_model
    c=tiny_case(reserve=0)
    with pytest.raises(ValueError,match='penalties.line'):
        build_model(c,penalties=dict(balance=1e6,line=bad,reserve=1e5))
