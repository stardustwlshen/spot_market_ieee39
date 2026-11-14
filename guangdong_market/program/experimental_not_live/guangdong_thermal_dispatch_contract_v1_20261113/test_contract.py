from contract import *
from pathlib import Path
import unittest,sys,json
B=Path(__file__).resolve().parent;R=B.parent
sys.path.insert(0,str(R/'guangdong_thermal_notice_bridge_v2_20261113'))
import test_bridge as old
at=lambda t:(clock(old.ORIGIN)+timedelta(minutes=5*t)).isoformat()
def rec(kind='commercial_DA',a=9,b=12,**kw):
 z=dict(id='record',series='series',version=1,unit='G',kind=kind,valid_from=at(a),valid_to=at(b),available_at=at(0),source={'id':'test fixture','sha256':'a'*64},issuer='research fixture',approval='research_declared',scope='reference',rows=[{'online':1}for _ in range(b-a)]);z.update(kw);return z
def doc(records):return dict(schema='gd.thermal.dispatch.contract.v1',mode='research',run_id=old.CONTEXT['run_id'],origin=old.ORIGIN,records=records)
def compile(d):return compile_contract(d,cutoff=at(6),begin=at(9),cells=24,units=['G'],expected_sha256=digest(d))
def proposal(on=1):return {'G':[dict(online=on,start=0,stop=0,power=100.*on)for _ in range(24)]}
class Tests(unittest.TestCase):
 def test_da_reference_not_all_day(self):
  c=compile(doc([rec()]));self.assertTrue(all(not x for x in c['fixes']['G']));self.assertFalse(check_publication(c,proposal(0))['research_publication_eligible']);self.assertEqual(len(check_publication(c,proposal(0))['violations']),6)
 def test_must_on_interval(self):
  c=compile(doc([rec('must_on',scope='candidate')]));self.assertEqual(sum(bool(x)for x in c['fixes']['G']),3)
 def test_permission_bounded(self):
  r=rec('permission',a=9,b=10,id='permit',series='permit',scope='publication',fields=['online','stop'],target_ids=['record'],safety_exception=True,reason='explicit synthetic safety exception');c=compile(doc([rec(),r]));self.assertEqual(len(check_publication(c,proposal(0))['violations']),4)
 def test_permission_not_erase_hard(self):
  c=compile(doc([rec('must_on',scope='candidate')]));
  with self.assertRaises(ValueError):check_publication(c,proposal(0))
 def test_formal_blocked(self):
  d=doc([]);d['mode']='formal'
  with self.assertRaisesRegex(ValueError,'FORMAL_BLOCKED'):compile(d)
 def test_future_version_not_selected(self):
  a=rec();b=rec(version=2,id='future',available_at=at(7));self.assertEqual(compile(doc([a,b]))['selected_ids'],['record'])
 def test_duplicate_version(self):
  with self.assertRaises(ValueError):compile(doc([rec(),rec(id='other')]))
 def test_conflict(self):
  with self.assertRaises(ValueError):compile(doc([rec('must_on',scope='candidate'),rec('must_off',id='off',series='off',scope='candidate')]))
 def test_future_actual(self):
  with self.assertRaises(ValueError):compile(doc([rec('actual_curve',scope='candidate')]))
 def test_clock_and_source(self):
  for changes in [dict(valid_to=at(9)),dict(available_at='2026-11-11T00:00:00+00:00'),dict(source={'id':'x','sha256':'bad'})]:
   with self.assertRaises(ValueError):compile(doc([rec(**changes)]))
 def test_immutable_version(self):
  with self.assertRaises(ValueError):compile(doc([rec('issued_instruction',scope='candidate'),rec('issued_instruction',scope='candidate',id='new',version=2)]))
 def test_reliability_not_invented_muston(self):self.assertTrue(all(not x for x in compile(doc([rec('reliability_plan')]))['fixes']['G']))
 def test_bridge_and_existing_native(self):
  ledger0=old.publish_due(old.PLAN,None,cutoff_tick=0,simulate_publish=True)
  pre=old.connect_boundary(old.initial(),old.PARAM,ledger0,old.cells(0,6),cutoff_tick=6,executed_cells=6,origin=old.ORIGIN,source='existing accepted pair')
  ledger=old.publish_due(old.PLAN,ledger0,cutoff_tick=6,previous_sha256=old.digest(ledger0),simulate_publish=True)
  issued=old.cells(6,9)
  for x in issued:x.update(issued_tick=6,issued_receipt_hash='explicit synthetic prefix fixture')
  conn=old.connect_boundary(pre['carry']['executed_boundary'],old.PARAM,ledger,issued,cutoff_tick=6,executed_cells=0,origin=old.ORIGIN,source='existing accepted pair issued prefix research')
  c=compile(doc(issued_records_from_ledger(ledger)));bound=bind_notice_carry(c,conn,ledger);self.assertEqual(len(bound['thermal']['G']['remaining_curve']),3)
  # Existing next-window result is checked, never solved anew.
  out=R/'guangdong_thermal_notice_bridge_v2_20261113/connected_second_window';n=json.loads((out/'names.json').read_text());sol=old.read(out/'SCUC/response.json.gz');v=dict(zip(n['variables'],sol['primal']))
  for j,row in enumerate(c['fixes']['G']):
   for f,z in row.items():self.assertAlmostEqual(v[f'{f}[G,{j}]'],z['value'])
  (B/'COMPILED_R68.json').write_text(json.dumps(c,indent=2));(B/'BUILDER_BOUNDARY_R68.json').write_text(json.dumps(bound,indent=2))
 def test_model_adapter(self):
  import pyomo.environ as p
  m=p.ConcreteModel();m.G=p.Set(initialize=['G']);m.T=p.RangeSet(0,23)
  for f in FIELDS:setattr(m,f,p.Var(m.G,m.T))
  self.assertEqual(apply_to_model(m,compile(doc([rec('must_on',scope='candidate')])) )['rows'],6)
if __name__=='__main__':unittest.main(verbosity=2)
