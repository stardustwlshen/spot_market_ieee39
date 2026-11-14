from rt_price_settlement import *
import pytest

def test_receipt_wrong_source():
 i={'projection':{'run_id':'fresh'},'contract_sha256':'a','window':{'index':0}};r=dict(stage='RT',accepted=True,run_id='old',source_contract_sha256='a',local_input_content_sha256=digest(i),window=0,event_id='x',request_sha256='q',response_sha256='r',research_only=True)
 with pytest.raises(ValueError):validate_price_receipt(i,r)
 r['run_id']='fresh';validate_price_receipt(i,r);r['local_input_content_sha256']='wrong'
 with pytest.raises(ValueError):validate_price_receipt(i,r)
def test_missing_execution():
 with pytest.raises(ValueError):validate_execution({},[],[],[],dict(complete=False,executed_cells=0))
def test_unknown_contracts_not_zero():
 with pytest.raises(ValueError):compute_settlement({},[],[],[],{},None,explicit_synthetic_contract_assumptions=False)
def test_missing_price_no_fallback():
 with pytest.raises(ValueError):bound_hourly_prices({str(i):[0]*98 for i in range(39)},[],day=datetime(2026,11,11),source_receipt={},price_limit_spec=None)
def test_direct_fresh_build_exact_retained_math():
 # Historical artifact is test oracle only; build API never reads its directory.
 p=ROOT/'guangdong_r74_quarter_price_20261112/batch_six_v1/W0';i=json.loads((p/'input.json').read_bytes());r=dict(stage='RT',accepted=True,run_id=i['projection']['run_id'],source_contract_sha256=i['contract_sha256'],local_input_content_sha256=digest(i),window=0,event_id='explicit-test-oracle',request_sha256='q',response_sha256='r',research_only=True)
 q,n,t=build_rt_price(i,r);old=json.loads((p/'template.json').read_bytes());assert q==old;assert n==json.loads((p/'names.json').read_bytes());assert t['RT_dual_used']is False and t['market_price']is None

def test_exact_accounting_transport_roundtrip():
 x={'price_kind':'source_verified_bounded_research_price','amount':Decimal('1234567890.12345678901234567890'),'price':Decimal('-50.000'),'at':datetime.fromisoformat('2026-11-11T00:00:00+08:00'),'components':(Decimal('0.000000000000000001'),),'formal_bill':None}
 y=json.loads(dumps_exact(x));assert y['amount']=='1234567890.12345678901234567890'and y['price']=='-50.000'and y['price_kind']==x['price_kind'];assert y['components']==['1E-18'];assert Decimal(y['amount'])==x['amount'];assert y['at']=='2026-11-11T00:00:00+08:00'

def test_manifest_identity_range_and_duplicates():
 r={'run_id':'new','source_contract_sha256':'s'};m=dict(schema='gd.pipeline.rt_price_manifest.v1',**r,records=[{'window':w}for w in range(96)]);assert len(validate_price_manifest(m,r))==96
 for bad in (-1,96,True,1):
  z=json.loads(json.dumps(m));z['records'][0]['window']=bad
  with pytest.raises(ValueError):validate_price_manifest(z,r)
 z=dict(m,run_id='old')
 with pytest.raises(ValueError):validate_price_manifest(z,r)
 z=dict(m,source_contract_sha256='other')
 with pytest.raises(ValueError):validate_price_manifest(z,r)
