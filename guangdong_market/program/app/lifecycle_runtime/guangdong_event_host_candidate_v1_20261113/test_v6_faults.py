import unittest,tempfile
from unittest.mock import patch
from pathlib import Path
import v6_host as h
from v6_runtime import open_hooks
RUN=h.R/'guangdong_unified_batch_candidate_v1_20261113/actual_first_hour_v1/rt'
class T(unittest.TestCase):
 def test_unknown_version(self):
  with patch.object(h,'MANIFEST','0'*64),self.assertRaisesRegex(ValueError,'version/manifest'):h.installed()
 def test_unaccepted_source(self):
  p=h.installed()
  with patch.object(p,'preflight',return_value={'status':'ready'}),self.assertRaisesRegex(ValueError,'accepted'):h.accepted_bundle(RUN,1)
 def test_switch_required(self):
  with self.assertRaisesRegex(ValueError,'switch'):open_hooks(RUN)
 def test_unregistered_carry(self):
  hooks=open_hooks(RUN,research_fixed_curve_events=True)
  with self.assertRaisesRegex(AssertionError,'unregistered physical'):hooks.build(RUN/'handoff/W2/CARRY.json',Path(tempfile.gettempdir())/'must-not-build-event',ledger_id='self-attested')
 def test_handoff_requires_host_generated_input(self):
  hooks=open_hooks(RUN,research_fixed_curve_events=True)
  with self.assertRaises(KeyError):hooks.handoff_accepted(RUN/'W2/prepared',RUN/'W2/actual','unused.json',simulate_no_deviation=True)
if __name__=='__main__':unittest.main(verbosity=2)
