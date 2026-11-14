import copy,json
from pathlib import Path
import pytest
from adapter import run,RuleInputError

def fixture():
    p=lambda v,u:dict(value=v,unit=u,version='synthetic_v1',source='explicit synthetic test, not current Guangdong value',source_grade='research_assumption',available_at='2026-11-11T00:00:00+08:00',effective_from='2026-11-12T00:00:00+08:00',effective_to='2026-11-13T00:00:00+08:00')
    return dict(schema='gd.agc.r73.v1',mode='research',scope='guangdong_local_research_projection',cross_zone_exchange_assumption=0,as_of='2026-11-12T00:30:00+08:00',hour_start='2026-11-12T01:00:00+08:00',parameters={n:p(v,u) for n,v,u in [('bid_floor',1,'yuan/MW_mileage'),('distribution_zone_demand_mw',200,'MW'),('provincial_procurement_target_mw',100,'MW'),('local_reserved_floor_mw',0,'MW'),('full_control_area_kmax',2,'dimensionless')]},units=[dict(resource_id=f'T{i}',plant_id=f'P{i}',participant_type='generator',submitted=True,region='Guangdong',kind='thermal',qualified=True,qualification_evidence='SYNTHETIC ONLY',available_at='2026-11-11T00:00:00+08:00',stable_online=True,k=1,k1=1,bid=1+i,declared_ratio=.05,capacity_excluding_outage_mw=1000) for i in range(6)])

def test_floor_and_separate_denominator():
    r=run(fixture());assert list(r['awards_mw'].values())==[40]*3;assert r['price']==6 and r['native_calls']==0

def test_floor_not_inferred():
    d=fixture();d['parameters']['bid_floor']['value']=3
    with pytest.raises(RuleInputError):run(d)
    del d['parameters']['bid_floor']
    with pytest.raises(RuleInputError):run(d)

def test_third_party_unsubmitted():
    d=fixture();d['units'].append(dict(resource_id='storage_unsubmitted',participant_type='third_party',submitted=False,kind='storage'))
    r=run(d);assert len(r['nonparticipants'])==1 and 'storage_unsubmitted' not in r['ranked_ids']

def test_submitted_unsupported():
    for kind in ('storage','hydro'):
        d=fixture();d['units'][0]['kind']=kind
        with pytest.raises(RuleInputError):run(d)

def test_no_silent_defaults():
    d=fixture();d['units'][0]['submitted']=False
    with pytest.raises(RuleInputError):run(d)
    d=fixture();del d['units'][0]['submitted']
    with pytest.raises(RuleInputError):run(d)

def test_parameter_evidence_and_time():
    for key,value in [('source',''),('version',''),('source_grade','official_authenticated'),('effective_to','2026-11-12T01:30:00+08:00'),('available_at','2026-11-12T01:00:00+08:00'),('value',float('nan')),('unit','yuan/MWh')]:
        d=fixture();d['parameters']['bid_floor'][key]=value
        with pytest.raises(RuleInputError):run(d)

def test_formal_rejected_and_shortage_explicit():
    d=fixture();d['mode']='formal'
    with pytest.raises(RuleInputError):run(d)
    d=fixture();d['units']=d['units'][:1];r=run(d)
    assert r['unmet_requirement_mw']==60 and r['price'] is None and not r['formal_supported']

def test_external_kmax_and_demand_not_local_inference():
    d=fixture();del d['parameters']['full_control_area_kmax']
    with pytest.raises(RuleInputError):run(d)
    d=fixture();d['parameters']['distribution_zone_demand_mw']['value']=100
    assert list(run(d)['awards_mw'].values())==[20]*5

def test_no_prior_price_and_missing_qualification():
    d=fixture();d['previous_raw_marginal']=5
    with pytest.raises(RuleInputError):run(d)
    d=fixture();del d['units'][0]['qualification_evidence']
    with pytest.raises(RuleInputError):run(d)

if __name__=='__main__':Path(__file__).with_name('example.synthetic.json').write_text(json.dumps(fixture(),indent=2)+'\n')
