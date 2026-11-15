"""Read-only current Pipeline interruption inventory. No live/math authority.
Exact expected stage enumeration, no recursive native count or symlink double count.
"""
from pathlib import Path
import hashlib,json,gzip

MODE_KEYS=('rt_clock_mode','grid_minutes','windows','finite_h1','finite_hourly','post_scuc_day','post_scuc_planning','reference_revision','counterfactual_continue','research_event_simulation','event_price_profile','selected_event_profile','ac_once','candidate_loss_once','threads','mip_gap','SCED_solver','profile','nextday_selection','nextday_explicit','post_declarations','stop_request')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def need(v,m):
 if not v:raise ValueError(m)
def verify_pins(pins):
 need(isinstance(pins,dict),'source_pins not a mapping')
 for f,h in pins.items():need(Path(f).is_file() and sha(f)==h,'source changed: '+str(f))
def merge_verified_pins(target,addition):
 for path,digest in addition.items():
  need(path not in target or target[path]==digest,'conflicting verified dependency: '+str(path))
  target[path]=digest

def verified_dependency_record(path):
 # Bind exactly the bytes parsed; reuse the validation already required by scan.
 path=Path(path);raw=path.read_bytes();z=json.loads(raw);pins={str(path):hashlib.sha256(raw).hexdigest()}
 deps=z.get('source_pins',{});verify_pins(deps);merge_verified_pins(pins,deps)
 return z,pins

def descriptor(d,expected):
 need(d.get('codec')=='gzip','unsupported native descriptor')
 p=Path(d['path']);need(p.resolve()==Path(expected).resolve(),'descriptor target mismatch')
 data=p.read_bytes();need(hashlib.sha256(data).hexdigest()==d['stored_sha256'] and len(data)==d['stored_bytes'],'stored native data changed')
 raw=gzip.decompress(data);need(hashlib.sha256(raw).hexdigest()==d['raw_sha256'] and len(raw)==d['raw_bytes'],'decoded native data changed')
 return json.loads(raw)
def _rt(stage):
 stage=Path(stage)
 if not stage.exists():return dict(state='absent',native_calls=0)
 job=stage/'JOB.json';done=stage/'COMPLETED.json'
 if not(job.is_file() and done.is_file()):return dict(state='unknown_started',native_calls=None,reason='native目录已存在，但JOB/COMPLETED未完整；不得重启')
 j=read(job);d=read(done);q=descriptor(j['request'],stage/'request.json.gz');r=descriptor(d['response'],stage/'response.json.gz')
 need(Path(j['response_path']).resolve()==(stage/'response.json.gz').resolve(),'response target changed')
 need(j['worker_sha256']==d['worker_sha256'] and sha(j['worker'])==j['worker_sha256'],'native worker changed')
 need(d.get('solve_entries')==1 and r.get('optimize_calls')==1,'native call count invalid')
 status=r.get('native_status',r.get('status'))
 return dict(state='completed',native_calls=1,native_status=status,native_occurrence=str((stage/'response.json.gz').resolve()),raw_complete=True,math_accepted=False,request_sha256=sha(stage/'request.json.gz'),response_sha256=sha(stage/'response.json.gz'),evidence={str(f):sha(f)for f in (job,done,stage/'request.json.gz',stage/'response.json.gz')})
def _price(folder):
 folder=Path(folder);p=folder/'prepared';native=p/'native_once'
 if not native.exists():return dict(state='prepared_not_started' if p.exists() else 'absent',native_calls=0)
 required=[native/n for n in ('LAUNCH.json','IDENTITY.json','COMPLETED.json','request.json','response.json')]
 if not all(f.is_file()for f in required):return dict(state='unknown_started',native_calls=None,reason='定价启动目录存在，但完成证据不完整；不得重启')
 launch,ident,done,req,res=map(read,required)
 need(launch.get('attempt')==1 and ident.get('automatic_retry') is False,'price retry identity changed')
 need(sha(native/'request.json')==ident['request_sha256'],'price request changed')
 need(sha(p/'READY.json')==ident['ready_sha256'],'price READY changed')
 need(sha(native/'response.json')==done['response_sha256'],'price response changed')
 need(res.get('optimize_calls')==1,'price call count invalid')
 z=dict(state='completed',native_calls=1,native_status=res.get('native_status',res.get('status')),native_occurrence=str((native/'response.json').resolve()),returncode=done.get('returncode'),math_accepted=False,evidence={str(f):sha(f)for f in required})
 accepted=folder/'ACCEPTED.json'
 if accepted.is_file():
  a=read(accepted);verify_pins(a.get('source_pins',{}));need(a.get('KKT',{}).get('passed') is True,'stored price acceptance incomplete')
  from gd_resume_runtime.recovery_branch import pinned_value
  need(pinned_value(a['source_pins'],native/'response.json')==sha(native/'response.json'),'accepted price raw binding missing')
  z.update(math_accepted=True,accepted_sha256=sha(accepted))
 return z

