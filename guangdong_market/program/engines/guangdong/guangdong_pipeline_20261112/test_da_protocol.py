import unittest,tempfile
from pathlib import Path
from run_protocol import *
import da_pipeline as D
class Tests(unittest.TestCase):
 def test_relative_hash_reference(self):
  with tempfile.TemporaryDirectory()as t:
   p=Path(t)/'x.json';write(p,{'a':1});r=ref(t,p);self.assertEqual(resolve(t,r),p);p.write_text('{}')
   with self.assertRaises(ValueError):resolve(t,r)
 def test_escape_ref(self):
  with tempfile.TemporaryDirectory()as t:
   with self.assertRaises(ValueError):resolve(t,dict(path='../missing',sha256='0'*64))
 def test_sced_cost_scope(self):
  q=dict(objective=dict(terms=[[0,10],[1,30],[2,5]],constant=7,minimize=True));n={'variables':['online[G,0]','start[G,0]','power[G,0]']};c={'energy_inputs':{'thermal':{'G':{'startup_yuan':{0:20}}}}}
  new,removed=D.sced_objective(q,n,c);self.assertEqual(new['objective']['terms'],[[1,10],[2,5]]);self.assertEqual(new['objective']['constant'],7);self.assertEqual(q['objective']['terms'][0],[0,10]);self.assertEqual(sum(z['coefficient']for z in removed),30)
 def test_no_native_during_import(self):
  import da_native
  self.assertTrue(callable(da_native.execute))
if __name__=='__main__':unittest.main()

class InputScopeTests(unittest.TestCase):
 def check(self,change):
  from unittest.mock import patch
  from copy import deepcopy
  base=PACKAGE/'examples/ieee39_research';docs={k:read(base/(k+'.json'))for k in ('grid','policy','agc')};change(docs)
  with patch.object(D,'verify_sources',return_value={'inputs':{}}),patch.object(D,'load_case',return_value={}),patch.object(D,'read',side_effect=lambda p:docs[Path(p).stem]):return D.inputs('/not-read')
 def test_one_each_tail(self):
  with self.assertRaises(ValueError):self.check(lambda x:x['grid'][97].update(kind=x['grid'][96]['kind']))
 def test_finite_and_chain_policy(self):
  for value in (float('inf'),999):
   with self.assertRaises(ValueError):self.check(lambda x:x['policy']['curtailment_penalty'].update(yuan_per_mwh=value))
 def test_derived_idempotence(self):
  with tempfile.TemporaryDirectory()as t:
   p=Path(t)/'derived.json';D.derived_write(p,{'a':1});D.derived_write(p,{'a':1})
   with self.assertRaises(ValueError):D.derived_write(p,{'a':2})
