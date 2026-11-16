"""Source verification must not trust cross-call metadata-only cached digests."""
import os,sys,types,tempfile,hashlib,unittest,importlib.util
from pathlib import Path
from unittest.mock import patch
APP=Path(os.environ.get('GD_TEST_APP',str(Path(__file__).resolve().parents[2]/'app')))
spec=importlib.util.spec_from_file_location('hash_under_test',os.environ.get('GD_TEST_HASH',str(APP/'lifecycle_runtime/content_hash.py')));g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
class HashTests(unittest.TestCase):
 def setUp(self):g._CACHE.clear()
 def test_changed_bytes_same_metadata_all_platforms(self):
  for platform in ['nt','posix']:
   with self.subTest(platform=platform),tempfile.TemporaryDirectory() as d:
    p=Path(d)/'输入.json';p.write_bytes(b'1111');meta=p.stat();fake=types.SimpleNamespace(name=platform,fstat=lambda _:meta)
    with patch.object(g,'os',fake),patch.object(Path,'stat',return_value=meta):
     first=g.sha(p);p.write_bytes(b'2222');second=g.sha(p)
    self.assertNotEqual(first,second);self.assertEqual(second,hashlib.sha256(b'2222').hexdigest())
 def test_stale_cache_never_authorizes_digest(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a';p.write_bytes(b'abcd');g._CACHE[str(p)]=((str(p),*g.signature(p.stat())),'0'*64)
   self.assertEqual(g.sha(p),hashlib.sha256(b'abcd').hexdigest())
 def test_every_call_reads_bytes(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a';p.write_bytes(b'abcd');n=g.STATS['full_reads'];g.sha(p);g.sha(p);self.assertEqual(g.STATS['full_reads'],n+2)
 def test_metadata_change_during_read_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'a';p.write_bytes(b'abcd');meta=p.stat();changed=types.SimpleNamespace(**{k:getattr(meta,k) for k in ['st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns']});changed.st_size+=1
   with patch.object(g.os,'fstat',side_effect=[meta,changed]):
    with self.assertRaisesRegex(ValueError,'changed during'):g.sha(p)
 def test_symlink_parent_traversal_preserved(self):
  if os.name!='posix':self.skipTest('real symlink traversal test')
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'real/child').mkdir(parents=True);(root/'real/data').write_bytes(b'real');(root/'data').write_bytes(b'wrong');(root/'alias').symlink_to(root/'real/child',target_is_directory=True)
   self.assertEqual(g.sha(root/'alias/../data'),hashlib.sha256(b'real').hexdigest())
if __name__=='__main__':unittest.main()