def _window(folder,stages,*,dependency_sink=None):
 dependencies={}
 p=Path(folder);a=p/'actual/ACCEPTED_PAIR.json';carry=p/'EXACT_CARRY.json';result=p/'RESULT.json';candidates=sorted(p.glob('H*_AGC_MATH_CANDIDATE.json'));receipts=sorted(p.glob('H*_RESEARCH_AGC_RECEIPT.json'))
 z=dict(math_accepted=False,handoff_complete=False,receipt_selected=False,candidate_count=len(candidates),receipt_count=len(receipts))
 if a.exists():
  accepted=read(a);need(all(s['state']=='completed'for s in stages),'pair acceptance without two completed native stages')
  need(accepted.get('status')=='accepted_research_pair','unsupported pair acceptance')
  for name in ('SCUC','SCED'):need(accepted[name+'_response_sha256']==sha(p/'actual'/name/'response.json.gz'),'accepted pair raw binding changed')
  need(accepted.get('prepared_sha256')==sha(p/'prepared/SUMMARY.json'),'accepted prepared binding changed')
  z['math_accepted']=True
 if carry.exists():
  need(z['math_accepted'],'handoff without accepted mathematics');c,bound=verified_dependency_record(carry);merge_verified_pins(dependencies,bound)
  phase=c.get('accepted_control_phase');need(phase and sha(phase['path'])==phase['sha256'],'handoff phase binding missing')
  z['handoff_complete']=True;z['carry_sha256']=sha(carry)
 for f in candidates:
  c,bound=verified_dependency_record(f);merge_verified_pins(dependencies,bound);need(c.get('schema')=='gd.agc.math_candidate.v1' and c.get('consumable') is False and c.get('selected') is False,'invalid candidate record')
 for f in receipts:
  r,bound=verified_dependency_record(f);merge_verified_pins(dependencies,bound);selection=r.get('selection')
  if selection is None:z['legacy_receipt_without_commit_proof']=True;continue
  need(z['handoff_complete'] and selection.get('handoff_complete') is True,'selected receipt before handoff')
  need(Path(selection['carry_path']).resolve()==carry.resolve() and selection['carry_sha256']==sha(carry),'selected receipt wrong carry')
  need(any(sha(c)==selection['candidate_sha256']for c in candidates),'selected candidate evidence absent')
  need(selection.get('safety_release') is False and r.get('formal') is False,'receipt must not grant operation authority')
  z['receipt_selected']=True
 if z['handoff_complete'] and candidates and not z['receipt_selected']:state='handoff_complete_receipt_uncommitted'
 elif z['handoff_complete'] and not result.exists():state='handoff_complete_host_checkpoint_missing'
 elif z['handoff_complete']:state='handoff_complete'
 elif z['math_accepted']:state='math_complete_handoff_missing'
 elif all(s['state']=='completed'for s in stages):state='native_complete_pair_acceptance_missing'
 elif any(s['state']=='unknown_started'for s in stages):state='unknown_native_no_restart'
 elif any(s['state']=='completed'for s in stages):state='partial_pair_native_complete'
 else:state='not_started'
 if dependency_sink is not None:merge_verified_pins(dependency_sink,dependencies)
 z['state']=state;return z

def _reference(folder,source,*,allow_cancelled=False):
 folder=Path(folder);stage=folder/'actual/SCED'
 if (folder/'ATTEMPT.json').exists():
  marker=read(folder/'ATTEMPT.json')
  if allow_cancelled and marker.get('schema')=='gd.reference.abandoned_before_native.v1':
   from gd_resume_runtime.reference_abandonment import validate_abandoned_reference
   proof=validate_abandoned_reference(source)
   return dict(state='cancelled_before_native',native_calls=0,math_accepted=False,evidence=proof['pins'])
  if not stage.exists():return dict(state='unknown_started',native_calls=None,reason='reference attempt exists without completed native occurrence')
 z=_rt(stage);accepted=folder/'ACCEPTED_REFERENCE.json'
 if accepted.exists():
  a=read(accepted);need(z['state']=='completed','reference acceptance lacks completed native');verify_pins(a['source_pins']);need(sha(a['path'])==a['sha256'],'reference overlay changed');z['math_accepted']=True
 return z

