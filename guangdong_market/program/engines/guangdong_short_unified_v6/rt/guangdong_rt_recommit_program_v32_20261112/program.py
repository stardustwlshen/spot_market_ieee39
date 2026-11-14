"""Compressed research RT continuation. Default is pure; no legacy writes or automatic retry."""
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
import sys,os,json,hashlib,math,time,subprocess,gzip,threading,argparse
from evidence_io import save,load,write_json,sha,read_bytes,store_bytes
from warmstart_proposal import fixed_integer_recourse,transplant_only,mathematical_identity
import interface_validators as guard
from handoff_validator import handoff_immutables
import preflight
B=Path(__file__).resolve().parent
read=lambda p:json.loads(Path(p).read_bytes())
filehash=lambda p:sha(Path(p).read_bytes())
def need(ok,msg):
 if not ok:raise ValueError(msg)
def pins():return {str(p):filehash(p)for p in sorted(B.glob('*.py'))}
def setup(program):
 sys.path.insert(0,str(program));import program as legacy
 # Import by distinct identity because this executable itself is __main__.
 import importlib.util
 s=importlib.util.spec_from_file_location('_frozen_v1',Path(program)/'program.py');v=importlib.util.module_from_spec(s);s.loader.exec_module(v)
 return v

def ctx(run):
 r=Path(run).resolve();c=read(r/'CONTEXT.json');need(c['code_pins']==pins(),'v2 source changed during run')
 for p,h in c['legacy_pins'].items():need(filehash(p)==h,'frozen historical source changed')
 v=setup(c['legacy_program']);v.bind_dependencies(c['dependency_root'])
 if c.get('history_run'):v.context(c['history_run'])
 return r,c,v

def initialize(history,program,run):
 history=Path(history).resolve();program=Path(program).resolve();r=Path(run).resolve();need(not r.exists(),'fresh run only');v=setup(program);_,old=v.context(history)
 from acceptance import load_verified
 for w in range(4):load_verified(history/f'W{w}/ACCEPTED_PAIR.json',read(history/f'W{w}/prepared/request.json'))
 request=read(history/'handoff/W4/request.json');h=read(history/'handoff/W4/HANDOFF.json')
 for p,z in h['pins'].items():need(filehash(p)==z,'historical W4 boundary changed')
 need(request['window']==4,'explicit W4 import only')
 r.mkdir();r.joinpath('seed').mkdir()
 refs={name:store_bytes(r/'seed'/f'{name}.json.gz',(history/'seed'/f'{name}.json').read_bytes())for name in ('case','agc','policy')}
 refs['request']=save(r/'seed/request.json.gz',request)
 refs['receipt']=save(r/'seed/receipt.json.gz',read(history/'handoff/W4/RECEIPT.json'))
 paths=[program/'program.py',program/'DEPENDENCIES.json',*sorted((program/'runtime').glob('*.py')),*[history/f'W{w}/ACCEPTED_PAIR.json'for w in range(4)],history/'handoff/W4/HANDOFF.json',history/'handoff/W4/request.json',history/'handoff/W4/RECEIPT.json']
 write_json(r/'CONTEXT.json',dict(schema='gd.rt.compressed.context.v2',history_run=str(history),legacy_program=str(program),dependency_root=old['dependency_root'],run_id=request['run_id'],start_window=4,code_pins=pins(),legacy_pins={str(p):filehash(p)for p in paths},refs=refs,formal=False))
 return {'imported_window':4,'native_calls':0,'legacy_unchanged':True}

def previous(r,c,w):
 if w==c['start_window'] and 'initial_witness' in c:
  z=c['initial_witness'];return dict(zip(load(z['names'])['variables'],load(z['response'])['primal']))
 if w==4:
  from acceptance import load_verified
  h=Path(c['history_run']);return load_verified(h/'W3/ACCEPTED_PAIR.json',read(h/'W3/prepared/request.json'))[1]
 a=verified(r,c,w-1);return dict(zip(load(a['names'])['variables'],load(a['SCED_response'])['primal']))
def request_for(r,c,w):
 if w==c['start_window']:return load(c['refs']['request'])
 return validate_handoff(r,c,w)
