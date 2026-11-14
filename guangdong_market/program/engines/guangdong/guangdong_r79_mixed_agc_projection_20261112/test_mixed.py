import importlib.util,sys,copy
from pathlib import Path
from decimal import Decimal as D
import pytest
from mixed import run,ROOT
sys.path.insert(0,str(ROOT/'guangdong_r78_storage_agc_competition_20261112'))
def load(name,path):
 s=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
T=load('r79_tfixture','guangdong_r73_agc_parameter_interface_20261112/test_adapter.py')
S=load('r79_sfixture','guangdong_r78_storage_agc_competition_20261112/test_storage.py')
def fixture():
 t=T.fixture();s=S.fixture();s['as_of']=t['as_of'];s['hour_start']=t['hour_start'];s['population']['demand_mw']='200';s['population']['offers']=s['population']['offers'][:1]
 u=s['population']['offers'][0];u.update(declared_capacity_mw='40',bid_yuan_per_mw_mileage='3',k='3',k1='1')
 t['parameters']['bid_floor']['value']=3;t['parameters']['full_control_area_kmax']['value']=3
 for u in t['units']:u['zone']=s['zone'];u['bid']+=2
 e=s['parameters']['evidence'];e.update(source='shared synthetic source',as_of=t['parameters']['bid_floor']['available_at'])
 for group in (s['population'],s['performance_universe']):group['evidence']=copy.deepcopy(e)
 s['parameters']['effective_from']=t['parameters']['bid_floor']['effective_from'];s['parameters']['effective_to']=t['parameters']['bid_floor']['effective_to']
 for n in ('bid_floor','full_control_area_kmax','distribution_zone_demand_mw'):t['parameters'][n]['source']=e['source']
 return dict(schema='gd.agc.mixed.r79.v1',mode='research',thermal=t,storage=s,common_binding=dict(zone=s['zone'],source_ids={'synthetic_v1':'fixture'}),floor_crossing_policy='exclude_crossing_unit',exact_tie_policy='resource_id_research',candidate_price_cap='15')
def test_mixed_whole_storage():
 r=run(fixture());assert r['ranked'][0]['kind']=='independent_storage_third_party';assert r['awards_mw']['D']==40 and len(r['awards_mw'])==3;assert r['requires_R77_and_joint_energy_safety']and not r['regional_uniform_price']
def test_sameplant_recompute():
 d=fixture();d['thermal']['units'][1]['plant_id']='P0';r=run(d)
 assert r['trace'][2]['awards_mw']['T0']==20 and r['trace'][2]['awards_mw']['T1']==20 and r['trace'][2]['awards_mw']['D']==40
 assert len(r['awards_mw'])==4
def test_no_fractional_storage():
 d=fixture();d['thermal']['parameters']['provincial_procurement_target_mw']['value']=30
 assert run(d)['awards_mw']['D']==40
def test_missing_qualification():
 d=fixture();d['storage']['population']['offers'][0]['qualification']['market_registered']=False
 with pytest.raises(ValueError):run(d)
def test_provenance_negative():
 for key in ('source','available_at'):
  d=fixture();d['thermal']['parameters']['full_control_area_kmax'][key]='other'if key=='source'else'2026-11-10T00:00:00+08:00'
  with pytest.raises(ValueError):run(d)
def test_time_zone_mismatch():
 d=fixture();d['common_binding']['zone']='elsewhere'
 with pytest.raises(ValueError):run(d)
 d=fixture();d['thermal']['hour_start']='2026-11-12T02:00:00+08:00'
 with pytest.raises(ValueError):run(d)
def test_unsupported_group_not_dropped():
 d=fixture();d['storage']['parameters']['values']['Ux']='.2';d['storage']['parameters']['values']['zx']='.1'
 with pytest.raises(ValueError,match='entire storage'):run(d)
def test_shortage_no_price_and_floor():
 d=fixture();d['thermal']['units']=[];r=run(d);assert r['unmet_mw']==60 and r['candidate_price']is None
 d=fixture();d['thermal']['parameters']['local_reserved_floor_mw']['value']=100;r=run(d);assert r['candidate_price']is None

def test_common_k1_upper_guard():
 for kind in ('thermal','storage'):
  d=fixture()
  if kind=='thermal':d['thermal']['units'][0]['k1']=6
  else:d['storage']['population']['offers'][0]['k1']='6'
  with pytest.raises(ValueError,match='k1 exceeds common'):run(d)