def scan(output,expected=None):
 """expected may pin case_sha256/run/da/plan_sha256 and any MODE_KEYS.
 Returns conservative planning evidence, never a resumed host or solve permission.
 """
 out=Path(output).resolve();plan=read(out/'PLAN.json');need(Path(plan['output']).resolve()==out,'plan output mismatch');n=plan.get('windows',4);need(n in (4,96),'unsupported Pipeline window scope')
 verify_pins(plan['source_pins']);case=Path(plan['run'])/'seed/case.json';case_sha=sha(case)
 identity=dict(case_sha256=case_sha,run=str(Path(plan['run']).resolve()),da=str(Path(plan['da']).resolve()),plan_sha256=sha(out/'PLAN.json'),**{k:plan.get(k)for k in MODE_KEYS})
 for k,v in (expected or {}).items():need(k in identity and identity[k]==v,'case/source/mode mismatch: '+k)
 rows=[];prices=[];errors=[];complete=unknown=0;native_evidence={};seen_occurrences=set()
 def checked(call,label):
  nonlocal complete,unknown
  try:z=call()
  except (OSError,ValueError,KeyError,TypeError) as exc:z=dict(state='corrupt_or_unverifiable',native_calls=None,reason=str(exc));errors.append(dict(stage=label,error=str(exc)))
  occurrence=z.get('native_occurrence')
  if occurrence and occurrence in seen_occurrences:
   errors.append(dict(stage=label,error='duplicate physical native occurrence; not a separate solve'));z.update(state='duplicate_native_occurrence',native_calls=0)
  elif occurrence:seen_occurrences.add(occurrence)
  complete+=z.get('native_calls')or 0;unknown+=int(z.get('native_calls')is None);merge_verified_pins(native_evidence,z.get('evidence',{}));return z
 for w in range(n):
  folder=out/'pipeline/dispatch'/f'W{w}';stages=[checked(lambda folder=folder,s=s:_rt(folder/'actual'/s),f'W{w}/{s}')for s in ('SCUC','SCED')]
  try:z=_window(folder,stages,dependency_sink=native_evidence)
  except (OSError,ValueError,KeyError,TypeError)as exc:z=dict(state='corrupt_or_unverifiable',error=str(exc));errors.append(dict(stage=f'W{w}',error=str(exc)))
  rows.append(dict(window=w,SCUC=stages[0],SCED=stages[1],**z))
 for q in range(n):prices.append(dict(quarter=q,**checked(lambda q=q:_price(out/'pipeline/price'/f'q{q}'),f'q{q}/PRICE')))
 for w,z in enumerate(rows):
  q=w if plan.get('rt_clock_mode')=='provincial_15min_execution' else w+1
  if z.get('receipt_selected')and q<n and not prices[q].get('math_accepted'):z['next_step']='selected_receipt_price_missing'
 locks=[str(p)for p in (out/'RESUME.lock',out/'RESUME_LOCK.json')if p.exists()]
 maxcalls=plan.get('maximum_native_calls',3*n)
 references=[]
 for flag,name in (('reference_revision','D1_REFERENCE'),('post_scuc_day','POST_D1_REFERENCE')):
  if not plan.get(flag):continue
  folder=out/'pipeline'/name;stage=folder/'actual/SCED'
  def reference(folder=folder,stage=stage,name=name):
   return _reference(folder,out,allow_cancelled=name=='POST_D1_REFERENCE')
  references.append(dict(name=name,**checked(reference,name)))
 if plan.get('candidate_loss_once'):errors.append(dict(stage='extra_budget',error='optional loss-correction resume is outside current adapter; original failure must remain'))
 missing=[dict(window=r['window'],state=r['state'],next_step=r.get('next_step'))for r in rows if r['state']!='handoff_complete' or r.get('next_step')]
 return dict(schema='gd.resume.inventory.v1',identity=identity,source_plan=str(out/'PLAN.json'),windows=rows,prices=prices,references=references,confirmed_completed_native_calls=complete,unknown_native_occurrences=unknown,maximum_authorized_native_calls=maxcalls,remaining_native_upper_bound=max(0,maxcalls-complete-unknown),resume_allowed_by_inventory=not(unknown or errors),requires_owned_source_model_reconstruction=True,lock_paths=locks,lock_policy='persistent lock files are evidence only; launcher must acquire live kernel mutex; never delete historical records',errors=errors,missing_steps=missing,all_standard_prices_accepted=all(r.get('math_accepted')for r in prices),native_evidence=native_evidence,math_replayed=False,new_native_calls=0,safety_release=False,formal_bill=None)