def prepare(run,w):
 r,c,v=ctx(run);p=r/f'W{w}';need(not p.exists(),'existing window forbidden');req=request_for(r,c,w)
 from builder import build
 from smm.gd_october_job_codec import loads
 from smm.highs_solver import serialize_model,validate_options
 from smm.gd_r23_equivalent_compression import audit
 need(c['refs']['case']['raw_sha256']==req['case_sha256'] and c['refs']['policy']['raw_sha256']==req['policy_sha256'] and c['refs']['agc']['raw_sha256']==req['sources']['AGC']['sha256'],'source byte identity');case=loads(read_bytes(c['refs']['case']));agc=load(c['refs']['agc']);policy=load(c['refs']['policy'])
 for z in agc.values():
  for k in ('up_mw','down_mw'):z[k]={int(t):x for t,x in z[k].items()}
 m=build(case,req,agc,policy);q,vs,cs=serialize_model(m,validate_options(dict(time_limit=300.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=True)));q['preparation_elapsed_s']=0.
 n=dict(variables=[x.name for x in vs],constraints=[x.name for x in cs]);old=previous(r,c,w)
 for name in n['variables']:
  if name not in old and name.startswith('rt_notice['):
   g,t=name[len('rt_notice['):-1].split(',');g=g.strip("'");j=int(t)+int(round(case['curves'][g]['notice_h']*12));keys=[f'start[{g},{j}]',f"start['{g}',{j}]"];found=[old[k]for k in keys if k in old];need(j>=576 or len(found)==1,'notice source mapping');old[name]=0. if j>=576 else found[0]
 need(all(name in old for name in n['variables']),'exact previous name mapping');x=[old[name]for name in n['variables']];obj=q['objective']['constant']+math.fsum(a*x[j]for j,a in q['objective']['terms']);a=audit(q,x,obj);need(a['max_primal_violation']<=1e-6 and a['max_integer_violation']<=1e-6,'previous feasible witness')
 for col,value in zip(q['variables'],x):col['start']=value
 q['warmstart']=True;p.mkdir()
 ticket=dict(schema='gd.rt.prepared.v2',window=w,context_sha256=filehash(r/'CONTEXT.json'),request=save(p/'request.json.gz',req),template=save(p/'template.json.gz',q),names=save(p/'names.json.gz',n),witness_audit=a,code_pins=pins(),native_calls=0)
 write_json(p/'PREPARED.json',ticket);return {'window':w,'columns':len(vs),'rows':len(cs),'native_calls':0,'warmstart_objective':obj}

