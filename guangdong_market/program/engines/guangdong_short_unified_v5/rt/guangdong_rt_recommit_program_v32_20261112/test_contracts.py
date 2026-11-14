import unittest,tempfile,json
from pathlib import Path
import program
from evidence_io import save,load
class Contracts(unittest.TestCase):
 def test_unknown_native_rejected(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d)
   with self.assertRaisesRegex(ValueError,'unknown/incomplete'):program.reuse_native(p,{},p/'worker.py')
 def test_compressed_repeat_checks_content(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d)/'x.gz';r=program.save_or_compare(p,{'a':1});self.assertEqual(r,program.save_or_compare(p,{'a':1}))
   with self.assertRaisesRegex(ValueError,'differs'):program.save_or_compare(p,{'a':2})
 def test_unknown_partial_artifact_rejected(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d)/'x.gz';save(p,{'a':1})
   with self.assertRaisesRegex(ValueError,'partial'):program.save_or_compare(p,{'a':1})
 def test_counter_only_lp_change(self):
  src=Path('legacy_v1/vendor/ieee39-cloud/repo/engines/guangdong_v3/guangdong_pipeline_20261112/precision_recovery_v3/lp_worker_1e10.py').read_text()
  expected=src.replace('def solve(data):','OPTIMIZE_CALLS=0\n\ndef solve(data):\n    global OPTIMIZE_CALLS').replace('    run_status=h.run()','    OPTIMIZE_CALLS+=1\n    run_status=h.run()');self.assertEqual(Path('lp_worker.py').read_text(),expected)
if __name__=='__main__':unittest.main()
