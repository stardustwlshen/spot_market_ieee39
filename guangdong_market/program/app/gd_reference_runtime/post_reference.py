"""Post-SCUC H24 reference bridge. Separate from classic pre-W94 reference.
No JSON restoration. Defaults zero native; model and transport inherited unchanged.
"""
from reference_session import (ReferenceSession, ReferenceOverlay, _KEY,
 B,R,APP,read,sha,need,put,module,materialize,prepare_boundary,thermal_overlay,PROFILE)
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
import subprocess,sys

def mapped_receipt(receipt, *, origin, case_sha256):
 """Pure schema mapping, NOT an authentication or publication operation."""
 start=origin+timedelta(hours=24);cut=start-timedelta(minutes=30)
 need(receipt['hour_start']==start.isoformat() and receipt['hour_end']==(start+timedelta(hours=1)).isoformat(),'H24 interval mismatch')
 need(receipt['published_at']==cut.isoformat() and receipt['internal_completed_at']==cut.isoformat(),'internal phase data_as_of mismatch')
 need(receipt['allocator']['unmet_requirement_mw']==0,'H24 unmet demand')
 return dict(context=dict(seed_case_sha256=case_sha256,as_of=cut.isoformat(),hour_start=start.isoformat()),result=deepcopy(receipt['allocator']))

