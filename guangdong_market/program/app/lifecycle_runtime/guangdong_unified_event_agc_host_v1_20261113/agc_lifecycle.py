"""Same-run causal AGC lifecycle. Internal trusted accepted-source callback, no native."""
from pathlib import Path
import sys,json,hashlib,copy,importlib.util
from datetime import timedelta
B=Path(__file__).resolve().parent;R=B.parent
INSTALL=R.parent.parent/'engines/guangdong_short_unified_v6'
MANIFEST='6a05c3018a6548428bdb68845ff428d74b5e2aec510d8dd0293cc34899a23e85'
INPUT_SHA='e947dffb0ff3ca8f21bcdf0391142274c32b2a78c7cf9ad60c5b17fabbb4c409'
sys.path.insert(0,str(B/'agc_runtime'))
from hourly_day import produce,digest,stamp
from batch_api import apply_available
read=lambda p:json.loads(Path(p).read_bytes())
from content_hash import sha
def need(x,msg):
 if not x:raise ValueError(msg)
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2,allow_nan=False)
def installed():
 sys.path.insert(0,str(R));from portable_gate import verify;verify()
 need(sha(INSTALL/'FROZEN_MANIFEST.json')==MANIFEST,'unsupported producer')
 for name,h in read(INSTALL/'FROZEN_MANIFEST.json')['files'].items():
  p=(INSTALL/name).resolve();need(p.is_relative_to(INSTALL)and sha(p)==h,'producer source changed')
 core=INSTALL/'rt/guangdong_rt_short_user_cli_v5_20261112';sys.path.insert(0,str(core));import program
 need(Path(program.__file__).resolve()==core/'program.py','isolated registered producer required')
 sys.path.insert(0,str(R/'guangdong_event_host_candidate_v1_20261113'));from v6_host import e
 return e.dual_adapter().install(program)
