import json,unittest,sys,copy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import adapter as A
sys.path.insert(0,str(A.ROOT/'guangdong_r36_energy_settlement_20261112'))
import test_r36 as F

def fixture(storage=False,charge=False,quarter=False):
 x=F.fixture(storage,charge,quarter);x=json.loads(json.dumps(x,default=A.encode));x.update(schema='gd.settlement.r63.ledger.v1',authenticity={'status':'synthetic_declared','source':'R63 disclosed test fixture'},account_mapping_evidence=json.loads(json.dumps(F.E,default=A.encode)),contract_inventory_evidence=json.loads(json.dumps(F.E,default=A.encode)),known_empty_contracts=False,meter_kind='synthetic_meter_fixture')
 for n in ('da','rt'):x[n]['price_kind']='synthetic_price_fixture'
 for c in x['contracts']+(x['agency_contracts']or[]):c.update(reference_selection_source='explicit synthetic contract reference choice',reference_market='DA',reference_price_kind='synthetic_price_fixture',reference_interval={'start':F.T.isoformat(),'end':(F.T+timedelta(hours=1)).isoformat()})
 return x
class Tests(unittest.TestCase):
 def test_reuses_r36(self):
  with patch.object(A.R,'settle_energy',wraps=A.R.settle_energy)as f:r=A.run(fixture());self.assertEqual(f.call_count,1);self.assertEqual(r['result']['energy_component_subtotal'],47900);self.assertFalse(r['payable'])
 def test_hourly_price_not_weighted(self):
  d={'schema':'gd.settlement.r63.aggregate_hour.v1','kind':'price','channel':'simulation','asof':(F.T+timedelta(days=1)).isoformat(),'price_kind':'synthetic_price_fixture','quarters':[{'start':(F.T+timedelta(minutes=15*i)).isoformat(),'value':json.loads(json.dumps(F.v(v,'CNY/MWh'),default=A.encode))}for i,v in enumerate((100,200,300,400))]};r=A.run(d);self.assertEqual(r['result']['value'],250);self.assertEqual((10-8)*r['result']['value'],500)
 def test_signed_pair(self):
  a,b=fixture(True),fixture(True,True)
  for x in (a,b):x['rt']['price']['value']='60'
  r=A.run({'schema':'gd.settlement.r63.storage_pair.v1','discharge':a,'charge':b});self.assertEqual(r['discharge']['result']['components']['RT_deviation'],600);self.assertEqual(r['charge']['result']['components']['RT_deviation'],-600)
  b['key']['settlement_unit_id']=a['key']['settlement_unit_id'];self.assertTrue(A.run({'schema':'gd.settlement.r63.storage_pair.v1','discharge':a,'charge':b})['status'].startswith('pending'))
 def test_quarter_contract(self):
  x=fixture(True,True,True);self.assertEqual(x['contracts'][0]['hourly_quantity']['value'],'-200');self.assertEqual(x['contracts'][0]['quantity']['value'],'-50');self.assertEqual(A.run(x)['status'],'energy_components_only');x['quarter_eligibility']=None;self.assertTrue(A.run(x)['status'].startswith('pending'))
 def test_bad_price_and_missing_meter(self):
  for field in ('raw_solver','bounded_candidate','offer'):
   x=fixture();x['rt']['price_kind']=field;self.assertTrue(A.run(x)['status'].startswith('pending'))
  x=fixture();x['meter']=None;self.assertTrue(A.run(x)['status'].startswith('pending'))
  x=fixture();x['channel']='formal_data';x['meter_kind']='simulated_executed';self.assertTrue(A.run(x)['status'].startswith('pending'))
 def test_unknown_and_duplicates(self):
  for key,v in [('participation','unknown'),('agency_mode','unknown'),('contracts',None)]:
   x=fixture();x[key]=v;self.assertTrue(A.run(x)['status'].startswith('pending'))
  x=fixture();x['contracts']*=2;self.assertTrue(A.run(x)['status'].startswith('pending'))
 def test_explicit_zero_da_allowed(self):
  x=fixture();x['da']['quantity']['value']='0';self.assertEqual(A.run(x)['status'],'energy_components_only')
 def test_known_empty_only_research(self):
  x=fixture(True);x['contracts']=[];x['known_empty_contracts']=True;r=A.run(x);self.assertEqual(r['status'],'energy_components_only');self.assertTrue(r['empty_contract_internal_zero']);self.assertFalse(r['payable'])
  x['known_empty_contracts']=False;self.assertTrue(A.run(x)['status'].startswith('pending'))
 def test_units_and_scope(self):
  x=fixture();x['meter']['unit']='MW';self.assertTrue(A.run(x)['status'].startswith('pending'))
  x=fixture();x['rt']['key']['node_id']='other';self.assertTrue(A.run(x)['status'].startswith('pending'))
if __name__=='__main__':unittest.main()
