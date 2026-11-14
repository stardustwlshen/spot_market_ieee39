"""Live-source research safety state. Missing operating evidence never grants release."""
from pathlib import Path
from copy import deepcopy
import json,hashlib,collections
from ac_candidate import candidate
from ac_adapter import digest
import failure_workflow as fw
B=Path(__file__).resolve().parent
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,m):
 if not x:raise ValueError(m)
def plan():return dict(default_new_AC_calls=0,max_new_AC_calls_per_session=1,supported_point='accepted window first 5-minute domain point only',new_optimization_calls=0,automatic_AC=False,automatic_reclear=False,default_publication_allowed=False)
from candidate_safety import CandidateSafetyMixin
class SafetySession(CandidateSafetyMixin):
 def __init__(self,host):
  from safety_gate import verify_package
  verify_package()
  from unified import RollingHost
  need(isinstance(host,RollingHost),'registered live RollingHost required')
  self.host=host;self.states={};self.failures={};self.counterfactual=False;self.ac_calls=0
  self.pins={str(B/name):h for name,h in read(B/'SOURCE_MANIFEST.json')['files'].items()};self.pins[str(B/'safety_gate.py')]=sha(B/'safety_gate.py')
 def verify(self):
  self.host.session.verify_sources()
  for p,h in self.pins.items():need(sha(p)==h,'safety dependency drift')
 def _source(self,w):
  self.verify();p,a=self.host.accepted_source(w);p,a=Path(p),Path(a)
  n,y=self.host.session.program.engine.checked_pair(p,a)
  from v6_host import e,read as rawread
  e.verify_pair(rawread(a/'SCUC/request.json.gz'),rawread(a/'SCUC/response.json.gz'),rawread(a/'SCED/request.json.gz'),y,n)
  return p,a,n,y
 def inspect(self,w):
  p,a,n,y=self._source(w);counts=collections.Counter(x.split('[')[0]for x in n['constraints'])
  checks={k:dict(status='unknown',reason='not verified for this branch and point')for k in fw.CHECKS}
  groups={'capacity_balance':['c_balance','phase_balance','reserves'],'storage_soc':['battery.c_soc']}
  for key,rows in groups.items():
   need(all(counts[x]for x in rows),'required model rows missing');checks[key]=dict(status='model_mathematics_verified',row_counts={x:counts[x]for x in rows},operating_safety_proven=False)
  case=self.host.session.program.engine.loads((p/'derived_case.json').read_bytes())
  need(set(case['energy_inputs']['thermal'])=={'C1','C2','C3','CCGT','GT'},'unsupported thermal/hydro inventory')
  checks['hydro_vibration']=dict(status='not_applicable_to_explicit_five_thermal_inventory',operating_safety_proven=False)
  self.host.agc.verify();receipts={str(h):digest(r)for h,r in self.host.agc.issued.items()}
  z=dict(branch_id=self.host.branch_id,window=w,domain_start=read(p/'SUMMARY.json')['domain_start'],checks=checks,hourly_receipt_digests=receipts,source_pins={str(f):sha(f)for f in [p/'names.json',p/'derived_case.json',p/'BOUNDARY.json',a/'SCED/request.json.gz',a/'SCED/response.json.gz']},status='BLOCKED_UNVERIFIED_SYSTEM_SAFETY',publication_allowed=False,dispatch_authorized=False,safety_release=False,counterfactual_research_enabled=self.counterfactual,new_AC_calls=self.ac_calls)
  if w in self.states and 'AC' in self.states[w]:z['AC']=self.states[w]['AC'];z['checks']['network_sections_ac']=deepcopy(self.states[w]['checks']['network_sections_ac']);z['status']=self.states[w]['status']
  self.states[w]=z;return deepcopy(z)
 def ac_input(self,w):
  self._source(w);d,m,evidence=candidate(self.host,w);return d,m,evidence
 def _record_ac(self,w,d,m,result,basis):
  z=self.inspect(w);z['AC']=dict(basis=basis,input_sha256=digest(m['ac_input']),result_sha256=digest(result),start=d['start'],end=d['end'],status=result['status'],scope='single research mean-P snapshot; no hour/day/N-1/dynamics claim',violations=result.get('violations',[]))
  failed=not result.get('ac_snapshot_safe',False)
  z['checks']['network_sections_ac']=dict(status='fail'if failed else'research_point_pass',source_sha256=digest(result),scope=z['AC']['scope'])
  z['status']='BLOCKED_SYSTEM_SAFETY'if failed else'BLOCKED_OTHER_SAFETY_CHECKS';self.states[w]=z
  if failed:self._open_failure(w,d,result)
  return deepcopy(z)
 def reuse_known_W0(self,w=0):
  d,m,_=self.ac_input(w);known=B/'known_W0';status=read(known/'SOURCE_BOUND_STATUS.json')
  need(self.host.branch_id==status['host_branch_id'] and w==status['source_window'],'historical result belongs to another branch/point')
  need(d==read(known/'PAYLOAD.json'),'full AC payload differs; reuse forbidden')
  need(m['ac_input']==read(known/'MAPPING.json')['ac_input'],'full mapped AC input differs')
  result=read(known/'AC_RESULT.json');need(result['input_sha256']==digest(m['ac_input']),'AC input/result binding mismatch')
  return self._record_ac(w,d,m,result,'complete payload and AC input identity with completed original one-call evidence; no new AC')
 def run_ac(self,w,output,*,execute_ac=False):
  need(execute_ac is True and self.ac_calls<1,'explicit one-call AC budget required');d,m,evidence=self.ac_input(w)
  out=Path(output);need(not out.exists(),'existing occurrence; inspect, never retry');out.mkdir(parents=True)
  for name,x in [('PAYLOAD',d),('MAPPING',m),('SOURCE',evidence),('STARTED',{'branch_id':self.host.branch_id,'window':w,'max_calls':1})]:(out/(name+'.json')).write_text(json.dumps(x,indent=2))
  self.ac_calls+=1
  from ac_security import check_ac
  result=check_ac(m['ac_input'],max_iterations=60,tolerance_mva=1e-10)
  (out/'AC_RESULT.json').write_text(json.dumps(result,indent=2));return self._record_ac(w,d,m,result,'new explicitly authorized single AC diagnostic')
 def _open_failure(self,w,d,result):
  hour=int((__import__('datetime').datetime.fromisoformat(d['start'])-self.host.agc.origin).total_seconds()//3600)
  need(hour in self.host.agc.issued,'same-hour trusted receipt required');r=self.host.agc.issued[hour]
  awards=dict(target_mw=50,awards_mw=deepcopy(r['result']['awards_mw']),reserve_requirements={'positive_mw':400,'accident_mw':400},source_sha256=digest(r))
  s=dict(schema='gd.agc.failure_workflow.research.v1',status='BLOCKED_SYSTEM_SAFETY',revision=0,branch_id=self.host.branch_id,hour_start=r['context']['hour_start'],case_sha256=self.host.agc.seed_sha,original_awards=awards,original_awards_sha256=digest(awards),failure={'kind':'source_bound_AC_failure','point':d['start'],'violations':result.get('violations',[])},failure_source_sha256=digest(result),candidate=None,events=[{'event':'source_bound_AC_failure'}],missing_inputs=['same-run material redispatch/reclearing declaration','fresh model and source-bound safety rechecks'],article59_policy=deepcopy(fw.ARTICLE59_POLICY),unit_removals=[],publication_allowed=False,dispatch_authorized=False,formal=False,native_calls=0)
  self.failures[w]=fw.seal(s)
 def propose_reclear(self,w,proposal,*,declaration_path=None):
  self.verify();need(w in self.failures,'source-bound failure required');s=self.failures[w]
  if declaration_path is None:return dict(status='BLOCKED_MISSING_DECLARATION',missing_inputs=['same-run declaration file registered by trusted host; a self-consistent JSON/hash is insufficient'],publication_allowed=False)
  p=str(Path(declaration_path).resolve());pins=self.host.session.registry['source_pins'];need(p in pins and sha(p)==pins[p],'declaration not trusted-host registered')
  declared=read(p);need(declared.get('branch_id')==self.host.branch_id and declared.get('proposal')==proposal,'same-run declaration mismatch')
  n=fw.propose(s,proposal,expected_state_sha256=s['state_sha256']);self.failures[w]=n
  return fw.view(n,expected_state_sha256=n['state_sha256'])
 def reclear_request(self,w):
  self.verify();s=self.failures[w];need(s['status']=='RESEARCH_RECHECK_REQUIRED','missing declaration/inputs: cannot request reclear')
  return dict(branch_id=self.host.branch_id,candidate_id=s['candidate']['candidate_id'],proposal=deepcopy(s['candidate']['proposal']),status='READY_FOR_EXPLICIT_RESEARCH_RUNNER',automatic_execution=False,safety_release=False,required=['new accepted raw pair registered by same host','fresh safety evidence; do not reuse failed result as pass'])
 def plan_reclear(self,w,runner):
  import sys
  request=self.reclear_request(w)
  need(getattr(runner,'host',None)is self.host,'reclear runner belongs to another live host')
  module=sys.modules.get(type(runner).__module__);path=str(Path(module.__file__).resolve())if module and getattr(module,'__file__',None)else None
  pins=self.host.session.registry['source_pins'];need(path in pins and sha(path)==pins[path],'runner source not registered by live host')
  result=runner.plan_reclear(deepcopy(request))
  need(result.get('branch_id')==self.host.branch_id and result.get('candidate_id')==request['candidate_id'],'runner plan identity mismatch')
  need(result.get('status')in ('pending_authorization','blocked')and result.get('actual_native_calls')==0 and result.get('actual_AC_calls')==0,'planning callback must not claim execution')
  return dict(request=request,runner_plan=result,status='PENDING_AUTHORIZATION'if result['status']=='pending_authorization'else'BLOCKED_RUNNER_INPUTS',recleared=False,safety_release=False)
 def allow_counterfactual_research(self,*,explicit=False):
  need(explicit is True,'separate explicit research continuation required');self.counterfactual=True
  return dict(counterfactual_research_enabled=True,safety_release=False,publication_allowed=False,dispatch_authorized=False)
 def require_safe_publication(self):raise ValueError('safety publication/dispatch unsupported; failed/missing checks cannot be overridden')