class AgcLifecycle:
 def __init__(self,run,root,*,accepted_source=None):
  self.p=installed();self.run,self.context=self.p.context(run);self.root=Path(root).resolve();need(not self.root.exists() or not any(self.root.iterdir()),'existing lifecycle has no durable authentication; restart refused');self.root.mkdir(parents=True,exist_ok=True);self.accepted_source=accepted_source;self.issued={}
  self.context_sha=sha(self.run/'SHORT_CONTEXT.json');self.seed_sha=sha(self.run/'seed/case.json');self.origin=stamp(read(self.run/'seed/request.json')['origin']);self.input_sha=INPUT_SHA
 def verify(self):
  need(sha(self.run/'SHORT_CONTEXT.json')==self.context_sha and sha(self.run/'seed/case.json')==self.seed_sha,'run/seed drift');need(sha(B/'agc_runtime/RESEARCH_INPUT.json')==self.input_sha,'offer/qualification drift')
 def rt_plan(self,hour):
  w=4*hour-3
  need(callable(self.accepted_source),'RT source requires live runner-owned accepted-source callback; old-run fallback forbidden')
  prep,actual=self.accepted_source(w);prep=Path(prep).resolve();actual=Path(actual).resolve();authority='trusted unified runner accepted-source callback; old raw adoption must stay explicitly labeled by runner'
  n,response=self.p.engine.checked_pair(prep,actual);case=self.p.engine.loads((prep/'derived_case.json').read_bytes());from gd_post_scuc_runtime import consumers;case_for=consumers.case_for;case=case_for(prep,actual,case);carry=read(prep/'CARRY.json');summary=read(prep/'SUMMARY.json');need(carry['seed_case_sha256']==self.seed_sha,'source belongs to another seed');need(stamp(summary['domain_start'])==self.origin+timedelta(minutes=15*(w+1)),'source window/domain mismatch');need(stamp(carry['cutoff'])==self.origin+timedelta(minutes=15*w),'source window/cutoff mismatch')
  adapter=__import__('v6_host').e.dual_adapter();adapter.validate_record(actual,adapter.metadata(__import__('v6_host').e.read(actual/'SCED/request.json.gz'),response,n))
  v=dict(zip(n['variables'],response['primal']));pins={str(f):sha(f)for f in [prep/'SUMMARY.json',prep/'CARRY.json',prep/'derived_case.json',prep/'names.json',*[actual/st/f for st in ('SCUC','SCED')for f in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz')]]}
  return case,v,dict(cutoff=carry['cutoff'],domain_start=summary['domain_start'],available_at=summary['domain_start'],cells=48,source_kind='RT',SCED_response_sha256=read(actual/'SCED/COMPLETED.json')['response']['raw_sha256'],numerical_certificate=__import__('v6_host').e.dual_adapter().metadata(__import__('v6_host').e.read(actual/'SCED/request.json.gz'),response,n),audit=dict(SCUC_accepted=True,SCED_full_KKT=True),source_pins=pins,source_authentication=authority)
 def da_plan(self):
  da=Path(self.context['selected_DA']).resolve();src=INSTALL/'rt/guangdong_rt_recommit_program_v32_20261112/interface_validators.py';spec=importlib.util.spec_from_file_location('_agc_registered_da',src);g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g);pins=g.selected_da(da);a=read(da/'ARTIFACTS.json');st=a['stages']['da_sced'];ac=read(da/st['acceptance']['path']);need(ac['audit']['full_physical']['max_primal_violation']<1e-7 and ac['audit']['full_physical']['max_integer_violation']<1e-7,'DA physical acceptance')
  warm=read(self.run/'seed/WARMSTART_SOURCE.json');need(st['expanded_response']['sha256']==warm['response_sha256'] and st['full_names']['sha256']==warm['names_sha256'],'DA not seed source');need(a['inputs']['case']['sha256']==self.seed_sha,'DA case not seed')
  initial=read(self.run/'INITIAL_CARRY.json');dpath=Path(initial['day_declaration_path']);need(sha(dpath)==initial['day_declaration_sha256'],'day declaration drift');d=read(dpath)['days'][self.origin.date().isoformat()];need(d['source']['case_sha256']==self.seed_sha and d['source']['accepted_original_DA_response_sha256']==warm['response_sha256'],'DA publication declaration source');pins.update({str(f):sha(f)for f in [dpath,self.run/'seed/WARMSTART_SOURCE.json',self.run/'INITIAL_CARRY.json']})
  case=self.p.engine.loads((da/a['inputs']['case']['path']).read_bytes());n=read(da/st['full_names']['path']);resp=read(da/st['expanded_response']['path']);v=dict(zip(n['variables'],resp['primal']))
  return case,v,dict(cutoff=d['available_at'],domain_start=self.origin.isoformat(),available_at=d['available_at'],cells=12,source_kind='DA_SCED',SCED_response_sha256=st['expanded_response']['sha256'],audit=dict(DA_SCED_accepted=True,SCED_full_KKT=True,full_physical=True),source_pins=pins,source_authentication='registered selected DA accepted SCED + seed + hypothetical day declaration')
 def create(self,hour):
  self.verify();need(type(hour)is int and 0<=hour<24,'hour0..23');case,v,plan=self.da_plan()if hour==0 else self.rt_plan(hour);cells=plan['cells'];need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'400 unchanged')
  plan.update(schema='gd.agc.verified_plan.v1',branch_id=self.context['run_id'],seed_case_sha256=self.seed_sha,zone='explicit_single_research_zone',availability_basis='explicit hypothetical publication; NOT actual timestamp',thermal={g:dict(stable=[v[f'stable[{g},{t}]']for t in range(cells)],power=[v[f'power[{g},{t}]']for t in range(cells)],minimum=[d['min_mw'][t]for t in range(cells)],maximum=[d['max_mw'][t]for t in range(cells)])for g,d in case['energy_inputs']['thermal'].items()},formal=False,safety_release=False)
  d=read(B/'agc_runtime/RESEARCH_INPUT.json');start=self.origin+timedelta(hours=hour);d.update(hour_start=start.isoformat(),as_of=(start-timedelta(minutes=30)).isoformat());r=produce(plan,d,expected_plan_sha256=digest(plan),branch_id=self.context['run_id'],seed_case_sha256=self.seed_sha);need(r['result']['awards_mw']=={g:10 for g in plan['thermal']},'changed awards require new enabled clearing/solve/security path');return r
 def produce(self,hour,*,cutoff):
  need(type(hour)is int and 0<=hour<24,'hour0..23')
  need(self.origin+timedelta(hours=hour,minutes=-30)<=stamp(cutoff),'auction not yet due');self.verify();f=self.root/f'H{hour:02}.json'
  if hour in self.issued:
   r=self.issued[hour]
   need(f.exists() and read(f)==r,'persisted receipt tampered or absent')
   for path,h in r['verified_plan']['source_pins'].items():need(sha(path)==h,'accepted source changed after issuance')
   if hour: self.accepted_source(4*hour-3) # live host reauthenticates accepted record
  else:
   need(not f.exists(),'unregistered persisted receipt cannot authorize consumption')
   r=self.create(hour);write(f,r);self.issued[hour]=copy.deepcopy(r)
  return copy.deepcopy(r)
 def apply_before_scuc(self,kw,carry):
  self.verify();need(carry['seed_case_sha256']==self.seed_sha,'wrong consumer seed');cut=stamp(carry['cutoff']);begin=stamp(kw['contract']['domain_start']);need(begin==cut+timedelta(minutes=15),'issued prefix/domain mismatch');end=begin+timedelta(hours=4);receipts=[]
  for h in range(24):
   start=self.origin+timedelta(hours=h)
   if start+timedelta(hours=1)>begin and start<end and start-timedelta(minutes=30)<=cut:receipts.append(self.produce(h,cutoff=cut.isoformat()))
  thermal={g:dict(maximum=[d['max_mw'][t]for t in range(48)],minimum=[d['min_mw'][t]for t in range(48)])for g,d in kw['energy_inputs']['thermal'].items()};c=dict(branch_id=self.context['run_id'],seed_case_sha256=self.seed_sha,cutoff=cut.isoformat(),delivery=begin.isoformat(),domain_start=begin.isoformat(),cells=48,thermal=thermal,zone='explicit_single_research_zone',positive_reserve_mw=[kw['reserve']['positive_mw'][t]for t in range(48)],accident_reserve_mw=[kw['reserve']['contingency_mw'][t]for t in range(48)])
  base=json.loads(json.dumps(kw['agc_awards']));z=apply_available(receipts,c,base,expected_receipt_digests=[digest(r)for r in receipts],expected_consumer_digest=digest(c));need(z['agc']==base,'award changed: new solve/security lifecycle required, cannot reuse old raw');new={g:{**a,**{side:{int(t):v for t,v in a[side].items()}for side in ('up_mw','down_mw')}}for g,a in z['agc'].items()};kw['agc_awards'].clear();kw['agc_awards'].update(new)
  from safety_adapter import pending_safety
  binding=dict(safety=pending_safety(),issuer_receipt_sha256=[digest(r)for r in receipts],branch_sha256=digest({'run_id':self.context['run_id'],'seed':self.seed_sha}),period_sha256=digest({'cutoff':cut.isoformat(),'domain_start':begin.isoformat()}),run_id=self.context['run_id'],consumer_sha256=digest(c),cutoff=cut.isoformat(),domain_start=begin.isoformat(),receipt_files={str(self.root/f'H{int((stamp(r["context"]["hour_start"])-self.origin).total_seconds()//3600):02}.json'):sha(self.root/f'H{int((stamp(r["context"]["hour_start"])-self.origin).total_seconds()//3600):02}.json')for r in receipts},applied=z['applied_receipts'],same_original_awards=True,already_consumed_by_old_SCED=False,H0_issued_first15min_untouched=True,native_calls=0,formal=False,safety_release=False)
  return kw,binding
