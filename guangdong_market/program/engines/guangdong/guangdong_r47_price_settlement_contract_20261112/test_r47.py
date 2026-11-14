from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
from dataclasses import replace
import pytest
from r47_price import PriceLimitSpec,price_candidate,hourly_candidate
from r47_settlement_inputs import vre_readiness,synthetic_participating_DA
T=datetime(2026,11,12,tzinfo=timezone.utc)
S=PriceLimitSpec('GD','both','CNY/MWh',D(-50),D(1800),datetime(2025,11,1,tzinfo=timezone.utc),None,'https://drc.gd.gov.cn/gkmlpt/content/4/4775/post_4775501.html','263','official_published',T,True,'not_found',False)
def q(raw,start=T,**kw):
 return price_candidate(spec=kw.pop('spec',S),status=kw.pop('status','success'),run_id='r',node='n',start=start,end=start+timedelta(minutes=15),stage='RT',raw_price=None if raw is None else D(raw),evidence_id='raw',evidence_sha256='a'*64,**kw)
def test_limits_raw_immutable():
 a=q(-1000);b=q(2000);assert a['raw_price']==-1000 and a['bounded_candidate_price']==-50 and b['bounded_candidate_price']==1800 and a['official_market_price']is None
def test_quarter_then_hour():
 qs=[q(v,T+timedelta(minutes=15*i))for i,v in enumerate([-1000,100,100,100])];assert hourly_candidate(qs)['bounded_hourly_candidate_price']==D('62.5')
def test_no_invented_year_expiry():
 assert q(10)['basis']=='published_policy_basis' and not S.dynamic_current_verified
 with pytest.raises(ValueError):q(10,datetime(2025,10,31,tzinfo=timezone.utc))
def test_override_and_scope():
 for v in ['known','conflict']:
  with pytest.raises(ValueError):q(10,spec=replace(S,override_status=v))
 with pytest.raises(ValueError):q(10,spec=replace(S,region='OTHER'))
def test_bad_native_no_missing_conversion():
 for status in ['unknown_native','invalid_data']:
  with pytest.raises(ValueError):q(0,status=status)
 a=q(None,status='confirmed_missing');assert a['bounded_candidate_price']is None
 qs=[q(10,T+timedelta(minutes=15*i))for i in range(4)];qs[0]=a;assert hourly_candidate(qs)['bounded_hourly_candidate_price']is None
def test_bad_hour_identity():
 qs=[q(10,T+timedelta(minutes=15*i))for i in range(4)]
 for field,value in [('node','other'),('run_id','other'),('start',T)]:
  bad=[dict(x)for x in qs];bad[1][field]=value
  with pytest.raises(ValueError):hourly_candidate(bad)
def test_evidence_synthetic_expiry():
 assert q(20,spec=replace(S,source_status='synthetic'))['basis']=='illustrative'
 with pytest.raises(ValueError):q(20,spec=replace(S,effective_to=T+timedelta(minutes=5)))
 with pytest.raises(ValueError):price_candidate(spec=S,status='success',run_id='r',node='n',start=T,end=T+timedelta(minutes=15),stage='RT',raw_price=D(20))
def test_vre_missing_not_zero():
 d=vre_readiness(dict(participation='unknown',mapping={'unit':'bus1','resource':'agg','node':'1','source':'R45','identity_kind':'synthetic_aggregate'},quantity_kind='simulated_executed',actual_meter=None,financial_DA=None,contracts=None,price_kind='raw_only'))
 assert d['amount']is None and not d['payable'] and 'participation'in d['missing'] and 'metered_quantity'in d['missing'] and any('contracts' in x for x in d['missing'])
def scalar(x,unit):return dict(value=D(x),unit=unit,node='n',start=T,end=T+timedelta(hours=1),source='synthetic fixture')
def fixture():
 return dict(participation='participates',node='n',start=T,end=T+timedelta(hours=1),quantity_kind='synthetic_metered_fixture',params={'QDA':scalar(100,'MWh'),'Qmeter':scalar(110,'MWh'),'PDA':scalar(400,'CNY/MWh'),'PRT':scalar(500,'CNY/MWh')},contracts=[{'contract_id':'L1','reference_node':'n','reference_market':'DA','Q':scalar(50,'MWh'),'Pc':scalar(300,'CNY/MWh'),'Pref':dict(scalar(250,'CNY/MWh'),reference_market='DA')}],contracts_source='synthetic disclosed inventory')
