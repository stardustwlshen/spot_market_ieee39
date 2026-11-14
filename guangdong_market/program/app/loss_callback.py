"""One owned unissued-candidate correction. No restore/import/retry or auto-release.
This module is a small installation patch, not enabled in any frozen application.
"""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timedelta
import hashlib,json,gzip,math
from loss_feedback import build_unissued_feedback,digest,need
read=lambda p:json.loads(gzip.decompress(Path(p).read_bytes())if str(p).endswith('.gz')else Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2,allow_nan=False)
_OWNED={}
def owned(p,a):return _OWNED.get((str(Path(p).resolve()),str(Path(a).resolve())))
class LossCorrection:
 def __init__(self,safety,handle,*,explicit_once=False):
  need(explicit_once is True,'explicit one SCED plus one AC correction budget required')
  self.s=safety;self.h=safety.host;self.g=self.h.before_handoff_gate;self.token=handle
  self.p,self.old=self.g.resolve(handle);self.identity=(id(self.h),self.h.branch_id,self.h.window,str(self.h.cp),self.h.lid)
  need(not hasattr(self.h,'_loss_correction'),'correction already reserved/attempted; no retry')
  r=safety._candidate_ac_receipt
  need(r['handle']is handle and 'result'in r and not r.get('attached'),'owned unissued completed AC only')
  need(r['result']['ac_converged'],'failed/unknown/nonconverged AC cannot supply loss')
  need(r['payload']['loss_proxy_mw']==0,'only first correction from zero-loss model supported')
  need(not getattr(self.h,'source_kind',None) and not self.h.session.compatibility,'historical source forbidden')
  self.receipt=r;self.receipt_digest=digest({k:r[k]for k in ('result','mapping','payload','source')});self.pins=deepcopy(self.g._record[6]);self.issued=digest(self.h.agc.issued);self.state='reserved';self.h._loss_correction=self
  self.root=self.p.parent/'loss_correction_1';self.actual=self.root/'actual';self.audit=None
  for f in (Path(__file__).resolve(),Path(__file__).with_name('loss_feedback.py'),Path(__file__).with_name('loss_consumers.py')):self.h.session.registry['source_pins'][str(f)]=sha(f)
 def verify(self):
  self.h.session.verify_sources();need(self.identity==(id(self.h),self.h.branch_id,self.h.window,str(self.h.cp),self.h.lid),'host/carry advanced')
  need(self.h.pending==self.p and self.h._loss_correction is self,'not current correction')
  need(digest(self.h.agc.issued)==self.issued,'published AGC prefix changed')
  for f,v in self.pins.items():need(sha(f)==v,'original candidate drift')
  r=self.s._candidate_ac_receipt
  need(r is self.receipt and r['handle']is self.token,'AC receipt replaced/stale')
  need(digest({k:r[k]for k in ('result','mapping','payload','source')})==self.receipt_digest,'owned AC receipt content changed')
  for x in r['source']['sources']:need(sha(x['path'])==x['sha256'],'AC input source drift')
 def prepare(self):
  self.verify();need(self.state=='reserved','prepare once')
  from gd_post_scuc_runtime import consumers
  h=self.h;program=h.session.program;n,response=program.engine.checked_pair(self.p,self.old)
  q=read(self.old/'SCED/request.json.gz');uq=read(self.old/'SCUC/request.json.gz');u=read(self.old/'SCUC/response.json.gz')
  from v6_host import e
  self.base_proof=e.verify_pair(uq,u,q,response,n) # retain prior post phase/full raw acceptance
  case=consumers.case_for(self.p,self.old,program.engine.loads((self.p/'derived_case.json').read_bytes()))
  from event_path import pack
  self.source_case_digest=digest(pack(case));self.semantics_digest=digest(consumers.semantics_for(self.p,self.old))
  agc=consumers.effective_awards(self.p,self.old,read(self.p/'agc.json'))
  # Existing pure feedback helper uses JSON tick keys, not instantaneous P as rating.
  agc={g:{k:({str(t):v for t,v in x.items()}if k in ('up_mw','down_mw')else x)for k,x in a.items()}for g,a in agc.items()}
  c=read(self.h.cp);frontier=datetime.fromisoformat(c['origin'])+timedelta(minutes=5*c.get('issued_end_exclusive',c['delivery_tick']))
  m=self.receipt['mapping'];ac=self.receipt['result'];identity=dict(branch_id=h.branch_id,seed_sha256=h.session.registry['case_identity'],window=h.window,point=m['start'],SCED_request_sha256=sha(self.old/'SCED/request.json.gz'),SCED_response_sha256=sha(self.old/'SCED/response.json.gz'))
  ctx=dict(identity,issued_end_at=frontier.isoformat(),stage='accepted_pair_before_handoff',template_digest=digest(q),names_digest=digest(n),local_tick=0,AC_to_model_bus={str(i):str(i+1)for i in range(39)})
  new,feedback=build_unissued_feedback(q,n,m,ac,case,agc,ctx,identity,positive_row='reserves[1]',negative_row='reserves[2]')
  changed={n['constraints'].index(x['name'])for x in feedback['changed_rows']}
  need(len(changed)==41 and all(x==q['rows'][i]for i,x in enumerate(new['rows'])if i not in changed),'non-loss full-domain rows changed')
  need(new['variables']==q['variables']and new['objective']==q['objective'],'full-domain bounds/objective changed')
  for i,v in enumerate(uq['variables']):
   if v['kind']!='C':need(new['variables'][i]['lb']==new['variables'][i]['ub']==round(u['primal'][i]),'original integer not fixed')
  # Future normal worker uses exactly the registered precision request.
  if hasattr(h,'sced_precision_policy'):
   from lifecycle_runtime.gd_sced_precision_runtime.policy import request
   h.sced_precision_policy.verify();new=request(new)
  self.q=new;self.n=n;self.uq=uq;self.u=u;self.case=case;self.feedback=feedback;self.context=ctx
  need(not self.root.exists(),'existing correction occurrence: inspect never retry');self.actual.mkdir(parents=True)
  # Original SCUC reference is explicitly reused, not copied/new SCUC occurrence.
  (self.actual/'SCUC').symlink_to((self.old/'SCUC').resolve(),target_is_directory=True)
  write(self.root/'CONTEXT.json',dict(context=ctx,feedback=feedback,new_SCUC_calls=0,max_new_SCED=1,max_new_AC=1,original_SCUC=str(self.old/'SCUC'),PRICE_pending=True))
  self.state='prepared';return deepcopy(feedback)
 def execute(self,*,execute_native=False,execute_ac=False):
  need(execute_native is True and execute_ac is True,'separate explicit paired SCED/AC budget required')
  self.verify();need(self.state=='prepared','not prepared or already attempted')
  self.state='started_unknown';write(self.root/'STARTED.json',{'SCED':1,'AC':1,'no_retry':True})
  program=self.h.session.program
  program.engine.worker.native(self.actual,'SCED',self.q,program.B/'lp_ipm_worker.py',3600.)
  eq,y=program.stage(self.root,'SCED',self.q,self.n,False)
  for k in ('rows','variables','objective'):need(eq[k]==self.q[k],'actual corrected model differs')
  need(y['native_status']=='Optimal'and y['optimize_calls']==y['transport_solve_entries']==1,'failed/unknown/repeated SCED')
  audit=program.checks(eq,y,self.n,False);need(audit['KKT']['passed'],'full original KKT failed')
  for i,v in enumerate(self.uq['variables']):
   if v['kind']!='C':need(abs(y['primal'][i]-round(self.u['primal'][i]))<1e-7,'integer drift')
  self.verify()
  from v6_host import e
  certificate=e.dual_adapter().metadata(eq,y,self.n)
  self.audit=audit;self.eq=eq;self.y=y;self.certificate=certificate
  from event_path import pack
  self.registered_digest=digest(pack(dict(names=self.n,case=self.case,request=eq,response=y,audit=audit,certificate=certificate,feedback=self.feedback)))
  record=dict(schema='gd.owned_loss_feedback_pair.v1',status='accepted_research_pair',native_calls=1,new_SCUC_calls=0,original_SCUC_source=str(self.old/'SCUC'),SCED_audit=audit,loss_feedback=self.feedback,formal=False,safety_release=False,**certificate)
  record.update(run_id=self.h.session.registry.get('run_id'),ledger_id=self.h.lid,prepared_sha256=sha(self.p/'SUMMARY.json'),SCUC_response_sha256=sha(self.old/'SCUC/response.json.gz'),SCED_response_sha256=sha(self.actual/'SCED/response.json.gz'),SCUC_gap=self.u.get('native_mip_gap'),SCUC_audit=deepcopy(self.base_proof['SCUC']))
  write(self.actual/'ACCEPTED_PAIR.json',record);self.proof=record
  self.corrected_pins={str(f):sha(f)for f in [self.actual/'ACCEPTED_PAIR.json',*[self.actual/'SCED'/n for n in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz')]]}
  _OWNED[(str(self.p.resolve()),str(self.actual.resolve()))]=self
  self.state='registered_math_only'
  # Only the owned typed registry can expose the corrected physical case/loss proxy.
  from loss_consumers import install
  install(program,e)
  from ac_candidate import candidate_from_paths
  d,m,source=candidate_from_paths(self.h,self.p,self.actual)
  need(d['gross_load']==self.receipt['payload']['gross_load'],'loss counted as physical AC load')
  need(d['loss_proxy_mw']==sum(self.feedback['node_loss_MW'].values()),'wrong loss proxy binding')
  write(self.root/'AC_INPUT.json',dict(payload=d,mapping=m,source=source));write(self.root/'AC_STARTED.json',{'max_calls':1})
  self.s.ac_calls+=1
  from ac_security import check_ac
  result=check_ac(m['ac_input'],max_iterations=60,tolerance_mva=1e-10)
  write(self.root/'AC_RESULT.json',result);need(result['input_sha256']==digest(m['ac_input']),'wrong AC input')
  self.verify();self.verify_registered()
  from bridge import postcheck
  capacity=postcheck(m,result)
  plan_error=max((abs(g['p_change_mw'])for g in result.get('generators',[])),default=float('inf'))
  actual_loss=math.fsum(x['from_p_mw']+x['to_p_mw']for x in result.get('branches',[])) if result.get('ac_converged') else None
  self.second=dict(payload=d,mapping=m,source=source,result=result,capacity=capacity,plan_actual_P_error_mw=plan_error,actual_loss_mw=actual_loss)
  self.state='second_AC_safe_other_checks_pending' if capacity.get('research_safe') and plan_error<=1e-6 else 'second_AC_unsafe'
  write(self.root/'RESULT.json',dict(status=self.state,violations=result.get('violations',[]),capacity=capacity,plan_actual_P_error_mw=plan_error if math.isfinite(plan_error) else None,actual_loss_mw=actual_loss,loss_proxy_mw=d['loss_proxy_mw'],handoff_allowed=False,PRICE_pending=True,safety_release=False))
  # Both outcomes remain quarantined; never overwrite original gate/receipt/raw.
  raise ValueError(self.state+'; original and corrected evidence retained, no handoff/carry/PRICE')
 def verify_registered(self):
  self.verify();need(self.audit is not None,'not mathematically registered')
  from event_path import pack
  from gd_post_scuc_runtime import consumers
  need(digest(pack(consumers.case_for(self.p,self.old,self.case)))==self.source_case_digest,'original effective post case drift')
  need(digest(consumers.semantics_for(self.p,self.old))==self.semantics_digest,'original effective post semantics drift')
  need(digest(pack(dict(names=self.n,case=self.case,request=self.eq,response=self.y,audit=self.audit,certificate=self.certificate,feedback=self.feedback)))==self.registered_digest,'registered typed state changed')
  for f,v in self.corrected_pins.items():need(sha(f)==v,'corrected raw drift')
  need(digest(self.eq)==digest(read(self.actual/'SCED/request.json.gz')),'corrected request changed')