def native(r,stage,q,worker):
 p=r/stage
 if p.exists():return reuse_native(p,q,worker)
 p.mkdir()
 q=deepcopy(q);q['deadline']=time.perf_counter()+q['options']['time_limit'];rq=save(p/'request.json.gz',q)
 job=dict(request=rq,worker=str(worker),worker_sha256=filehash(worker),response_path=str(p/'response.json.gz'));write_json(p/'JOB.json',job)
 proc=subprocess.Popen([sys.executable,'-B',str(B/'compressed_worker.py'),str(p/'JOB.json'),str(p/'RESPONSE_REF.json')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'})
 write_json(p/'LAUNCH.json',dict(pid=proc.pid,attempt=1,wrapper_sha256=filehash(B/'compressed_worker.py'),job_sha256=filehash(p/'JOB.json'),automatic_retry=False))
 def pump():
  with (p/'worker.log.gz').open('xb')as f:
   with gzip.GzipFile(fileobj=f,mode='wb',mtime=0)as g:
    while True:
     data=proc.stdout.read(65536)
     if not data:break
     g.write(data)
 t=threading.Thread(target=pump);t.start()
 try:code=proc.wait(timeout=q['options']['time_limit']+30)
 except subprocess.TimeoutExpired:
  proc.terminate();proc.wait(timeout=10);t.join();write_json(p/'PENDING.json',{'retry':False});raise RuntimeError('native outcome pending; no retry')
 t.join();raw=gzip.decompress((p/'worker.log.gz').read_bytes());data=(p/'worker.log.gz').read_bytes();log=dict(schema='gd.evidence.gzip.v2',path=str(p/'worker.log.gz'),codec='gzip',stored_sha256=sha(data),raw_sha256=sha(raw),raw_bytes=len(raw),stored_bytes=len(data))
 need(code==0 and (p/'RESPONSE_REF.json').exists(),'worker response missing');rr=read(p/'RESPONSE_REF.json')['response'];response=load(rr)
 z=dict(returncode=code,request=rq,response=rr,log=log,worker=str(worker),worker_sha256=filehash(worker),wrapper_sha256=filehash(B/'compressed_worker.py'),job_sha256=filehash(p/'JOB.json'));write_json(p/'COMPLETED.json',z)
 return q,response,{**z,'launched_this_call':True}

def verified(r,c,w):
 a=read(r/f'W{w}/ACCEPTED_PAIR.json');need(a['schema']=='gd.rt.accepted_pair.v2'and a['context_sha256']==filehash(r/'CONTEXT.json'),'acceptance context')
 for p,h in a['code_pins'].items():need(filehash(p)==h,'accepted source changed')
 for ref in a['refs']:read_bytes(ref)
 for p,h in a['receipt_pins'].items():need(filehash(p)==h,'accepted native receipt changed')
 guard.acceptance_fields(a,read(r/f'W{w}/PREPARED.json'),c,w,filehash(r/'CONTEXT.json'),pins(),read(r/f'W{w}/SCED/COMPLETED.json'),r/f'W{w}/PREPARED.json')
 return a

def execute(run,w,enabled):
 r,c,v=ctx(run);p=r/f'W{w}';ticket=read(p/'PREPARED.json');guard.prepared(ticket,c,w,filehash(r/'CONTEXT.json'),pins(),request_for(r,c,w),load)
 if not enabled:return preflight.status(B,run,w)
 if (p/'ACCEPTED_PAIR.json').exists():
  z=strict_accept(r,c,w);return dict(status='existing_completed_strictly_reverified',window=w,native_calls=0,formal=False)
 from acceptance import checks
 from contracts import fixed_sced
 from smm.gd_r23_equivalent_compression import audit
 q=load(ticket['template']);names=load(ticket['names']);workers={'SCUC':B/'scuc_worker.py','SCED':B/'lp_worker.py'};lp=fixed_integer_recourse(q,[z['start']for z in q['variables']]);lp['options'].update(time_limit=60.,dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0)
 lq,lr,le=native(p,'WARMSTART_LP',lp,workers['SCED']);decision={'accepted':False,'basis':'retain original feasible prior witness','native_status':lr.get('native_status')}
 if lr.get('native_status')=='Optimal'and lr.get('has_primal'):
  try:
   review=checks(lq,lr,names,False);pr=audit(q,lr['primal'],lr['objective']);need(pr['max_primal_violation']<=1e-6 and pr['max_integer_violation']<=1e-6,'original free model feasibility');need(lr['objective']<=ticket['witness_audit']['objective']+1e-6,'not worse start');candidate=transplant_only(q,lr['primal']);need(mathematical_identity(candidate)==mathematical_identity(q),'original math changed');q=candidate;decision.update(accepted=True,basis='fixed prior integer recourse only; not final SCED',full_KKT=review,free_model_audit=pr)
  except Exception as e:decision['rejection']=str(e)
 if (p/'WARMSTART_DECISION.json').exists():need(read(p/'WARMSTART_DECISION.json')==decision,'existing warmstart decision mismatch')
 else:write_json(p/'WARMSTART_DECISION.json',decision)
 chosen=save_or_compare(p/'SCUC_template.json.gz',q);uq,ur,ue=native(p,'SCUC',q,workers['SCUC']);ua=checks(uq,ur,names,True)
 sq=fixed_sced(q,ur['primal']);sq['options'].update(time_limit=120.,dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0);st=save_or_compare(p/'SCED_template.json.gz',sq);eq,er,ee=native(p,'SCED',sq,workers['SCED']);ea=checks(eq,er,names,False)
 need(all(abs(x-round(y))<=1e-8 for x,y,col in zip(er['primal'],ur['primal'],q['variables'])if col['kind']!='C'),'new integer drift');need(ur.get('optimize_calls')==er.get('optimize_calls')==1,'exact native run counter')
 refs=[ticket[k]for k in ('request','template','names')]+[chosen,st]+[z[k]for z in(le,ue,ee)for k in('request','response','log')]
 a=dict(schema='gd.rt.accepted_pair.v2',window=w,run_id=c['run_id'],context_sha256=filehash(r/'CONTEXT.json'),request=ticket['request'],names=ticket['names'],SCED_response=ee['response'],SCUC_audit=ua,SCED_audit=ea,SCUC_gap=ur['native_mip_gap'],refs=refs,code_pins=pins(),receipt_pins={str(p/x):filehash(p/x)for x in('PREPARED.json','WARMSTART_DECISION.json','WARMSTART_LP/COMPLETED.json','SCUC/COMPLETED.json','SCED/COMPLETED.json')},formal=False,safety_release=False,native_calls=3)
 write_json(p/'ACCEPTED_PAIR.json',a);return {'status':'accepted','window':w,'native_calls':sum(z['launched_this_call']for z in(le,ue,ee)),'SCUC_gap':ur['native_mip_gap'],'SCUC_objective':ur['objective'],'SCED_objective':er['objective'],'warmstart_LP_used':decision['accepted'],'formal':False}

def handoff(run,w,enabled):
 need(enabled,'explicit counterfactual simulation required');r,c,v=ctx(run);a=strict_accept(r,c,w);req=load(a['request']);values=dict(zip(load(a['names'])['variables'],load(a['SCED_response'])['primal']))
 from contracts import validate,stamp,digest
 cutoff=req['cutoff_tick']+3;delivery=req['delivery_tick']+3;at=(stamp(req['origin'])+timedelta(minutes=5*cutoff)).isoformat();fixed={}
 for name,value in values.items():
  if name.startswith('rt_notice['):continue
  if name.startswith('battery.hour_direction['):
   hour=name.rsplit(',',1)[1].rstrip(']').strip("'\"")
   if stamp(hour)<stamp(req['origin'])+timedelta(minutes=5*delivery):fixed[name]=value
  elif '['in name:
   token=(name.rsplit(',',1)[-1]if ','in name else name.split('[',1)[1]).rstrip(']')
   if token.isdigit()and int(token)<delivery:fixed[name]=value
 for name,value in req['state']['fixed_values'].items():need(name in fixed and abs(fixed[name]-value)<=1e-7,'prefix drift')
 notices={g:{str(t):int(round(values[f'rt_notice[{g},{t}]']))for t in range(cutoff)}for g in req['DA_online']}
 receipt=dict(schema='gd.research.no_deviation_execution_receipt.v2',run_id=c['run_id'],at=at,executed_end_tick=cutoff,issued_end_tick=delivery,previous_receipt_sha256=req['state']['receipt_sha256'],accepted_pair_sha256=filehash(r/f'W{w}/ACCEPTED_PAIR.json'),prefix_values_sha256=digest(fixed),sealed_notice_values=notices,tracking_deviation_mw=0,formal=False,safety_release=False,AGC_AC_clearance='not_verified',basis='counterfactual_research_execution_without_AGC_AC_clearance')
 z=deepcopy(req);z.update(window=w+1,cutoff_tick=cutoff,delivery_tick=delivery,cutoff=at);z['state'].update(executed_end_tick=cutoff,issued_end_tick=delivery,receipt_at=at,receipt_sha256=digest(receipt),completed_window=w,fixed_values=fixed,notice_values=notices);validate(z)
 out=r/f'handoff/W{w+1}'
 if out.exists():
  validate_handoff(r,c,w+1);return dict(status='existing_handoff_strictly_reverified',native_calls=0,formal=False)
 out.mkdir(parents=True,exist_ok=False);write_json(out/'HANDOFF.json',dict(schema='gd.rt.handoff.v2',window=w+1,context_sha256=filehash(r/'CONTEXT.json'),request=save(out/'request.json.gz',z),receipt=save(out/'receipt.json.gz',receipt),acceptance_sha256=filehash(r/f'W{w}/ACCEPTED_PAIR.json'),formal=False,safety_release=False));return {'at':at,'executed':cutoff,'issued':delivery,'formal':False,'safety_release':False}

def _legacy_main_unused():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['import','build','run','handoff']);p.add_argument('--run',required=True);p.add_argument('--history');p.add_argument('--legacy-program');p.add_argument('--window',type=int,default=4);p.add_argument('--execute-native',action='store_true');p.add_argument('--simulate-no-deviation',action='store_true');a=p.parse_args()
 z=initialize(a.history,a.legacy_program,a.run)if a.action=='import'else prepare(a.run,a.window)if a.action=='build'else execute(a.run,a.window,a.execute_native)if a.action=='run'else handoff(a.run,a.window,a.simulate_no_deviation)
 print(json.dumps(z,allow_nan=False))


def save_or_compare(path,obj):
 refpath=Path(str(path)+'.REF.json')
 if Path(path).exists():
  need(refpath.exists(),'partial compressed artifact; no automatic recovery');ref=read(refpath);need(load(ref)==obj,'existing template differs');return ref
 ref=save(path,obj);write_json(refpath,ref);return ref

def reuse_native(p,template,worker):
 need((p/'COMPLETED.json').exists(),'unknown/incomplete native occurrence; no retry')
 e=read(p/'COMPLETED.json');q=load(e['request']);response=load(e['response']);read_bytes(e['log']);job=read(p/'JOB.json');launch=read(p/'LAUNCH.json')
 need(e['returncode']==0 and {k:z for k,z in q.items()if k!='deadline'}==template,'completed actual mathematical template mismatch')
 need(e['worker']==str(worker)and e['worker_sha256']==filehash(worker)==job['worker_sha256'],'worker source mismatch')
 need(e['wrapper_sha256']==filehash(B/'compressed_worker.py')==launch['wrapper_sha256'],'wrapper mismatch')
 need(e['job_sha256']==filehash(p/'JOB.json')==launch['job_sha256']and job['request']==e['request'],'job request mismatch')
 need(read(p/'RESPONSE_REF.json')['response']==e['response']and launch['attempt']==1,'native response/launch mismatch')
 need(response.get('transport_solve_entries')==1 and (response.get('optimize_calls')==1 or p.name=='WARMSTART_LP' and response.get('optimize_calls')==0 and not response.get('has_primal')),'explicit optimizer invocation counter')
 return q,response,{**e,'launched_this_call':False}

def strict_accept(r,c,w):
 a=verified(r,c,w);ticket=read(r/f'W{w}/PREPARED.json');guard.prepared(ticket,c,w,filehash(r/'CONTEXT.json'),pins(),request_for(r,c,w),load);q=load(next(x for x in a['refs']if x['path'].endswith('/SCUC_template.json.gz')));orig=load(ticket['template']);need(mathematical_identity(q)==mathematical_identity(orig),'SCUC math drift')
 from acceptance import checks
 from contracts import fixed_sced
 names=load(ticket['names']);uq,ur,_=reuse_native(r/f'W{w}/SCUC',q,B/'scuc_worker.py');checks(uq,ur,names,True)
 lp=fixed_sced(q,ur['primal']);lp['options'].update(time_limit=120.,dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0);eq,er,_=reuse_native(r/f'W{w}/SCED',lp,B/'lp_worker.py');checks(eq,er,names,False)
 need(all(abs(x-round(y))<=1e-8 for x,y,col in zip(er['primal'],ur['primal'],q['variables'])if col['kind']!='C'),'SCED integer mismatch');return a

def validate_handoff(r,c,w):
 h=read(r/f'handoff/W{w}/HANDOFF.json');q=load(h['request']);receipt=load(h['receipt']);a=verified(r,c,w-1);prev=load(a['request']);handoff_immutables(prev,q)
 from contracts import digest,validate
 need(h.get('window')==w and h['context_sha256']==filehash(r/'CONTEXT.json'),'handoff context/window')
 need(q['window']==w and q['run_id']==c['run_id']==receipt['run_id'],'handoff run/window')
 need(h['acceptance_sha256']==receipt['accepted_pair_sha256']==filehash(r/f'W{w-1}/ACCEPTED_PAIR.json'),'handoff acceptance chain')
 need(q['state']['receipt_sha256']==digest(receipt)and receipt['previous_receipt_sha256']==prev['state']['receipt_sha256'],'receipt chain');need(q['state']['completed_window']==w-1,'completed prior window')
 need(receipt['at']==q['cutoff']==q['state']['receipt_at']and receipt['executed_end_tick']==q['cutoff_tick']==prev['cutoff_tick']+3 and receipt['issued_end_tick']==q['delivery_tick']==prev['delivery_tick']+3,'clock progression')
 vals=dict(zip(load(a['names'])['variables'],load(a['SCED_response'])['primal']));fixed=q['state']['fixed_values'];need(digest(fixed)==receipt['prefix_values_sha256'],'prefix digest')
 from contracts import stamp
 expected={}
 for name,x in vals.items():
  if name.startswith('rt_notice['):continue
  if name.startswith('battery.hour_direction['):
   if stamp(name.rsplit(',',1)[1].rstrip(']').strip("'\""))<stamp(q['origin'])+timedelta(minutes=5*q['delivery_tick']):expected[name]=x
  elif '['in name:
   t=(name.rsplit(',',1)[-1]if ','in name else name.split('[',1)[1]).rstrip(']')
   if t.isdigit()and int(t)<q['delivery_tick']:expected[name]=x
 need(expected==fixed,'raw solution to prefix exact reproduction')
 for name,x in prev['state']['fixed_values'].items():need(abs(fixed[name]-x)<=1e-7,'prefix drift')
 need(receipt['sealed_notice_values']==q['state']['notice_values'],'notice receipt')
 for g in q['DA_online']:
  need(set(q['state']['notice_values'][g])==set(map(str,range(q['cutoff_tick']))),'notice inventory')
  for t,x in q['state']['notice_values'][g].items():need(abs(vals[f'rt_notice[{g},{t}]']-x)<=1e-8,'raw notice integer')
 need(receipt['safety_release']is False and receipt['formal']is False,'research boundary only');validate(q);return q

def seed_da(da,program,run):
 da=Path(da).resolve();program=Path(program).resolve();r=Path(run).resolve();need(not r.exists(),'fresh output required');checked_DA=guard.selected_da(da);v=setup(program);v.bind_dependencies(program/'vendor')
 from prepare_research_seed import prepare as create_seed
 r.mkdir();temp=r/'temporary_seed_source';create_seed(da,temp)
 # Small initial source files retained as explicit evidence; no worker expansion or deletion.
 refs={k:store_bytes(r/'seed'/f'{k}.json.gz',(temp/f'{k}.json').read_bytes())for k in('case','agc','policy','request')}
 refs['receipt']=store_bytes(r/'seed/receipt.json.gz',(temp/'SEED_ASSUMPTIONS.json').read_bytes())
 art=read(da/'ARTIFACTS.json');stage=art['stages']['da_sced'];sources={}
 for k,key in [('names','full_names'),('response','expanded_response')]:
  p=(da/stage[key]['path']).resolve();need(filehash(p)==stage[key]['sha256'],'accepted DA witness identity');sources[k]=store_bytes(r/'seed'/f'DA_{k}.json.gz',p.read_bytes())
 request=load(refs['request']);paths=[program/'program.py',program/'DEPENDENCIES.json',*sorted((program/'runtime').glob('*.py')),da/'ARTIFACTS.json',da/stage['acceptance']['path']]
 write_json(r/'CONTEXT.json',dict(schema='gd.rt.compressed.context.v3',adapter_version=32,history_run=None,legacy_program=str(program),dependency_root=str(program/'vendor'),run_id=request['run_id'],start_window=0,code_pins=pins(),legacy_pins={**checked_DA,**{str(p):filehash(p)for p in paths}},refs=refs,initial_witness=sources,event_model='uniform_original_v1; three_state_requires_separate_schema_and_explicit_adapter',formal=False))
 return dict(status='selected_accepted_DA_seed_prepared',run_id=request['run_id'],start_window=0,native_calls=0,formal=False)

def migrate_v2(source,program,run,review):
 source=Path(source).resolve();program=Path(program).resolve();r=Path(run).resolve();need(not r.exists(),'fresh migration destination');report=read(review);need(report['status']=='compressed_raw_full_math_and_native_chain_verified','independent v2 review required')
 for p,h in report['source_pins'].items():need(filehash(p)==h,'review/source binding')
 import importlib.util
 s=importlib.util.spec_from_file_location('_registered_v2',program/'program.py');old=importlib.util.module_from_spec(s);s.loader.exec_module(old);_,oc,_=old.ctx(source);a=old.verified(source,oc,4);h=read(source/'handoff/W5/HANDOFF.json');q=load(h['request']);receipt=load(h['receipt'])
 from contracts import digest,validate
 need(h['context_sha256']==filehash(source/'CONTEXT.json')and h['acceptance_sha256']==receipt['accepted_pair_sha256']==filehash(source/'W4/ACCEPTED_PAIR.json'),'v2 boundary binding');need(q['window']==5 and q['state']['receipt_sha256']==digest(receipt),'v2 state receipt')
 need(report['proposed_boundary']['prefix_values_sha256']==digest(q['state']['fixed_values'])==receipt['prefix_values_sha256'],'independent prefix proof')
 proposed=report['proposed_boundary'];prior=load(a['request']);need(q['cutoff_tick']==proposed['cutoff_tick']==receipt['executed_end_tick']and q['delivery_tick']==proposed['delivery_tick']==receipt['issued_end_tick'],'independent boundary clocks');need(q['cutoff']==proposed['at']==receipt['at']==q['state']['receipt_at'],'boundary timestamps');need(q['state']['notice_values']==proposed['sealed_notice_values']==receipt['sealed_notice_values'],'independent notice proof');need(receipt['previous_receipt_sha256']==prior['state']['receipt_sha256']and q['run_id']==receipt['run_id']==oc['run_id'],'boundary source chain');need(receipt['safety_release']is False and receipt['formal']is False,'research only');validate(q)
 r.mkdir();refs={k:store_bytes(r/'seed'/f'{k}.json.gz',read_bytes(oc['refs'][k]))for k in('case','agc','policy')};refs['request']=store_bytes(r/'seed/request.json.gz',read_bytes(h['request']));refs['receipt']=store_bytes(r/'seed/receipt.json.gz',read_bytes(h['receipt']))
 iw={k:store_bytes(r/'seed'/f'previous_{k}.json.gz',read_bytes(a[f]))for k,f in [('names','names'),('response','SCED_response')]}
 paths=[source/'CONTEXT.json',source/'W4/ACCEPTED_PAIR.json',source/'handoff/W5/HANDOFF.json',Path(review),*map(Path,report['source_pins'])];lp=dict(oc['legacy_pins']);lp.update({str(p):filehash(p)for p in paths});lp.update(oc['code_pins'])
 write_json(r/'CONTEXT.json',dict(schema='gd.rt.compressed.context.v3',adapter_version=32,history_run=oc['history_run'],legacy_program=oc['legacy_program'],dependency_root=oc['dependency_root'],run_id=q['run_id'],start_window=5,code_pins=pins(),legacy_pins=lp,refs=refs,initial_witness=iw,original_v2_context_sha256=filehash(source/'CONTEXT.json'),event_model='uniform_original_v1',formal=False))
 return dict(status='v2_W4_imported_without_reoptimization',start_window=5,native_calls=0,formal=False)

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['seed','migrate-v2','build','run','accept','handoff','preflight']);p.add_argument('--run',required=True);p.add_argument('--da-run');p.add_argument('--legacy-program');p.add_argument('--source-run');p.add_argument('--source-program');p.add_argument('--review');p.add_argument('--window',type=int,default=0);p.add_argument('--execute-native',action='store_true');p.add_argument('--simulate-no-deviation',action='store_true');a=p.parse_args()
 if a.action=='preflight':z=preflight.status(B,a.run,a.window)
 elif a.action=='seed':z=seed_da(a.da_run,a.legacy_program,a.run)
 elif a.action=='migrate-v2':z=migrate_v2(a.source_run,a.source_program,a.run,a.review)
 elif a.action=='build':z=prepare(a.run,a.window)
 elif a.action=='run':z=execute(a.run,a.window,a.execute_native)
 elif a.action=='accept':r,c,_=ctx(a.run);strict_accept(r,c,a.window);z=dict(status='strict_full_math_reverified',native_calls=0)
 else:z=handoff(a.run,a.window,a.simulate_no_deviation)
 print(json.dumps(z,allow_nan=False))
 if z.get('status')=='blocked':sys.exit(2)
if __name__=='__main__':main()

# Installed solver-policy revision: MIP gap 0.01, per native solve 3600s; LP gap N/A.
