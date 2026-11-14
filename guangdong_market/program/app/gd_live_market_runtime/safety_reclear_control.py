"""Same-host safety gate -> confirmed future tightening -> runner -> safety callback."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'lifecycle_runtime/guangdong_safety_session_v1_20261113'))
from safety_session import SafetySession
from tightening import TighteningAdapter
class SafetyReclearControl:
 def __init__(self,host,*,safety=None):
  if safety is not None and safety.host is not host:raise ValueError('same live safety session required')
  self.host=host;self.safety=SafetySession(host)if safety is None else safety;self.adapter=TighteningAdapter(host);self.events=[];self.phase='uninspected';self.source_window=None
 def inspect_trigger(self,w):
  if self.phase!='uninspected':raise ValueError('trigger already inspected')
  z=self.safety.inspect(w)
  if not z['status'].startswith('BLOCKED_')or z['safety_release']:raise ValueError('explicit unresolved safety source required')
  self.events.append({'event':'safety_blocked_trigger','window':w,'status':z['status'],'branch_id':self.host.branch_id});self.phase='awaiting_declaration';self.source_window=w;return z
 def review(self,declaration):
  if self.phase!='awaiting_declaration':raise ValueError('inspect blocked source first')
  if declaration['base_source_window']!=self.source_window:raise ValueError('declaration must bind triggering accepted window')
  return self.adapter.registration_plan(declaration)
 def confirm_prepare(self,declaration,*,confirmed=False,expected_confirmation_digest=None):
  self.review(declaration)
  r=self.adapter.register_declaration(declaration,confirmed=confirmed,expected_confirmation_digest=expected_confirmation_digest)
  self.events.append({'event':'declaration_registered','declaration':r['declaration_path']})
  z=self.adapter.prepare(r['declaration_path']);self.phase='prepared_unsolved';self.events.append({'event':'complete_model_prepared','prepared':z['prepared']});return dict(prepared=z,plan=self.adapter.plan())
 def execute(self,*,execute_native=False,replay_runner=None):
  if self.phase!='prepared_unsolved':raise ValueError('fresh confirmed prepared candidate required')
  w=self.host.window
  if replay_runner is None:
   if execute_native is not True:raise ValueError('separate native authorization required')
   self.phase='execution_started_no_retry';result=self.host.run(execute_native=True);mode='real_native_host_run'
  else:
   if execute_native:raise ValueError('read-only replay cannot authorize native')
   if replay_runner.host is not self.host:raise ValueError('replay host mismatch')
   self.phase='execution_started_no_retry';result=replay_runner.replay_existing();mode='verified_existing_raw_replay_no_native'
  self.events.append({'event':'runner_returned','mode':mode,'window':w})
  state=self.safety.inspect(w);self.phase='inspected_after_runner';self.events.append({'event':'post_runner_safety','window':w,'status':state['status']})
  return dict(result=result,safety=state,events=self.events,mode=mode,safety_release=False,prior_failure_repaired=False)
