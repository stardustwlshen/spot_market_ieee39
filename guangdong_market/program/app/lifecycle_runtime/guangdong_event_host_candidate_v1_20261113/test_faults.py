from host import *
import unittest,tempfile,shutil
class Tests(unittest.TestCase):
 def test_switch_required(self):
  with tempfile.TemporaryDirectory()as d:
   with self.assertRaisesRegex(ValueError,'explicit'):Session(d)
 def test_unregistered_self_attested_handoff(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d)/'W45';p.mkdir();write(p/'HOST_STATE.json',{'window':45,'native_calls':0,'accepted':True})
   s=Session(d,research_fixed_curve_events=True)
   with self.assertRaisesRegex(AssertionError,'not registered'):s.handoff(45)
 def test_source_hash_mismatch(self):
  with tempfile.TemporaryDirectory()as d:
   s=Session(d,research_fixed_curve_events=True);s.registry=deepcopy(s.registry);s.registry['windows']['45']['summary_sha256']='0'*64
   with self.assertRaisesRegex(AssertionError,'source changed'):s.bundle(45)
 def test_no_overwrite_prior_run(self):
  s=Session(B/'two_window_run',research_fixed_curve_events=True)
  with self.assertRaisesRegex(AssertionError,'no overwrite'):s.prepare(45)
 def test_changed_ledger_not_self_authenticated(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d)/'W45';p.mkdir();original=read(B/'two_window_run/W45/LEDGER.json');original['cutoff_tick']+=1;original['ledger_sha256']=event.digest({k:v for k,v in original.items()if k!='ledger_sha256'});write(p/'LEDGER.json',original);write(p/'HOST_STATE.json',dict(ledger_file_sha256=sha(p/'LEDGER.json')))
   s=Session(d,research_fixed_curve_events=True)
   # A valid self-consistent hash is insufficient: the host has not registered it.
   with self.assertRaisesRegex(AssertionError,'not registered'):s._ledger(46)
 def test_native_entry_not_exposed(self):self.assertFalse(hasattr(Session,'solve'))
if __name__=='__main__':unittest.main(verbosity=2)
