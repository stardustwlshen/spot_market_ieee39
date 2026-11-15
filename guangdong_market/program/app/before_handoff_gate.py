"""Fail-closed candidate quarantine. No solver, AC execution or safety release.
Python in-process capability boundary, not a sandbox against malicious Python.
"""
from pathlib import Path
import hashlib,json

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok,msg):
 if not ok: raise ValueError(msg)
class BlockedBeforeHandoff(ValueError): pass
class BeforeHandoffGate:
 def __init__(self,host,*,counterfactual_research=False,model_scope="legacy_ac_research"):
  need(model_scope in ("legacy_ac_research","active_power_dc"),"unsupported model scope")
  need(model_scope!="active_power_dc" or not counterfactual_research,"DC scope needs no counterfactual safety override")
  self.model_scope=model_scope;self.host=host;self.counterfactual=counterfactual_research is True
  self._record=None;self.last=None;self._handle=None
  files=[Path(__file__).resolve()]
  if model_scope!="active_power_dc":
   import candidate_safety
   files.append(Path(candidate_safety.__file__).resolve())
  for f in files:
   host.session.registry['source_pins'][str(f)]=sha(f)
 def _generated_after_verified_pair(self,p,a,proof):
  h=self.host
  need(h.mode=='native' and h.pending==p,'same native pending host required')
  need(proof.get('status')=='accepted_research_pair','complete verified pair required')
  need(str(p.resolve()) in h.hooks.prepared,'host did not prepare candidate')
  h.session.verify_sources()
  record=h.hooks.prepared[str(p.resolve())]
  for f,d in record['artifact_pins'].items():need(sha(f)==d,'prepared source drift')
  need(sha(record['carry_path'])==record['carry_sha256'],'incoming carry drift')
  files=[p/'SUMMARY.json',p/'names.json',a/'ACCEPTED_PAIR.json']+[a/s/f for s in ('SCUC','SCED') for f in ('request.json.gz','response.json.gz','JOB.json','COMPLETED.json')]
  self._record=(p,a,proof,h.window,h.cp,h.lid,{str(f):sha(f) for f in files})
  self._handle=object()
 def before_handoff(self,p,a,proof):
  h=self.host;r=self._record
  need(r is not None,'no same-host newly generated candidate; historical/JSON import forbidden')
  need(p==r[0] and a==r[1] and proof is r[2],'candidate identity substitution')
  need(h.window==r[3] and h.cp==r[4] and h.lid==r[5] and h.pending==p,'clock/carry changed')
  h.session.verify_sources()
  for f,d in r[6].items():need(sha(f)==d,'candidate raw source drift')
  if self.model_scope=='active_power_dc':
   self.last=dict(status='DC_VERIFIED_PAIR_SELECTED',model_scope='active_power_dc',ac_assessment='not_in_model_scope',reactive_power_modeled=False,branch_id=h.branch_id,window=h.window,source_pins=r[6],candidate_registered_in_memory=True,safety_release=False,publication_allowed=False,dispatch_authorized=False,formal=False,carry_advance_allowed=True,execution_allowed=True,execution_scope='model simulation only',basis='same-host verified numerical pair; retained DC/energy/thermal/storage/reserve/AGC/source checks')
   (p.parent/'BEFORE_HANDOFF.json').write_text(json.dumps(self.last,indent=2));self._record=None
   return dict(self.last)
  status='BLOCKED_MISSING_CANDIDATE_AC'
  safety=getattr(h,'candidate_safety',None)
  if safety is not None and getattr(h,'candidate_ac_once',False) and not hasattr(safety,'_candidate_ac_receipt'):
   safety.run_candidate_ac(self._handle,p.parent/'CANDIDATE_AC',execute_ac=True)
  if safety is not None and getattr(safety,'_candidate_ac_receipt',{}).get('handle') is self._handle:
   status=safety.candidate_status(self._handle)['status']
  self.last=dict(status=status,branch_id=h.branch_id,window=h.window,
   violations=getattr(safety,'_candidate_ac_receipt',{}).get('result',{}).get('violations',[]) if safety is not None else [],
   source_pins=r[6],candidate_registered_in_memory=True,ac_status=status,
   safety_release=False,publication_allowed=False,dispatch_authorized=False,formal=False,
   carry_advance_allowed=self.counterfactual,
   basis='explicit counterfactual simulation only' if self.counterfactual else 'quarantined before handoff')
  (p.parent/'BEFORE_HANDOFF.json').write_text(json.dumps(self.last,indent=2))
  # Preserve first unsafe BEFORE_HANDOFF before optional owned correction.
  if status=='BLOCKED_SYSTEM_SAFETY' and getattr(h,'candidate_loss_once',False):
   from loss_callback import LossCorrection
   correction=LossCorrection(safety,self._handle,explicit_once=True)
   correction.prepare()
   correction.execute(execute_native=True,execute_ac=True)
  if not self.counterfactual:raise BlockedBeforeHandoff(status+'; no handoff/carry advance')
  self._record=None
  return dict(self.last)
 def resolve(self,handle):
  need(handle is self._handle and handle is not None,'private current candidate handle required')
  r=self._record;h=self.host
  need(r is not None and h.pending==r[0] and h.window==r[3] and h.cp==r[4] and h.lid==r[5],'stale candidate/host advanced')
  h.session.verify_sources()
  for f,d in r[6].items():need(sha(f)==d,'candidate source drift')
  return r[0],r[1]
 def inspect_historical_ac(self,source,result,*,expected_source):
  """Read-only fixture only; never creates a candidate or grants permission."""
  need(source==expected_source,'historical AC identity mismatch')
  return dict(status='historical_unsafe' if not result.get('ac_snapshot_safe') else 'historical_point_only',
   violations=result.get('violations',[]),live_candidate=False,carry_advance_allowed=False,
   safety_release=False,dispatch_authorized=False)
