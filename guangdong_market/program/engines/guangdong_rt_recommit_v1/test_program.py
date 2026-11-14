import unittest,tempfile,json,subprocess,sys
from pathlib import Path
import program as p
class Tests(unittest.TestCase):
 def test_dependencies(self):p.bind_dependencies(p.B/'vendor')
 def test_duplicate_seed(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaisesRegex(ValueError,'fresh'):p.seed('/absent',d,p.B/'vendor')
 def test_source_mismatch(self):
  with tempfile.TemporaryDirectory() as d:
   p.write(Path(d)/'CONTEXT.json',{'code_pins':{}})
   with self.assertRaisesRegex(ValueError,'revision'):p.context(d)
 def test_unknown_native_no_retry(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaisesRegex(ValueError,'unknown'):p.native_once('/absent',d,'/absent','SCUC')
 def test_prepared_identity_mismatch(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);(r/'W0/prepared').mkdir(parents=True);p.write(r/'CONTEXT.json',{})
   p.write(r/'W0/prepared/READY.json',{'context_sha256':'wrong'})
   with self.assertRaisesRegex(ValueError,'context mismatch'):p.verify_prepared(r,0)
 def test_duplicate_output(self):
  with tempfile.TemporaryDirectory() as d:
   q=Path(d)/'receipt.json';p.write(q,{})
   with self.assertRaises(FileExistsError):p.write(q,{})
 def test_formal_rejected(self):
  a=subprocess.run([sys.executable,'-B',str(p.B/'program.py'),'verify','--mode','formal'],capture_output=True,text=True)
  self.assertNotEqual(a.returncode,0);self.assertIn('missing AGC/AC',a.stderr)
 def test_cwd_independence(self):
  a=subprocess.run([sys.executable,'-B',str(p.B/'program.py'),'verify'],cwd='/tmp',capture_output=True,text=True)
  self.assertEqual(a.returncode,0,a.stderr);self.assertEqual(json.loads(a.stdout)['native_calls'],0)
if __name__=='__main__':unittest.main()
