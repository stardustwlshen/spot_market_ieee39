import unittest,tempfile
from pathlib import Path
from evidence_io import *
class T(unittest.TestCase):
 def test_roundtrip(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d)/'x.gz';r=save(p,{'a':[1,2]});self.assertEqual(load(r),{'a':[1,2]})
   with self.assertRaises(FileExistsError):save(p,{})
 def test_bad_hash(self):
  with tempfile.TemporaryDirectory()as d:
   r=save(Path(d)/'x.gz',{'a':1});r['raw_sha256']='0'*64
   with self.assertRaises(ValueError):load(r)
 def test_bad_length(self):
  with tempfile.TemporaryDirectory()as d:
   r=save(Path(d)/'x.gz',{});r['raw_bytes']+=1
   with self.assertRaises(ValueError):load(r)
 def test_storage_damage(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d)/'x.gz';r=save(p,{});p.write_bytes(b'bad')
   with self.assertRaises(ValueError):load(r)
if __name__=='__main__':unittest.main()