def test_synthetic_participating_math():
 d=synthetic_participating_DA(**fixture());assert (d['LT'],d['DA'],d['RT'],d['amount'])==(22500,20000,5000,47500);assert d['formal_bill']is None and not d['payable'] and d['monthly_mechanism']is None
def test_nonDA_unknown_and_zero_contract_source():
 f=fixture();f['participation']='does_not_participate';assert synthetic_participating_DA(**f)['route']=='needs_nonDA_rule_mapping'
 f['participation']='unknown';assert synthetic_participating_DA(**f)['amount']is None
 f=fixture();f['contracts']=[];f['contracts_source']=None
 with pytest.raises(ValueError):synthetic_participating_DA(**f)
def test_quantity_and_parameter_provenance():
 f=fixture();f['quantity_kind']='simulated_executed'
 with pytest.raises(ValueError):synthetic_participating_DA(**f)
 f=fixture();f['params']['PDA']['node']='other'
 with pytest.raises(ValueError):synthetic_participating_DA(**f)
def test_actual_r45_pinned_binding():
 from pathlib import Path
 import hashlib
 from r47_settlement_inputs import bind_r45_vre
 p=Path(__file__).resolve().parent.parent/'guangdong_r45_actual_noon_20261112/native_four_v1/post_verification.json'
 if not p.exists():pytest.skip('external preserved R45 evidence not available')
 h=hashlib.sha256(p.read_bytes()).hexdigest();d=bind_r45_vre(p,h,'30');assert len(d['rows'])==4 and all(x['readiness']['amount']is None for x in d['rows'])
 with pytest.raises(ValueError):bind_r45_vre(p,'0'*64,'30')
def test_distinct_contract_reference_and_duplicate():
 f=fixture();f['contracts'][0]['reference_node']='provincial_ref';f['contracts'][0]['Pref']['node']='provincial_ref'
 assert synthetic_participating_DA(**f)['amount']==47500
 f['contracts'].append(dict(f['contracts'][0]))
 with pytest.raises(ValueError):synthetic_participating_DA(**f)
def test_tampered_hour_candidate():
 qs=[q(10,T+timedelta(minutes=15*i))for i in range(4)];qs[0]['bounded_candidate_price']=D(999)
 with pytest.raises(ValueError):hourly_candidate(qs)
def test_auxiliary_not_energy_prerequisite():
 d=vre_readiness(dict(participation='participates',mapping={'unit':'u','resource':'r','node':'n','source':'s','identity_kind':'real_unit'},quantity_kind='metered',actual_meter=10,financial_DA=9,contracts=[],contracts_source='knownzero',price_kind='verified_settlement'))
 assert not d['missing'] and d['separate_unclosed_components']['AGC_mileage']is None and not d['payable']
def test_r45_pending_and_unknown(tmp_path):
 from pathlib import Path
 import json,hashlib
 from r47_settlement_inputs import bind_r45_vre
 src=Path(__file__).resolve().parent.parent/'guangdong_r45_actual_noon_20261112/native_four_v1/post_verification.json'
 if not src.exists():pytest.skip('preserved R45 unavailable')
 d=json.loads(src.read_text());d['executed_quarters'][0]['raw_price_status']='unpriced_pending';d['executed_quarters'][0]['raw_unclipped_PRICE']=None;p=tmp_path/'pending.json';p.write_text(json.dumps(d));h=hashlib.sha256(p.read_bytes()).hexdigest()
 assert bind_r45_vre(p,h,'30')['rows'][0]['raw_price']is None
 d['executed_quarters'][0]['raw_price_status']='unknown_native';p.write_text(json.dumps(d));h=hashlib.sha256(p.read_bytes()).hexdigest()
 with pytest.raises(ValueError):bind_r45_vre(p,h,'30')

def test_final_strict_boundaries():
 with pytest.raises(ValueError):q(0,status='confirmed_missing')
 with pytest.raises(ValueError):q(10,T+timedelta(minutes=1))
 f=fixture();f['params']['Qmeter']['value']=D(-1)
 with pytest.raises(ValueError):synthetic_participating_DA(**f)
 f=fixture();f['contracts'][0]['Pref']['reference_market']='RT'
 with pytest.raises(ValueError):synthetic_participating_DA(**f)
