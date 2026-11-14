"""New reference solve from a typed, already-selected historical H24 fact.
Never creates LivePhase, reissues AGC, or treats a saved model as acceptance.
"""
from pathlib import Path
from datetime import timedelta
from post_reference import PostReferenceSession
from reference_session import read,sha,need,put,B,materialize,prepare_boundary,PROFILE
from post_reference import mapped_receipt
import subprocess,sys
from gd_resume_runtime.reference_abandonment import validate_abandoned_reference

class HistoricalPostReferenceSession(PostReferenceSession):
 def __init__(self,host,output,*,source_output,research_reference_revision=False):
  self.historical_source=Path(source_output).resolve()
  self.abandoned=validate_abandoned_reference(self.historical_source)
  super().__init__(host,output,research_reference_revision=research_reference_revision)
  self.pins.update(self.abandoned['pins'])
  f=Path(__file__).resolve();self.pins[str(f)]=sha(f)
  host.session.registry['source_pins'].update(self.pins)
 def _record_pins(self,record):
  return {**record.guards,str(record.evidence):record.evidence_sha256}
 def _post_source(self,*,at_preparation=True):
  from gd_post_scuc_runtime.controller import Controller
  from gd_resume_runtime.historical_hour import HistoricalSelected,verified_history
  h=self.host;c=read(h.cp);ctrl=getattr(h.hooks,'post_scuc',None)
  need(validate_abandoned_reference(self.historical_source)==self.abandoned,'reference cancellation fence changed')
  need(type(ctrl)is Controller and ctrl.h is h,'same-host typed Controller required')
  need(h.mode=='native' and h.window>=95 and c['cutoff_tick']>=285,'historical reference requires accepted W94')
  need(c['cutoff']==(h.agc.origin+timedelta(minutes=5*c['cutoff_tick'])).isoformat(),'carry tick/timestamp mismatch')
  if at_preparation:need(h.window==95 and h.pending is None and c['cutoff_tick']==285,'historical reference prepare only before q95/W95')
  need(24 in ctrl.accepted and 24 not in h.agc.issued,'already selected H24 required; no lifecycle forecast')
  record=ctrl.accepted[24]
  need(type(record)is HistoricalSelected and record.controller is ctrl and record.hour==24,'typed historical H24 required; no fabricated LivePhase')
  z=verified_history(record)
  need(z['branch_id']==h.branch_id and z['historical_selected_fact']is True,'historical H24 branch mismatch')
  prepared,actual=map(lambda p:Path(p).resolve(),h.accepted_source(94))
  old=self.historical_source/'pipeline/dispatch/W94'
  need(prepared==(old/'prepared').resolve() and actual==(old/'actual').resolve(),'historical reference W94 occurrence mismatch')
  f=actual/'ACCEPTED_PAIR.json';pair=read(f)
  need(record.guards.get(str(f))==sha(f),'historical accepted pair not bound')
  need(pair['status']=='accepted_research_pair' and pair['post_SCUC_phase']==z['phase_proof'] and pair['SCED_audit']['KKT']['passed']is True,'historical SCED certificate missing/mismatched')
  need(z['effective_case']['contract']['cutoff']==(h.agc.origin+timedelta(minutes=1410)).isoformat(),'historical W94 model clock mismatch')
  if at_preparation:
   before=read(old/'EXACT_CARRY.json')
   for k in ('thermal','thermal_age_h','storage','notice_values','cutoff_tick','delivery_tick'):
    need(before[k]==c[k],'historical reference physical boundary mismatch: '+k)
  if self.post_record is not None:need(record is self.post_record,'historical H24 replaced')
  self.post_record=record
  return z,record,f,c
 def _prepare_same_math(self):
  need(not self.prepared,'single reference preparation');self.verify();h=self.host;c=read(h.cp)
  z,record,accepted_file,c=self._post_source()
  case,values,da=h.agc.da_plan();receipt=mapped_receipt(z['receipt'],origin=h.agc.origin,case_sha256=h.agc.seed_sha)
  self.data_as_of=receipt['context']['as_of'];self.stage_available_at=c['cutoff']
  origin=h.agc.origin.isoformat();boundary=prepare_boundary(case,values,receipt,origin=origin,case_sha256=h.agc.seed_sha)
  need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'400 reserve unchanged')
  self.pins.update(da['source_pins']);self.pins.update(self._record_pins(record));self.pins[str(accepted_file)]=sha(accepted_file)
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
 def prepare(self):
  result=self._prepare_same_math()
  old=self.historical_source/'pipeline/POST_D1_REFERENCE'
  # Reconstructed from the same DA and typed receipt, not trusted by hash alone.
  for name in ('prepared/REFERENCE_LP.json','INTEGER_SOURCE.json','prepared/names.json','prepared/agc.json'):
   need(read(self.out/name)==read(old/name),'historical reference math differs: '+name)
   self.pins[str(old/name)]=sha(old/name)
  path=self.out/'HISTORICAL_REFERENCE_INPUT.json'
  put(path,dict(schema='gd.reference.historical_selected_input.v1',source_output=str(self.historical_source),fence=self.abandoned['fence'],H24_reissued=False,new_LivePhase=False,prior_native_calls=0,new_reference_not_yet_solved=True,original_prepared_math_equal=True,safety_release=False))
  self.pins[str(path)]=sha(path);self.verify()
  return result
