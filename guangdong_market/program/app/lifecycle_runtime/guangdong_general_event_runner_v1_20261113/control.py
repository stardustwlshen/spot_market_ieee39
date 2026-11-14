"""ResearchInstructions on a host-created accepted-plan phase capability."""
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
import sys
R=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(R/'guangdong_shutdown_instruction_candidate_v1_20261113'))
from instructions import ResearchInstructions,e,read,sha
class AcceptedInstructions(ResearchInstructions):
 def __init__(self,hooks,grant,ledger_id,issue_tick):
  e.need(grant is hooks._phase_grant,'host acceptance capability required')
  self.hooks=hooks;self.issue_tick=issue_tick;self._commands={};self._clock=issue_tick
  self.host=SimpleNamespace(accepted=hooks.accepted_plans,hooks=hooks,lid=ledger_id)
 def _live_carry(self):
  # Separate accepted-control clock. Never rewrite/read a forged model carry cutoff.
  self.hooks.verify_phase();return {'cutoff_tick':self.issue_tick}
 def _load(self,w):
  self.hooks.verify_phase();b,h=self.hooks.accepted_plans[w];return e.export_plan(b,expected_bundle_sha256=h)
