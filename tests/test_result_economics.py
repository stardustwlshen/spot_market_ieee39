import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import pandas as pd
import pytest
from result_economics import build_unit_economics, summarize_unit_economics
from smm.spot_costs import empty_profile

def fixture():
    return {'summary':{'expected_periods':1,'dt_h':1},
            'da_unit_awards':pd.DataFrame([{'unit':'S','type':'storage','period':0}]),
            'settlement_detail':pd.DataFrame([
                dict(entity='S_放电',period=0,q_da=10.,q_rt=12.,lmp_da=100.,lmp_rt=50.,da_amount=1000.,rt_amount=100.,total=1100.),
                dict(entity='S_充电',period=0,q_da=-5.,q_rt=-4.,lmp_da=100.,lmp_rt=50.,da_amount=-500.,rt_amount=50.,total=-450.)])}

def test_storage_cash_no_double_count():
    profile=empty_profile()
    profile['units']['S']=dict(type='storage',source='test',source_kind='assumption',cost_scope='no_fuel_or_defined_start_stop_costs_excluding_om')
    df=build_unit_economics(fixture(),profile=profile).set_index('phase')
    assert df.loc['da','profit_yuan']==500.
    assert df.loc['rt','profit_yuan']==650.
    assert df.loc['rt','gross_generation_cash_yuan']==1100.
    assert df.loc['rt','purchase_cost_yuan']==450.
    assert df.loc['rt','generation_mwh']==12.
    assert df.loc['rt','net_cash_yuan']==df.loc['rt','profit_yuan']

def test_unknown_not_zero():
    df=build_unit_economics(fixture())
    assert df.profit_yuan.isna().all()
    assert summarize_unit_economics(df).profit_yuan.isna().all()

def test_missing_entity_fails():
    r=fixture(); r['settlement_detail']=r['settlement_detail'].iloc[:1]
    with pytest.raises(ValueError,match='Incomplete'):
        build_unit_economics(r)

def test_no_settlement_not_fabricated():
    assert build_unit_economics({}).empty

def test_da_only_hourly_plan():
    r={'summary':{'expected_periods':1,'dt_h':1},'da_unit_awards':pd.DataFrame([dict(unit='W',type='wind',period=0,P_mw=3,lmp=20)])}
    r['da_unit_awards']['bus']=1
    r['da_prices']=pd.DataFrame([dict(period=0,bus=1,lmp=999,lmp_clamped=20)])
    profile=empty_profile();profile['units']['W']=dict(type='wind',source='test',source_kind='assumption',cost_scope='no_fuel_or_defined_start_stop_costs_excluding_om')
    x=build_unit_economics(r,profile=profile)
    assert list(x.phase)==['da']
    assert x.iloc[0].generation_mwh==3
    assert x.iloc[0].profit_yuan==60

def test_thermal_initial_stop_and_actual_cost_once():
    r=fixture();r['settlement_detail']=r['settlement_detail'].iloc[:1].assign(entity='T')
    r['da_unit_awards']=pd.DataFrame([dict(unit='T',type='coal',period=0,on=1)])
    r['rt_units']=pd.DataFrame([dict(unit='T',type='coal',period=0,on=1)])
    profile=empty_profile();profile['units']['T']=dict(type='coal',source='test',source_kind='assumption',fuel_cost_yuan_per_mwh=5,online_cost_yuan_per_h=2,startup_cost_yuan_per_start=7,shutdown_cost_yuan_per_stop=3,online_cost_basis='no_load_fuel')
    x=build_unit_economics(r,profile=profile,initial_state={'da':{'T':{'on':0}},'rt':{'T':{'on':0}}}).set_index('phase')
    assert x.loc['da','operating_cost_yuan']==59
    assert x.loc['rt','operating_cost_yuan']==69
    assert x.loc['rt','profit_yuan']==1031


def test_actual_missing_settlement_not_plan():
    with pytest.raises(ValueError,match='formal settlement'):
        build_unit_economics({'full':True})

def test_missing_entire_unit_rejected():
    r=fixture();r['da_unit_awards']=pd.concat([r['da_unit_awards'],pd.DataFrame([dict(unit='W',type='wind',period=0)])])
    with pytest.raises(ValueError,match='Missing settlement unit'):
        build_unit_economics(r)

def test_declared_period_missing_rejected():
    r=fixture();r['summary']['executed_periods']=2
    with pytest.raises(ValueError,match='period scope'):
        build_unit_economics(r)

def test_da_price_unknown_not_award_fallback():
    r={'summary':{'expected_periods':1,'dt_h':.25},'da_unit_awards':pd.DataFrame([dict(unit='W',type='wind',period=0,P_mw=4,lmp=999)])}
    x=build_unit_economics(r)
    assert x.iloc[0].generation_mwh==1
    assert pd.isna(x.iloc[0].gross_generation_cash_yuan)
    assert pd.isna(x.iloc[0].da_cash_yuan)

def test_da_missing_component_unknown():
    r={'summary':{'expected_periods':1,'dt_h':.25},'da_unit_awards':pd.DataFrame([dict(unit='S',type='storage',period=0,P_mw=4,lmp=999)])}
    x=build_unit_economics(r)
    assert pd.isna(x.iloc[0].generation_mwh)
    assert pd.isna(x.iloc[0].profit_yuan)

def test_duplicate_awards_rejected():
    r={'da_unit_awards':pd.DataFrame([dict(unit='W',type='wind',period=0)]*2)}
    with pytest.raises(ValueError,match='Duplicate DA'):
        build_unit_economics(r)

def test_frozen_nonidle_and_no_actual_fallback(tmp_path):
    import yaml
    from result_economics import _frozen_evidence
    root=tmp_path/'scenario_snapshot';(root/'config').mkdir(parents=True)
    (root/'config/units.yaml').write_text(yaml.safe_dump({'thermal':[{'id':'T','type':'coal','init':{'online':True}}],'pumped':[{'id':'P','init_mode':'pump','n_units':3}]}),encoding='utf8')
    _,initial=_frozen_evidence({'dir':str(tmp_path)},None,None)
    assert initial['da']['P']=={'ng':0,'np':3}
    assert initial['rt']=={}

def test_continuous_missing_actual_evidence_rejected(tmp_path):
    import yaml
    from result_economics import _frozen_evidence
    root=tmp_path/'scenario_snapshot';(root/'config').mkdir(parents=True)
    (root/'config/units.yaml').write_text(yaml.safe_dump({'thermal':[]}),encoding='utf8')
    (tmp_path/'admission.json').write_text('{}',encoding='utf8')
    with pytest.raises(ValueError,match='actual initial'):
        _frozen_evidence({'dir':str(tmp_path)},None,None)

def test_cost_metadata_detached():
    p=empty_profile()
    x=build_unit_economics(fixture(),profile=p)
    x.attrs['cost_profile']['source']='changed'
    assert p['source']!='changed'
    assert x.attrs['cost_source']['profit_basis']==p['profit_basis']
