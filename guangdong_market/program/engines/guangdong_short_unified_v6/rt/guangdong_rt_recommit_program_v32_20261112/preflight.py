"""Read-only UI gate around frozen engine APIs. Never launches or prepares a native model."""
from pathlib import Path
import sys,json,importlib.util,hashlib,argparse

def status(engine,run,window):
 engine=Path(engine).resolve();run=Path(run).resolve();sys.path.insert(0,str(engine));spec=importlib.util.spec_from_file_location('_selected_rt_preflight_engine',engine/'program.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 result=dict(schema='gd.rt.ui_preflight.v1',native_calls=0,window=window,pending_stages=[],stage_status={},engine_sha256=hashlib.sha256((engine/'program.py').read_bytes()).hexdigest(),ready_to_run=False,formal=False,safety_release=False,stages={})
 try:
  r,c,_=m.ctx(run);p=r/f'W{window}';ticket=m.read(p/'PREPARED.json');result.update(context_sha256=m.filehash(r/'CONTEXT.json'),prepared_sha256=m.filehash(p/'PREPARED.json'),source_identity=hashlib.sha256(json.dumps(m.pins(),sort_keys=True).encode()).hexdigest());req,original,names=m.guard.prepared(ticket,c,window,m.filehash(r/'CONTEXT.json'),m.pins(),m.request_for(r,c,window),m.load)
  from acceptance import checks
  from contracts import fixed_sced
  from warmstart_proposal import fixed_integer_recourse,mathematical_identity
  if(p/'ACCEPTED_PAIR.json').exists():
   m.strict_accept(r,c,window);result.update(status='accepted',ready_to_run=False,existing_accepted=True,stage_status={k:'accepted'for k in ('WARMSTART_LP','SCUC','SCED')});return result
  expected=fixed_integer_recourse(original,[z['start']for z in original['variables']]);expected['options'].update(time_limit=60.,dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0)
  prior_complete=True;uc=None;chosen=None
  for stage,worker,mip in [('WARMSTART_LP',engine/'lp_worker.py',False),('SCUC',engine/'scuc_worker.py',True),('SCED',engine/'lp_worker.py',False)]:
   d=p/stage
   if not d.exists():result['stages'][stage]={'status':'not_started'};prior_complete=False;continue
   if not prior_complete:raise ValueError('stage exists before prerequisite completed: '+stage)
   if not(d/'COMPLETED.json').exists():raise ValueError('unknown/incomplete native occurrence: '+stage+'; no retry')
   if stage=='SCUC':
    ref=m.read(p/'SCUC_template.json.gz.REF.json');chosen=m.load(ref)
    if mathematical_identity(chosen)!=mathematical_identity(original):raise ValueError('free SCUC math differs')
    expected=chosen
   if stage=='SCED':
    expected=fixed_sced(chosen,uc['primal']);expected['options'].update(time_limit=120.,dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0)
   q,rsp,e=m.reuse_native(d,expected,worker)
   if rsp.get('native_status')!='Optimal' or not rsp.get('has_primal'):
    result['stages'][stage]={'status':'completed_failed','native_status':rsp.get('native_status'),'retry_allowed':False}
    if stage!='WARMSTART_LP':raise ValueError('completed unsuccessful '+stage+'; explicit recovery required, no retry')
    result['warmstart_failure_policy']='preserve failed LP; fallback to original feasible witness without rerunning LP';continue
   audit=checks(q,rsp,names,mip);result['stages'][stage]={'status':'completed_full_math_verified','native_status':'Optimal','objective':rsp['objective'],'optimize_calls':rsp['optimize_calls']}
   if stage=='SCUC':uc=rsp
  result.update(status='ready',ready_to_run=True,existing_accepted=False,pending_stages=[k for k,v in result['stages'].items()if v['status']=='not_started'],stage_status={k:v['status']for k,v in result['stages'].items()})
 except Exception as exc:result.update(status='blocked',reason=type(exc).__name__+': '+str(exc),ready_to_run=False,stage_status={k:v['status']for k,v in result['stages'].items()})
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--engine',required=True);p.add_argument('--run',required=True);p.add_argument('--window',type=int,required=True);a=p.parse_args();z=status(a.engine,a.run,a.window);print(json.dumps(z,allow_nan=False));sys.exit(2 if z['status']=='blocked'else 0)
