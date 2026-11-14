from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
import pytest
from r36_energy import *
L=legacy
T=datetime(2026,11,12,tzinfo=timezone.utc)
E=L.Evidence('simulation','synthetic_fixture','0'*64,'1',T-timedelta(days=1),T-timedelta(days=1))
def v(x,unit='MWh'):return L.Value(D(str(x)),unit,E)
def c(id,q,p=300,ref=250,hq=None):return L.Contract(id,'Nref',v(q),v(p,'CNY/MWh'),v(ref,'CNY/MWh'),None if hq is None else v(hq))
def fixture(storage=False,charge=False,quarter=False):
    sign=-1 if charge else 1
    key=L.Key('ES1' if storage else 'C1','charge' if charge else 'discharge','N1','charge' if charge else 'discharge',T,T+timedelta(minutes=15 if quarter else 60))
    kw=dict(technology='independent_storage' if storage else 'coal',participation='participates',participation_source='SYNTHETIC declaration',agency_mode='not_applicable' if storage else 'non_decoupled_provincial',agency_contracts=None if storage else (c('GD_LOCAL:agency',20,280,260),),key=key,settlement_class='independent_storage' if storage else 'generator',classification=L.Attestation(True,E),applicability=L.Attestation(True,E),quarter_eligibility=L.Attestation(True,E) if quarter else None,channel='simulation',asof=T+timedelta(days=2),meter=v(sign*110),contracts=(c('LT',sign*50,hq=sign*200 if quarter else None),),da=L.Award('DA','da','dap','1','pubda',key,v(400,'CNY/MWh'),v(sign*100),'final'),rt=L.Award('RT','rt','rtp','1','pubrt',key,v(500,'CNY/MWh'),None,'final'))
    if not storage:kw.update(base=v(0),agency_export=v(20))
    return kw

def test_thermal_all_energy_legs():
    r=settle_energy(**fixture());assert r['components']==dict(LT_principal=D(15000),LT_congestion=D(7500),agency_principal=D(5600),agency_congestion=D(2800),DA_deviation=D(12000),RT_deviation=D(5000),nuclear_base=D(0))
    assert r['energy_component_subtotal']==D(47900)
    assert r['legacy_result']['energy_subtotal_CNY']==D(39500) # old subtotal omitted agency income
    assert r['formal_bill'] is None

def test_storage_separate_signed_ledgers():
    d=settle_energy(**fixture(True));q=settle_energy(**fixture(True,True))
    assert d['energy_component_subtotal']==47500 and q['energy_component_subtotal']==-47500
    assert paired_storage(d,q)['net_energy_component_subtotal']==0

def test_qualified_quarter_and_contract_split():
    k=fixture(True,quarter=True);r=settle_energy(**k);assert r['components']['LT_principal']==15000
    q=split_hourly_contract(v(-200),eligible=L.Attestation(True,E),channel='simulation',asof=T)
    assert [x.value for x in q]==[D(-50)]*4
    k['contracts']=(c('LT',51,hq=200),)
    with pytest.raises(ValueError):settle_energy(**k)

def test_hour_means_not_quantity_weighted():
    prices=tuple((T+timedelta(minutes=15*i),v(p,'CNY/MWh')) for i,p in enumerate((100,200,300,400)))
    q=tuple((T+timedelta(minutes=15*i),v(p)) for i,p in enumerate((1,2,3,4)))
    assert aggregate_hour(prices,kind='price',channel='simulation',asof=T)['value']==250
    assert aggregate_hour(q,kind='energy',channel='simulation',asof=T)['value']==10
@pytest.mark.parametrize('change',[{'technology':'nuclear'},{'participation':'does_not_participate'},{'contracts':None},{'meter':None},{'base':v(1)},{'agency_export':v(21)},{'agency_mode':'decoupled'},{'agency_contracts':None}])
def test_missing_unsupported(change):
    k=fixture();k.update(change)
    with pytest.raises(ValueError):settle_energy(**k)
def test_no_double_congestion_or_contract():
    k=fixture();k['agency_contracts']=(c('LT',20),)
    with pytest.raises(ValueError):settle_energy(**k)
def test_storage_no_quarter_eligibility():
    k=fixture(True,quarter=True);k['quarter_eligibility']=L.Attestation(False,E)
    with pytest.raises(ValueError):settle_energy(**k)
def test_missing_price_not_zero():
    k=fixture();a=k['rt'];k['rt']=L.Award(a.market,a.run_id,a.pricing_run_id,a.version,a.publication_id,a.key,L.unknown('CNY/MWh','missing'),None,'final')
    with pytest.raises(ValueError):settle_energy(**k)
def test_negative_price():
    k=fixture(True);a=k['rt'];k['rt']=L.Award(a.market,a.run_id,a.pricing_run_id,a.version,a.publication_id,a.key,v(-100,'CNY/MWh'),None,'final')
    assert settle_energy(**k)['components']['RT_deviation']==-1000
