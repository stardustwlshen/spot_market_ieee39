"""Analytic cash accounting; no market solve and no bid-price cost inference."""
from types import SimpleNamespace as NS
from copy import deepcopy
import yaml
import pandas as pd
import pytest
from smm.profit import analyze as _analyze,load_fixed_cost_profile,validate_profile

def analyze(res):
    return _analyze(res,policy="legacy_cash_operating_v1")


def sample(tmp_path,write=True):
    profile=dict(source='synthetic fixed assumptions',version='test',units={
        'G':dict(type='gas_gt',scalar_marginal_cost_yuan_per_mwh=20.,fixed_online_cost_yuan_per_h=40.,fixed_startup_cost_yuan_per_start=100.),
        'B':dict(type='storage',discharge_degradation_yuan_per_mwh=5.),
        'H':dict(type='hydro',scalar_marginal_cost_yuan_per_mwh=6.),
        'PS':dict(type='pumped',generation_vom_yuan_per_mwh=10.,gen_start_yuan_per_unit_start=30.,pump_start_yuan_per_unit_start=50.),
        'W':dict(type='wind',scalar_marginal_cost_yuan_per_mwh=2.)})
    (tmp_path/'config').mkdir(exist_ok=True)
    if write:(tmp_path/'config/fixed_costs.yaml').write_text(yaml.safe_dump(profile))
    cfg=NS(root=tmp_path,dt=.25,units=dict(thermal=[dict(id='G',type='gas_gt')],renewable=[dict(id='W',type='wind')],hydro=[dict(id='H')],storage=[dict(id='B')],pumped=[dict(id='PS')]))
    states=[];rt=[];cash=[]
    for t in range(2):
        states.append(NS(thermal={'G':{'online':bool(t)}},storage={'B':50.-5*t},hydro={'H':100.-t},pumped={'PS':{'V':100.-t,'ng':int(t>0),'np':0}}))
        rt.append(dict(t=t,snap=dict(P={'G':[100.]},u={'G':[1]},Pw={'W':[10.]},Ph={'H':[20.]},Vh={'H':[99.-t]},Pd={'B':[20.]},E={'B':[45.-5*t]},Pg={'PS':[40.]},Vu={'PS':[99.-t]},ngen={'PS':[1]},npump={'PS':[0]})))
        for entity,total in [('G',1000.),('W',30.),('H',100.),('B_放电',100.),('B_充电',-60.),('PS_发电',200.),('PS_抽水',-50.)]:
            cash.append(dict(entity=entity,period=t,da_amount=total*.8,rt_amount=total*.2,total=total))
    res=dict(da=NS(case=NS(cfg=cfg),prices={'lmp':99999}),rr=NS(rt=rt,states=states),settle={'detail':pd.DataFrame(cash)})
    return res,profile


def test_fixed_thermal_cost_startup_once_noload_and_profitable(tmp_path):
    res,_=sample(tmp_path);result=analyze(res);g=result['by_unit'].set_index('unit').loc['G']
    assert g.variable_cost_yuan==20*100*.25*2
    assert g.fixed_online_cost_yuan==40*.25*2
    assert g.startup_cost_yuan==100. # one actual start, not multiplied by dt
    assert g.simulated_operating_profit_yuan==880.
    res['da'].prices={'lmp':-99999} # quotes/prices are not a cost driver
    assert analyze(res)['by_unit'].set_index('unit').loc['G'].cash_operating_cost_yuan==1120.


def test_battery_charge_once_hydro_no_water_opportunity_and_ps_starts(tmp_path):
    res,_=sample(tmp_path);u=analyze(res)['by_unit'].set_index('unit')
    assert u.loc['B','cash_revenue_yuan']==80.
    assert u.loc['B','charging_settlement_yuan']==-120.
    assert u.loc['B','cash_operating_cost_yuan']==50.
    assert u.loc['B','simulated_operating_profit_yuan']==30.
    assert u.loc['B','inventory_change']==-10.
    assert u.loc['H','cash_operating_cost_yuan']==60.
    assert u.loc['PS','cash_operating_cost_yuan']==230.
    assert u.loc['PS','simulated_operating_profit_yuan']==70.


def test_loss_and_missing_profile_are_not_zero_or_bid_fallback(tmp_path):
    res,profile=sample(tmp_path);res['settle']['detail'].loc[lambda d:d.entity=='G',['total','da_amount','rt_amount']]=0.
    assert analyze(res)['by_unit'].set_index('unit').loc['G'].simulated_operating_profit_yuan==-1120.
    (tmp_path/'config/fixed_costs.yaml').unlink()
    result=analyze(res)
    assert not result['by_unit'].cost_available.any()
    assert result['by_unit'].cash_operating_cost_yuan.isna().all()
    assert result['summary']['simulated_operating_profit_yuan'] is None
    assert result['by_unit'].set_index('unit').loc['B'].cash_revenue_yuan==80.


def test_missing_one_unit_keeps_other_costs_and_unavailable_type_totals(tmp_path):
    res,profile=sample(tmp_path);del profile['units']['W']
    (tmp_path/'config/fixed_costs.yaml').write_text(yaml.safe_dump(profile))
    result=analyze(res)
    assert not result['by_type'].set_index('type').loc['wind'].cost_available
    assert result['by_unit'].set_index('unit').loc['G'].cost_available
    assert result['summary']['cash_operating_cost_yuan'] is None