class PostReferenceSession(ReferenceSession):
 def __init__(self,host,output,*,research_reference_revision=False):
  super().__init__(host,output,research_reference_revision=research_reference_revision)
  self.pins[str(Path(__file__).resolve())]=sha(__file__)
  host.session.registry['source_pins'].update(self.pins)
  self.post_record=None
 def _post_source(self, *, at_preparation=True):
  from gd_post_scuc_runtime.controller import Controller
  from gd_post_scuc_runtime.live_hook import LivePhase
  from gd_post_scuc_runtime import consumers
  h=self.host;c=read(h.cp);ctrl=getattr(h.hooks,'post_scuc',None)
  need(type(ctrl)is Controller and ctrl.h is h,'same-host typed post Controller required')
  need(h.window>=95 and c['cutoff_tick']>=285,'post reference cannot precede accepted W94')
  need(c['cutoff']==(h.agc.origin+timedelta(minutes=5*c['cutoff_tick'])).isoformat(),'carry tick/timestamp mismatch')
  if at_preparation:need(h.window==95 and h.pending is None and c['cutoff_tick']==285,'prepare only after accepted W94, before W95/q95')
  need(24 in ctrl.accepted and 24 not in h.agc.issued,'real post H24 required, not lifecycle forecast/receipt')
  record=ctrl.accepted[24];phase=record['phase'];need(type(phase)is LivePhase and phase.h is h,'same live phase required')
  prepared,actual=h.accepted_source(94);prepared=Path(prepared).resolve();actual=Path(actual).resolve()
  need(phase.p==prepared and consumers.lookup(prepared,actual)is record,'unregistered W94 phase')
  z=consumers.verified_state(record);need(z['branch_id']==h.branch_id,'phase branch mismatch')
  f=actual/'ACCEPTED_PAIR.json';need(f.exists() and read(f).get('post_SCUC_phase')==z['phase_proof'],'SCED not yet accepted')
  need(z['SCED_audit']['KKT']['passed'],'SCED KKT not accepted')
  need(phase.case['contract']['cutoff']==(h.agc.origin+timedelta(minutes=1410)).isoformat(),'W94 model clock mismatch')
  if self.post_record is not None:need(record is self.post_record,'registered H24 replaced')
  self.post_record=record
  return z,record,f,c
 def verify(self):
  super().verify()
  if getattr(self,'post_record',None)is not None:self._post_source(at_preparation=False)
 def prepare(self):
  need(not self.prepared,'single reference preparation');self.verify();h=self.host;c=read(h.cp)
  z,record,accepted_file,c=self._post_source()
  case,values,da=h.agc.da_plan();receipt=mapped_receipt(z['receipt'],origin=h.agc.origin,case_sha256=h.agc.seed_sha)
  self.data_as_of=receipt['context']['as_of'];self.stage_available_at=c['cutoff']
  origin=h.agc.origin.isoformat();boundary=prepare_boundary(case,values,receipt,origin=origin,case_sha256=h.agc.seed_sha)
  need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'400 reserve unchanged')
  self.pins.update(da['source_pins']);self.pins.update(record['pins']);self.pins[str(accepted_file)]=sha(accepted_file)
  da_root=Path(h.agc.context['selected_DA']);art=read(da_root/'ARTIFACTS.json');st=art['stages']['da_scuc']
  for artifact in st.values():
   if isinstance(artifact,dict)and'path'in artifact:need(sha(da_root/artifact['path'])==artifact['sha256'],'DA SCUC source drift');self.pins[str(da_root/artifact['path'])]=artifact['sha256']
  scuc=dict(zip(read(da_root/st['full_names']['path'])['variables'],read(da_root/st['full_primal']['path'])))
  self.da_root=da_root;self.values=values;self.boundary=boundary;self.units=list(case['energy_inputs']['thermal'])
  put(self.out/'POST_REFERENCE_SOURCE.json',dict(schema='gd.post_reference.source.v1',branch_id=h.branch_id,source_window=94,data_as_of=self.data_as_of,stage_available_at=self.stage_available_at,receipt=z['receipt'],SCED_accepted_path=str(accepted_file),SCED_accepted_sha256=sha(accepted_file),research_only=True,formal=False,safety_release=False))
  materialize(self.out,h.session.run,values,scuc,receipt,boundary,self.pins)
  p=subprocess.run([sys.executable,str(B/'build_process.py'),str(self.out)],capture_output=True,text=True)
  (self.out/'BUILD.log').write_text(p.stdout+p.stderr);need(p.returncode==0,'reference pure build failed; preserve BUILD.log')
  self.pins.update({str(f):sha(f)for f in self.out.rglob('*')if f.is_file()});self.prepared=True;self.verify()
  return dict(profile=PROFILE,branch_id=self.branch_id,prepared=str(self.out/'prepared'),reference_native_budget=1,seconds=3600,threads=1,mip_gap=None,solver_time_limit_scope='each_native_solve',process_grace_seconds=300,default_native_calls=0,formal=False,safety_release=False)
 def execute(self,*,execute_native=False):
  self.verify();need(self.prepared,'prepare required')
  if not execute_native:return dict(native_calls=0,prepared=str(self.out/'prepared'),requires_explicit_execution=True)
  need(execute_native is True and not self.attempted,'single explicit native occurrence; retries forbidden')
  need(self.host.window==95 and self.host.mode=='native','live native host after W94 required; read-only adoption not execution authority')
  self.attempted=True;put(self.out/'ATTEMPT.json',dict(branch_id=self.branch_id,reference_budget=1,automatic_retry=False))
  worker=B/'precision_worker.py';runner=R/'guangdong_rt_short_physical_v1_20261112/run_actual39.py'
  for f in (worker,runner):self.pins[str(f)]=sha(f)
  w=module('_reference_native_transport',runner);q=read(self.out/'prepared/REFERENCE_LP.json');n=read(self.out/'prepared/names.json');actual=self.out/'actual';actual.mkdir()
  request,response=w.native(actual,'SCED',q,worker,3600.);need({k:v for k,v in request.items()if k!='deadline'}=={k:v for k,v in q.items()if k!='deadline'},'worker altered registered reference model')
  derived=None
  try:review=w.checks(q,response,n,False)
  except Exception as ex:
   put(actual/'RAW_KKT_FAILURE.json',dict(reason=str(ex),native_response_unchanged=True));sys.path.insert(0,str(APP/'lifecycle_runtime/guangdong_network_dual_certificate_v1_20261113'));from network_certificate import certify
   certificate=certify(q,n,response);derived=certificate['derived_response'];need(derived is not None,'no valid separately derived certificate');review=w.checks(q,derived,n,False);put(actual/'DUAL_CERTIFICATE.json',certificate['certificate']);put(actual/'derived_response.json',derived)
  # Native raw remains intact. Capability is minted only from this session call.
  rv=dict(zip(n['variables'],(derived or response)['primal']));physical_path=self.da_root/'da_sced/physical_solution.json';physical=read(physical_path)['physical_cells'];agc=read(self.out/'prepared/agc.json');overlay=thermal_overlay(physical,rv,agc,self.units);target=self.out/'THERMAL_REFERENCE_OVERLAY.json';put(target,dict(physical_cells=overlay))
  self.pins[str(physical_path)]=sha(physical_path);self.pins.update({str(f):sha(f)for f in actual.rglob('*')if f.is_file()});self.pins[str(target)]=sha(target)
  self.accepted=dict(profile=PROFILE,branch_id=self.branch_id,path=str(target),sha256=sha(target),original_DA_physical_path=str(physical_path),original_DA_physical_sha256=sha(physical_path),source_pins=dict(self.pins),D0_unchanged=True,outside_reference_interval_unchanged=True,interval=[288,335],input_available_at=self.stage_available_at,data_as_of=self.data_as_of,stage_available_at=self.stage_available_at,post_SCUC_reference=True,native_calls=1,raw_KKT_passed=derived is None,derived_certificate=None if derived is None else str(actual/'DUAL_CERTIFICATE.json'),review=review,formal_DA98=False,safety_release=False,publication=False)
  put(self.out/'ACCEPTED_REFERENCE.json',self.accepted);self.pins[str(self.out/'ACCEPTED_REFERENCE.json')]=sha(self.out/'ACCEPTED_REFERENCE.json');self.verify();return ReferenceOverlay(_KEY,self)
 def overlay(self):
  self.verify();need(self.accepted is not None,'no accepted reference');return ReferenceOverlay(_KEY,self)