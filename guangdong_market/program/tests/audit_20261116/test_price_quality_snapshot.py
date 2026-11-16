"""Call-scoped snapshot optimization must retain fresh integrity verification."""
import hashlib,json
from pathlib import Path
import unittest,tempfile
from unittest.mock import patch
from gd_price_quality import from_accepted


def fixture(tmp_path):
 p=tmp_path/'prepared';n=p/'native_once';n.mkdir(parents=True)
 def put(path,value):path.write_text(json.dumps(value));return hashlib.sha256(path.read_bytes()).hexdigest()
 put(p/'names.json',{'variables':['PRICE_load_shortage[0,0]']})
 raw_sha=put(n/'response.json',{'primal':[0.0],'optimize_calls':1})
 put(n/'COMPLETED.json',{'response_sha256':raw_sha,'returncode':0})
 put(n/'IDENTITY.json',{'identity':'test'})
 context={'start':'2026-11-16T00:00:00+08:00'}
 common={'context':context,'raw_prices':{'0':5},'KKT':{'passed':True}}
 put(p/'PRICE_RESULT.json',{**common,'slacks':{'PRICE_load_shortage[0,0]':0.0}})
 accepted=tmp_path/'ACCEPTED.json'
 put(accepted,{**common,'source_pins':{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in [p/'names.json',n/'response.json',n/'COMPLETED.json',n/'IDENTITY.json']}})
 return accepted


class SnapshotTests(unittest.TestCase):
 def test_each_file_once_per_refresh(self):
  with tempfile.TemporaryDirectory() as d:
   a=fixture(Path(d));reads={};original=Path.read_bytes
   def read(p):reads[str(p)]=reads.get(str(p),0)+1;return original(p)
   with patch.object(Path,'read_bytes',read):
    self.assertEqual(from_accepted(a)['status'],'no_material_penalty_slacks')
    self.assertEqual(len(reads),6);self.assertEqual(set(reads.values()),{1})
    from_accepted(a);self.assertEqual(set(reads.values()),{2})
 def test_changed_bound_files(self):
  for relative in ['names.json','native_once/response.json','native_once/COMPLETED.json','native_once/IDENTITY.json']:
   with self.subTest(relative=relative),tempfile.TemporaryDirectory() as d:
    root=Path(d);a=fixture(root);from_accepted(a)
    f=root/'prepared'/relative;f.write_bytes(f.read_bytes()+b' ')
    with self.assertRaisesRegex(ValueError,'raw binding changed'):from_accepted(a)
 def test_alias_conflict(self):
  with tempfile.TemporaryDirectory() as d:
   a=fixture(Path(d));v=json.loads(a.read_text());v['source_pins'][str(Path(d)/'prepared')+'/./names.json']='0'*64;a.write_text(json.dumps(v))
   with self.assertRaisesRegex(ValueError,'conflicting'):from_accepted(a)
 def test_missing_raw_stays_unknown(self):
  with tempfile.TemporaryDirectory() as d:
   a=fixture(Path(d));(Path(d)/'prepared/PRICE_RESULT.json').unlink()
   self.assertEqual(from_accepted(a)['status'],'unknown')

if __name__=='__main__':unittest.main()