def test_profile_hash_and_parameters_independent_of_bid_worksheet(tmp_path):
    res,profile=sample(tmp_path);cfg=res['da'].case.cfg
    before=load_fixed_cost_profile(cfg)
    (tmp_path/'config/bid_cost_basis.yaml').write_text('thermal: {G: {fuel_price: 999999}}')
    assert load_fixed_cost_profile(cfg)['sha256']==before['sha256']
    assert analyze(res)['by_unit'].set_index('unit').loc['G'].cash_operating_cost_yuan==1120.
    profile['units']['G']['scalar_marginal_cost_yuan_per_mwh']=float('nan')
    with pytest.raises(ValueError):validate_profile(profile)


def test_each_unit_retains_its_own_cost_source_kind(tmp_path):
    res, profile = sample(tmp_path)
    profile['source_kind'] = 'mixed_explicit_and_synthetic_costs'
    profile['unit_sources'] = {'G': {'source_kind': 'observed'},
                               'W': {'source_kind': 'research_assumption_not_observed_plant_data'}}
    (tmp_path / 'config/fixed_costs.yaml').write_text(yaml.safe_dump(profile))
    result = analyze(res)
    assert set(result['by_period'].query("unit == 'G'").cost_source_kind) == {'observed'}
    assert result['by_unit'].set_index('unit').loc['W', 'cost_source_kind'] == 'research_assumption_not_observed_plant_data'
    assert result['by_type'].set_index('type').loc['gas_gt', 'cost_source_kind'] == 'observed'


def test_already_online_no_start_operator_penalties_and_water_opportunity_excluded(tmp_path):
    res,_=sample(tmp_path);res['rr'].states[0].thermal['G']['online']=True
    res['da'].costs={'line_penalty':1e12,'anchor_penalty':1e12,'hydro_water_opportunity':1e12}
    result=analyze(res);u=result['by_unit'].set_index('unit')
    assert u.loc['G','startup_cost_yuan']==0.
    assert u.loc['G','cash_operating_cost_yuan']==1020.
    assert u.loc['H','cash_operating_cost_yuan']==60.
    assert u.loc['G','cost_profile_sha256']==result['profile']['sha256']


def test_pumped_actual_pump_count_increase_fee_without_double_energy_purchase(tmp_path):
    res,_=sample(tmp_path)
    for t,r in enumerate(res['rr'].rt):
        r['snap']['Pg']['PS']=[0.];r['snap']['ngen']['PS']=[0];r['snap']['npump']['PS']=[1]
        res['rr'].states[t].pumped['PS'].update(ng=0,np=int(t>0))
    ps=analyze(res)['by_unit'].set_index('unit').loc['PS']
    assert ps.pump_startup_count==1.
    assert ps.cash_operating_cost_yuan==50.
    assert ps.cash_revenue_yuan==300.
    assert ps.simulated_operating_profit_yuan==250.


@pytest.mark.parametrize('corruption',['missing_charge','duplicate','nan','infinite','outside_period','unknown_entity','bad_total'])
def test_invalid_cash_evidence_never_summed_as_zero(tmp_path,corruption):
    res,_=sample(tmp_path);d=res['settle']['detail']
    if corruption=='missing_charge':d=d.drop(d.index[d.entity=='B_充电'][0])
    elif corruption=='duplicate':d=pd.concat([d,d.iloc[[0]]],ignore_index=True)
    elif corruption=='nan':d.loc[0,'total']=float('nan')
    elif corruption=='infinite':d.loc[0,'da_amount']=float('inf')
    elif corruption=='outside_period':d.loc[0,'period']=99
    elif corruption=='unknown_entity':d=pd.concat([d,pd.DataFrame([dict(entity='unknown',period=0,da_amount=0.,rt_amount=0.,total=0.)])])
    else:d.loc[0,'total']+=1.
    res['settle']['detail']=d
    with pytest.raises(ValueError):analyze(res)


@pytest.mark.parametrize('corruption',['power','on','initial_online','battery_power','pumped_power','pumped_count','initial_pumped_count','inventory'])
def test_nonfinite_formal_quantities_cannot_become_zero_cost(tmp_path,corruption):
    res,_=sample(tmp_path);sn=res['rr'].rt[0]['snap'];state=res['rr'].states[0]
    if corruption=='power':sn['P']['G'][0]=float('nan')
    elif corruption=='on':sn['u']['G'][0]=float('nan')
    elif corruption=='initial_online':state.thermal['G']['online']=float('nan')
    elif corruption=='battery_power':sn['Pd']['B'][0]=float('nan')
    elif corruption=='pumped_power':sn['Pg']['PS'][0]=float('nan')
    elif corruption=='pumped_count':sn['npump']['PS'][0]=float('nan')
    elif corruption=='initial_pumped_count':state.pumped['PS']['ng']=float('inf')
    else:sn['E']['B'][0]=float('nan')
    with pytest.raises(ValueError,match='non-finite actual'):
        analyze(res)


def test_profit_scope_does_not_mislabel_a_full_day_as_a_prefix(tmp_path):
    res,_=sample(tmp_path)
    scope=analyze(res)['summary']['scope']
    assert 'actual executed RT periods only' in scope
    assert 'see completed_scope for full-day status' in scope
    assert 'ending inventories separately disclosed' in scope
    assert 'not a full-day claim' not in scope
