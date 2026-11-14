from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
import pytest
from r54_reserve import Scope,Unit,calculate

def fixture():
    t=datetime(2026,11,12,tzinfo=timezone.utc)
    s=Scope(t,t+timedelta(minutes=15),'SYNTHETIC_ONLY','synthetic_fixture','not_started',True,True,True,D(80),'SYNTHETIC_REQUIREMENT_NOT_MODEL400')
    g=Unit('G','gas',D(100),D(100),D(60),'SYNTHETIC_DECLARATION_AND_DISPATCH',True,True,False,False)
    c=Unit('C','coal',D(200),D(180),D(80),'SYNTHETIC_DECLARATION_AND_DISPATCH',True,True,False,False)
    return s,g,c

def test_capped_gas_coal():
    s,g,c=fixture();r=calculate(s,(g,c))
    assert [x['service_MWh'] for x in r['rows']]==[D(10),D(20)]
    assert [x['amount_CNY'] for x in r['rows']]==[D(200),D(400)]
    assert r['total_CNY']==600 and r['formal_bill'] is None and not r['payable']
def test_uncapped():
    s,g,c=fixture();assert calculate(replace(s,lower_requirement_mw=D(160)),(g,c))['total_CNY']==900
def test_daily_failure_and_unknown():
    s,g,c=fixture();assert calculate(s,(replace(g,own_cause_daily_failure=True),))['total_CNY']==0
    r=calculate(s,(replace(g,own_cause_daily_failure=None),));assert r['status']=='missing' and r['rows'][0]['service_MWh'] is None
def test_nonself_derating():
    s,g,c=fixture();g=replace(g,rated_mw=D(200),declared_upper_mw=D(160),actual_mw=D(120),nonself_upper_reduction=True)
    assert calculate(s,(g,))['total_service_MWh']==20
@pytest.mark.parametrize('field,value',[('pool_complete',False),('lower_requirement_mw',None),('market_state',None),('current_parameter_verified',None)])
def test_missing(field,value):
    s,g,c=fixture();r=calculate(replace(s,**{field:value}),(g,c));assert r['status']=='missing' and r['total_CNY'] is None

def test_market_not_spot_exclusion():
    s,g,c=fixture();assert calculate(replace(s,market_state='trading'),(g,c))['status'].startswith('excluded')
def test_strict_and_category():
    s,g,c=fixture()
    with pytest.raises(ValueError):calculate(s,(replace(g,qualified='false'),))
    with pytest.raises(ValueError):calculate(s,(g,g))
    with pytest.raises(ValueError):calculate(replace(s,province='regional'),(g,))
    with pytest.raises(ValueError):calculate(s,(replace(g,actual_mw=D('NaN')),))
    assert calculate(s,(replace(g,technology='battery'),))['status']=='missing'
def test_nuclear_zero_and_missing():
    s,g,c=fixture();n=replace(g,technology='nuclear')
    assert calculate(s,(n,))['status']=='missing'
    assert calculate(s,(replace(n,nuclear_normal_lower_mw=D(100)),))['total_CNY']==0
