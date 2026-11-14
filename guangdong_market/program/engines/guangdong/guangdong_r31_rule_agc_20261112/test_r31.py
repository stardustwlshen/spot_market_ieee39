import copy
import pytest
from r31_agc import *

def case():
    return [dict(resource_id=f'C{i}',plant_id=f'P{i}',region='Guangdong',kind='thermal',qualification_evidence='SYNTHETIC ONLY',qualified=True,available_at='2026-11-12T00:00:00',stable_online=True,k=1,k1=1,bid=3+i,declared_ratio=.05,capacity_excluding_outage_mw=1000) for i in range(6)]
def run(u=None,**kw):
    args=dict(demand_mw=100,local_floor_mw=70,full_control_area_kmax=2,as_of='2026-11-12T00:30:00',hour_start='2026-11-12T01:00:00');args.update(kw)
    return clear_hour(case() if u is None else u,**args)

def test_whole_units_price():
    r=run();assert list(r['awards_mw'].values())==[20]*5
    assert r['reserved_non_price_setters']==['C0','C1','C2','C3']
    assert r['price']==14 and not r['formal_publication_allowed']
def test_sameplant_recompute():
    u=case();u[1]['plant_id']='P0';r=run(u)
    assert r['trace'][1]['awards_mw']=={'C0':10,'C1':10}
    assert len(r['awards_mw'])==6 and sum(r['awards_mw'].values())==100

def test_tie_performance():
    u=case();u[0].update(bid=3,k=1);u[1].update(bid=6,k=2)
    assert run(u)['ranked_ids'].index('C1')<run(u)['ranked_ids'].index('C0')

def test_tie_k1():
    u=case();u[1].update(bid=3,k1=2)
    assert run(u)['ranked_ids'][0]=='C1'

def test_missing_history():
    r=run(local_floor_mw=100);assert r['price'] is None and r['price_source']=='unresolved'
def test_previous_raw_cap():
    r=run(local_floor_mw=100,previous_raw_marginal=22);assert r['raw_marginal']==22 and r['price']==15

def test_shortage():
    r=run(case()[:3]);assert r['unmet_mw']==40 and r['price'] is None

def test_exclusion():
    r=run(safety_excluded=['C0']);assert 'C0' not in r['awards_mw'] and r['unmet_mw']==0

@pytest.mark.parametrize('kw',[{'full_control_area_kmax':None},{'mode':'formal'},{'as_of':'2026-11-12T00:31:00'},{'floor_crossing_policy':'guess'}])
def test_invalid_params(kw):
    with pytest.raises((RuleInputError,TypeError)):run(**kw)
@pytest.mark.parametrize('change',[{'qualified':False},{'kind':'BESS'},{'region':'Guangxi'},{'available_at':'2026-11-12T02:00:00'},{'stable_online':False},{'declared_ratio':.1},{'k':3}])
def test_invalid_units(change):
    u=case();u[0].update(change)
    with pytest.raises(RuleInputError):run(u)

def test_sced_preserves_reserve():
    r=run(); b={k:dict(pmin=100,pmax=500,power=300) for k in r['awards_mw']}; saved=copy.deepcopy(b)
    out=sced_bounds(r,b);assert b==saved and out['bounds']['C0']['pmin']==120
    assert out['positive_reserve_requirement']==out['accident_reserve_requirement']==400
    with pytest.raises(RuleInputError):sced_bounds(r,b,accident_reserve_requirement=0)
    with pytest.raises(RuleInputError):sced_bounds(r,out['bounds'])
def test_gross():
    assert gross_mileage(10,14,1.2)['gross_compensation']==168
    assert gross_mileage(10,14,1.2,designated_test=True)['gross_compensation']==0

def test_horizon():
    rs=[run(hour_start=f'2026-11-12T0{i}:00:00') for i in (1,2,3)]
    assert len(execution_view(rs)['lookahead_only'])==2
    with pytest.raises(RuleInputError):execution_view(rs[:2])

@pytest.mark.parametrize('field,value', [('qualified','false'),('qualified',1),('stable_online','false'),('stable_online',1)])
def test_strict_true_flags(field,value):
    u=case();u[0][field]=value
    with pytest.raises(RuleInputError):run(u)

@pytest.mark.parametrize('value',['false',0,1,None])
def test_designated_test_boolean(value):
    with pytest.raises(RuleInputError):gross_mileage(10,14,1.2,designated_test=value)

@pytest.mark.parametrize('args',[(10,15.1,1.2),(1e308,15,1e308),(10,14,-1)])
def test_gross_limits(args):
    with pytest.raises(RuleInputError):gross_mileage(*args)

@pytest.mark.parametrize('first',[{'unmet_mw':1,'price':14},{'unmet_mw':0,'price':None}])
def test_unresolved_first_hour(first):
    rs=[run(hour_start=f'2026-11-12T0{i}:00:00') for i in (1,2,3)]
    rs[0].update(first)
    with pytest.raises(RuleInputError):execution_view(rs)
