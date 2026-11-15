"""One new loss-derived near/far candidate, same live host, no historical adoption."""
from pathlib import Path
from copy import deepcopy
import json,sys,hashlib,importlib.util
from loss_profile import StageLossOverlay,read,sha,digest,need
class Candidate:
 def __init__(self,*,host,program_root,stage_api_root,profile_path,output):
  self.h=host;self.root=Path(program_root).resolve();self.api=Path(stage_api_root).resolve();self.profile_path=Path(profile_path).resolve();self.profile=read(self.profile_path);self.profile_sha=sha(self.profile_path);self.out=Path(output).resolve()
  need(not self.out.exists(),'fresh candidate output only');need(not hasattr(host,'_curve_loss_iteration'),'one candidate iteration only');need(self.profile['iteration']==1,'only first iteration')
  host.session.verify_sources();self.before=(deepcopy(host.hooks.carries),deepcopy(host.hooks.ledgers),host.window,str(host.cp),host.lid);self.state='reserved';host._curve_loss_iteration=self;self.out.mkdir(parents=True)
 def overlay(self):
  self.verify();return StageLossOverlay(program_root=self.root,profile=self.profile)
 def verify(self):
  h=self.h;h.session.verify_sources();need(h._curve_loss_iteration is self and sha(self.profile_path)==self.profile_sha,'candidate/profile drift');need(digest(self.profile)==digest(read(self.profile_path)),'profile mutated');need(self.before==(h.hooks.carries,h.hooks.ledgers,h.window,str(h.cp),h.lid),'actual state advanced')
 def prepare(self):
  need(self.state=='reserved','prepare once');self.verify();self.state='preparing';h=self.h;c=read(h.cp);sys.path.insert(0,str(self.api));from stage_api import prepare_registered_stage
  import thermal_physical
  original=thermal_physical.build_free_scuc
  def agc(**kw):
   from agc_near import apply_near
   apply_near(h.agc,kw,c);return original(**kw)
  thermal_physical.build_free_scuc=agc;p=self.out/'near/prepared';p.parent.mkdir();stage=dict(block='near',count=24,start_tick=c['delivery_tick'],near_start_tick=c['delivery_tick'],anchor_ticks=3,initial_kind='issued_prefix_projected_boundary',executed_end_exclusive=c['executed_end_exclusive'],issued_end_exclusive=c['issued_end_exclusive'],carry_sha256=sha(h.cp))
  try:result=prepare_registered_stage(program_root=self.root,seed=h.session.registry['seed'],carry=h.cp,stage=stage,agc_source={'kind':'registered_host'},output=p,hooks=h.hooks,ledger_id=h.lid,loss_overlay=self.overlay())
  finally:thermal_physical.build_free_scuc=original
  self.p=p;record=h.hooks.prepared[str(p.resolve())];need(str(p/'LOSS_OVERLAY.json')in record['artifact_pins']and str(p/'LOSS_PROFILE.json')in record['artifact_pins'],'loss files not registered');self.state='prepared';self.verify()
  z=dict(status='new_loss_near_registered',native_calls=0,NR_calls=0,profile_sha256=self.profile_sha,prepared=str(p),record=record,far='pending_new_near',execution=False,publication=False,PRICE=False)
  (self.out/'PREPARE_RESULT.json').write_text(json.dumps(z,indent=2));return z
 def execute(self,*,execute_native=False):
  need(execute_native is True,'explicit four-candidate-native approval required');need(self.state=='prepared','candidate prepare required/no retry');self.verify();self.state='started_unknown';h=self.h
  (self.out/'STARTED.json').write_text(json.dumps(dict(max_native=4,iteration=1,no_retry=True,PRICE=False)))
  from native_runtime import execute_pair
  h.pending=self.p;near=execute_pair(h.hooks,self.p,execute_native=True)
  # Existing continue_window authenticates this NEW accepted near, then derives
  # its own new predicted SOC/P/ages before constructing and solving far24.
  sys.path.insert(0,str(self.api/'price_api'));from window_api_corrected import continue_window
  far=continue_window(program_root=self.root,host=h,near_prepared=self.p,near_actual=self.p.parent/'actual',research_agc_input=self.root/'app/gd_hourly_agc_runtime/RESEARCH_INPUT.json',output=self.out/'far_window',mode='research_native',execute_far=True,loss_overlay_factory=self.overlay)
  self.state='four_native_completed';self.verify()
  # Complete new curve AC, not source AC reuse. PRICE never auto-released here.
  spec=importlib.util.spec_from_file_location('_loss_candidate_AC',Path(__file__).resolve().parent.parent/'safety/curve.py');ac=importlib.util.module_from_spec(spec);spec.loader.exec_module(ac)
  try:gate=ac.validate_curve(program_root=self.root,near_prepared=self.p,near_actual=self.p.parent/'actual',far_window=self.out/'far_window',output=self.out/'AC48')
  except Exception as ex:gate=dict(status='blocked_unsafe',price_allowed=False,unknown=True,error=str(ex))
  z=dict(status='candidate_static_safe_PRICE_pending'if gate.get('price_allowed')else'blocked_unsafe',native_calls=4,near=near,far=far,safety=gate,full_window_AC=gate.get('coverage')==48,PRICE=False,execution=False,publication=False,profile_path=str(self.profile_path))
  (self.out/'MATH_RESULT.json').write_text(json.dumps(z,indent=2));return z
