"""Normal-run optional event callbacks for the one fixed installed v6 producer."""
from pathlib import Path
from copy import deepcopy
import importlib
from host import Session
from runtime_hooks import RuntimeHooks
from v6_host import installed,accepted_bundle,INSTALL,MANIFEST,sha,read,e

class V6Session(Session):
 def __init__(self,run,*,research_fixed_curve_events=False):
  from lifecycle_runtime.worker_proof_memo import session_creation_guard
  session_creation_guard()
  e.need(research_fixed_curve_events is True,'explicit research event switch');self.program=installed();self.run,self.context=self.program.context(run)
  self.builder=importlib.import_module('builder_user_v2');core=INSTALL/'rt/guangdong_rt_short_user_cli_v5_20261112';e.need(Path(self.builder.__file__).resolve()==core/'builder_user_v2.py','registered builder only')
  self.registry={'seed':str(self.run/'seed'),'case_identity':sha(self.run/'seed/case.json'),'run_id':self.context['run_id'],'source_pins':{str(INSTALL/'FROZEN_MANIFEST.json'):MANIFEST,str(Path(__file__).resolve()):sha(__file__),str(Path(__file__).with_name('runtime_hooks.py')):sha(Path(__file__).with_name('runtime_hooks.py')),str(self.run/'SHORT_CONTEXT.json'):sha(self.run/'SHORT_CONTEXT.json')}}
  self.registry['source_pins'].update(e.dual_adapter().pins())
 def verify_sources(self):
  from lifecycle_runtime.worker_proof_memo import session_access_guard
  session_access_guard(self)
  installed()
  for p,h in self.registry['source_pins'].items():e.need(sha(p)==h,'host/run source drift')
 def registered_boundary(self,w):
  self.verify_sources();e.need(type(w)is int and 1<=w<96,'registered previous window required');p,r,c,b,h=accepted_bundle(self.run,w-1);cp=p.current_carry(r,c,w);carry=read(cp)
  ledger=e.publish(b,None,expected_bundle_sha256=h,cutoff_tick=carry['cutoff_tick'],simulate=True)
  return cp,ledger
 def export_generated_pair(self,p,a,record):
  self.verify_sources();owned_loss=__import__('loss_callback').owned(p,a);e.need(a==p.parent/'actual' or owned_loss is not None,'generated pair layout');owned_loss.verify_registered() if owned_loss is not None else None;e.need(sha(p/'SUMMARY.json')==record['summary_sha256'],'host prepared source')
  # The normal runner must produce raw completed jobs; host checks all of them.
  n,response=self.program.engine.checked_pair(p,a);case=self.program.engine.loads((p/'derived_case.json').read_bytes());from gd_post_scuc_runtime import consumers;case_for=consumers.case_for;case=case_for(p,a,case);q=read(a/'SCUC/request.json.gz');u=read(a/'SCUC/response.json.gz');eq=read(a/'SCED/request.json.gz');proof=e.verify_pair(q,u,eq,response,n);e.dual_adapter().validate_record(a,proof)
  e.need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'400 invariant')
  b=dict(schema='gd.fixed.host_bundle.v1',mode='fixed_curve_research',run_id=self.registry['run_id'],case_identity=self.registry['case_identity'],available_at=read(p/'SUMMARY.json')['domain_start'],case=e.pack(case),boundary=e.pack(read(p/'BOUNDARY.json')),incoming_carry=e.pack(read(p/'CARRY.json')),SCUC_request=q,SCUC_response=u,SCED_request=eq,SCED_response=response,names=n,SCED_response_kind=proof['SCED_response_kind'],dual_certificate=proof['dual_certificate'],dual_certificate_sha256=proof['dual_certificate_sha256'],host_source_selection={'fixed_manifest':MANIFEST,'host_generated_summary':record['summary_sha256'],'new_actual_raw_checked':True})
  return b,e.digest(b)

def open_hooks(run,*,research_fixed_curve_events=False):return RuntimeHooks(V6Session(run,research_fixed_curve_events=research_fixed_curve_events))
