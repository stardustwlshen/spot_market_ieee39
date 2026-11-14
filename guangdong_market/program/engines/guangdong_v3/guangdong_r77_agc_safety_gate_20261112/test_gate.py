import copy
import pytest
from gate import run,InputError,CHECKS
E=dict(id='synthetic',source='synthetic fixture',source_grade='research',as_of='2026-11-12T00:00:00+08:00',hour_start='2026-11-12T01:00:00+08:00')
def fixture():
 def u(i,bid,p,k):
  c={name:dict(status='pass',reason='synthetic explicitly checked',evidence=dict(E))for name in CHECKS}
  for name in ('hydro_vibration','storage_soc'):c[name]['status']='not_applicable';c[name]['reason']='thermal resource'
  return dict(resource_id=i,kind='thermal',awarded_mw=20,sorting_price=bid,P=p,k1=k,award_evidence=dict(E),checks=c)
 return dict(schema='gd.agc.safety.r77.v1',mode='research',hour_start='2026-11-12T01:00:00+08:00',gate_as_of='2026-11-12T00:30:00+08:00',procurement_target_mw=60,awarded_units=[u('a',5,.9,2),u('b',5,.8,2),u('c',5,.8,1)])
def fail(u,cause='sectionX',check='network_sections'):
 u['checks'][check].update(status='fail',reason='synthetic section failure',attribution='unit_specific',cause_id=cause,cause_evidence=dict(E))
def test_same_cause_order_and_shortage():
 d=fixture()
 for u in d['awarded_units']:fail(u)
 r=run(d);assert r['removal_groups'][0]['removal_order']==['c','b','a'];assert r['unmet_requirement_mw']==60 and r['requires_reclearing']
def test_price_desc_and_exact_research_tie():
 d=fixture();d['awarded_units'][0].update(sorting_price=9)
 for u in d['awarded_units']:fail(u)
 assert run(d)['removal_groups'][0]['removal_order'][0]=='a'
 for u in d['awarded_units']:u.update(sorting_price=5,P=.8,k1=1)
 assert run(d)['removal_groups'][0]['removal_order']==['a','b','c']
def test_unknown_not_pass():
 d=fixture();d['awarded_units'][0]['checks']['agc_normal']['status']='unknown';r=run(d)
 assert r['requires_reclearing']and r['requires_operator_resolution']and not r['removed']
def test_aggregate_not_unit_attribution():
 d=fixture();fail(d['awarded_units'][0]);d['awarded_units'][0]['checks']['network_sections']['attribution']='aggregate'
 with pytest.raises(InputError):run(d)
 d=fixture();d['aggregate_no_increase']=dict(status='research_no_increase_not_certified',reason='aggregate R76 difference does not identify units',evidence=E)
 assert run(d)['removed']==[]
def test_na_relevance():
 d=fixture();d['awarded_units'][0]['kind']='storage'
 with pytest.raises(InputError):run(d)
 d=fixture();d['awarded_units'][0]['checks']['capacity_balance']['status']='not_applicable'
 with pytest.raises(InputError):run(d)
def test_missing_evidence_and_check():
 d=fixture();del d['awarded_units'][0]['checks']['network_sections']
 with pytest.raises(InputError):run(d)
 d=fixture();d['awarded_units'][0]['checks']['agc_normal']['evidence']={}
 with pytest.raises(InputError):run(d)
def test_multiple_causes_no_duplicate_removed():
 d=fixture();fail(d['awarded_units'][0]);fail(d['awarded_units'][0],'socX','agc_normal');r=run(d)
 assert r['removed']==['a']and r['remaining_awarded_mw']==40 and len(r['removal_groups'])==2
def test_formal_and_nonfinite_rejected():
 d=fixture();d['mode']='formal'
 with pytest.raises(InputError):run(d)
 d=fixture();d['awarded_units'][0]['P']=float('nan')
 with pytest.raises(InputError):run(d)
def test_pass_not_formal_authorization():
 r=run(fixture());assert not r['removed']and not r['formal_safety_certified']and not r['dispatch_authorized']and not r['qualification_created']

def test_cross_hour_rejected():
 for kind in ('award','check','cause'):
  d=fixture();u=d['awarded_units'][0]
  if kind=='award':e=u['award_evidence']
  elif kind=='check':e=u['checks']['agc_normal']['evidence']
  else:fail(u);e=u['checks']['network_sections']['cause_evidence']
  e['hour_start']='2026-11-12T02:00:00+08:00'
  with pytest.raises(InputError):run(d)
def test_future_evidence_rejected():
 for kind in ('award','check','cause'):
  d=fixture();u=d['awarded_units'][0]
  if kind=='award':e=u['award_evidence']
  elif kind=='check':e=u['checks']['agc_normal']['evidence']
  else:fail(u);e=u['checks']['network_sections']['cause_evidence']
  e['as_of']='2026-11-12T00:31:00+08:00'
  with pytest.raises(InputError):run(d)
