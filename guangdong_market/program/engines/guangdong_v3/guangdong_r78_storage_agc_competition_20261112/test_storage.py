import copy
from decimal import Decimal as D
import pytest
from storage import run,fm,Missing,Unsupported
E=dict(id='fixture',source='synthetic NOT operating data',source_grade='research',hour_start='2026-11-12T01:00:00+08:00',as_of='2026-11-12T00:00:00+08:00')
def fixture():
 vals=dict(G='80',H='20',QLL='.2',Ux='.7',Uy='2.5',zx='.4',zy='.6',Qlb='5',bid_floor='3',bid_upper='15',lambdaI='.5',lambdaII='.25',lambdaIII='.25',KI_upper='5',Lss_hours='1',Ts_hours='8')
 qual={k:True for k in ('market_registered','dispatch_direct_control','dispatch_agreement','purchase_sale_contract','agc_capable','initial_model_performance_test','autor_test_published','simultaneous_energy_market_qualified','simultaneous_technical_test_completed')};qual.update(evidence=E,sustained_response_hours='1',autor_test_hours='8')
 offers=[dict(resource_id=r,participant_type='independent_storage_third_party',zone='GD_fixture',evidence=E,submitted=True,normal_service=True,operator_excluded=False,participation_mode='simultaneous_energy_agc',qualification=qual,rated_power_mw='100',installed_capacity_mw='100',declared_capacity_mw='75',bid_yuan_per_mw_mileage='15',k='1')for r in ('D','E')]
 return copy.deepcopy(dict(schema='gd.agc.storage.r78.v1',mode='research',hour_start=E['hour_start'],as_of='2026-11-12T00:30:00+08:00',zone='GD_fixture',population=dict(complete=True,zone='GD_fixture',demand_mw='1200',evidence=E,offers=offers),parameters=dict(version='scan_baseline_fixture_not_current',evidence=E,effective_from='2026-11-12T00:00:00+08:00',effective_to='2026-11-13T00:00:00+08:00',values=vals),performance_universe=dict(scope='entire_southern_unified_control_area',complete=True,kmax='3',evidence=E)))
def test_tied_group_and_id_invariance():
 d=fixture();r=run(d);assert len(r['tie_groups'])==1;assert all(x['x']==D('.125')for x in r['candidates']);d['population']['offers'][0]['resource_id']='Z';d['population']['offers'].reverse();assert [x['Fm']for x in run(d)['candidates']]==[x['Fm']for x in r['candidates']]
def test_curve_edges():
 p={k:D(v)for k,v in fixture()['parameters']['values'].items()}
 assert fm(D(0),p)==D('2.5')and fm(D('.4'),p)==D('.6')
 with pytest.raises(Unsupported):fm(D('.7'),p)
def test_domain_conflict():
 d=fixture();d['population']['demand_mw']='50';d['population']['offers']=d['population']['offers'][:1];d['population']['offers'][0].update(rated_power_mw='200',installed_capacity_mw='200',declared_capacity_mw='10');r=run(d);assert r['status']=='unsupported_domain_no_partial_population_mapping'
def test_cumulative_endpoint():
 d=fixture();d['parameters']['values']['Ux']='.125';d['parameters']['values']['zx']='.1';r=run(d);assert r['status']=='unsupported_cumulative_domain';assert all(x['Fm']==0 and x['sorting_price']is None for x in r['candidates'])
def test_completeness_and_qualification():
 for f in ('population','performance_universe'):
  d=fixture();d[f]['complete']=False
  with pytest.raises(Missing):run(d)
 d=fixture();d['population']['offers'][0]['qualification']['simultaneous_technical_test_completed']=False
 with pytest.raises(Missing):run(d)
def test_time_and_source():
 for edit in ('hour','future','source'):
  d=fixture();e=d['parameters']['evidence'];e['hour_start'if edit=='hour'else'as_of'if edit=='future'else'source']='2026-11-12T02:00:00+08:00'if edit!='source'else''
  with pytest.raises(Missing):run(d)
def test_unsubmitted_no_defaults():
 d=fixture();u=d['population']['offers'][0];u.update(submitted=False,exclusion_reason='not declared',exclusion_evidence=copy.deepcopy(E));del u['bid_yuan_per_mw_mileage'];r=run(d);assert len(r['excluded'])==1 and len(r['candidates'])==1
def test_price_not_capped_or_settlement_multiplier():
 r=run(fixture());assert all(x['sorting_price']>15 for x in r['candidates']);assert not r['settlement_Fm_application'];assert r['K_U']==3 and not r['formal_eligibility_granted']
def test_increments_and_boundaries():
 d=fixture();d['population']['offers'][0]['declared_capacity_mw']='75.1'
 with pytest.raises(Missing):run(d)
 d=fixture();d['parameters']['effective_to']='2026-11-12T01:30:00+08:00'
 with pytest.raises(Missing):run(d)

def test_participant_scope_guard():
 for value in (None,'generator_attached_storage'):
  d=fixture();d['population']['offers'][0]['participant_type']=value
  with pytest.raises(Missing,match='section49'):run(d)
def test_common_upper_bound_guard():
 d=fixture();d['performance_universe']['kmax']='3.1'
 with pytest.raises(Missing,match='heterogeneous'):run(d)
