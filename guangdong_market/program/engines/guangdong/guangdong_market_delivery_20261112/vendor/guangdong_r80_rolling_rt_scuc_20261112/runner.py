"""Four-call main-authorized experiment. Default never invokes native."""
from protocol import *
import argparse,time,subprocess,os
sys.path.insert(0,str(ROOT/'guangdong_r65_three_state_startup_20261112'))
from preflight import validate as preflight
from smm import highs_solver as hs
sys.path.insert(0,str(ROOT/'guangdong_r46_curtailable_full_day_20261112'))
from r46smm.gd_r16_native_support import audit_lp_certificate
WORKER=ROOT/'guangdong_r65_three_state_startup_20261112/standard_counted_worker.py';WORKER_SHA='96dfd2d81053f3a41dcc42a6078d99d1900eb8dbf20bda0b8bee59804e55f637'
def native(q,n,out):
 preflight(q);need(sha(WORKER)==WORKER_SHA,'frozen worker');out.mkdir(exist_ok=False);request=deepcopy(q);request['deadline']=time.perf_counter()+60;write(out/'request.json',request);write(out/'names.json',n);write(out/'IDENTITY.json',dict(worker_sha256=WORKER_SHA,request_sha256=sha(out/'request.json'),native_attempt=1,automatic_retry=False))
 with(out/'worker.log').open('x')as log:
  proc=subprocess.Popen([sys.executable,'-B',str(WORKER),str(out/'request.json'),str(out/'response.json')],stdout=log,stderr=log,env={**hs.child_environment(),'PYTHONDONTWRITEBYTECODE':'1'})
  write(out/'launch.json',dict(pid=proc.pid,attempt=1))
  try:code=proc.wait(timeout=70)
  except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=5);raise RuntimeError('bounded native timeout, check raw result; no retry')
 need(code==0,'worker exit');r=json.loads((out/'response.json').read_bytes());write(out/'CONFIRMED.json',dict(returncode=code,response_sha256=sha(out/'response.json'),native_status=r.get('native_status'),native_calls=r.get('optimize_calls')))
 need(r.get('backend')=='highs' and r.get('solver_version')=='1.15.1','actual expected backend/version')
 need(r.get('native_settings')==dict(output_flag=False,threads=1,presolve='choose',mip_rel_gap=.001,mip_abs_gap=0.),'actual native options readback')
 need(r.get('optimize_calls')==1 and r.get('native_status')=='Optimal'and r.get('has_primal'),'actual nonOptimal stops')
 a=audit(request,r['primal'],r['objective']);need(a['max_primal_violation']<=1e-6 and a['max_integer_violation']<=1e-6,'primal/integer review')
 if any(v['kind']!='C'for v in q['variables']):need(r['native_mip_gap']<=.001+1e-12,'new actual MIP gap')
 else:
  cert=audit_lp_certificate(request,r,n);a['KKT']={k:v for k,v in cert.items()if k not in('rows','columns')}
 write(out/'REVIEW.json',a);return r

def run(output):
 out=Path(output);out.mkdir(exist_ok=False);history=None;results=[];attempt=0
 try:
  for w in range(2):
   m=build(w)
   if history is not None:bind_history(m,history)
   q,n,vs=serialize(m);pdir=out/f'W{w}_SCUC_prepared';save_prepared(pdir,q,n,dict(stage='RT_SCUC',window=w,history=history,official_certification=False))
   attempt+=1;uc=native(q,n,out/f'W{w}_SCUC');lp=fixed_sced(q,n,uc['primal']);save_prepared(out/f'W{w}_SCED_prepared',lp,n,dict(stage='RT_SCED_fixed_new_RT_integer',window=w,SCUC_response_sha256=sha(out/f'W{w}_SCUC/response.json'),AGC_stage='explicit synthetic fixed awards10MW per base unit; not formal auction',AC_security='not performed, no formal execution authority'))
   attempt+=1;ed=native(lp,n,out/f'W{w}_SCED');new=control_history(lp,n,ed['primal'],w);new['SCUC_response_sha256']=sha(out/f'W{w}_SCUC/response.json');new['SCED_response_sha256']=sha(out/f'W{w}_SCED/response.json');write(out/f'W{w}_ISSUED_RESEARCH_COMMANDS.json',new)
   if w==0:need(any(z['connection_tick']==3 and z['kind']=='hot'for z in new['started']),'test must actually select new fast start3; do not claim intended scenario otherwise')
   else:
    values=dict(zip(n['variables'],ed['primal']));need(abs(values['fastP[6]']-dto().curves['hot'].mean_mw[3])<=1e-7,'ongoing startup curve preserved at second delivery')
   values=dict(zip(n['variables'],ed['primal']));rt_direction=values['direction[2026-11-11T20]'];need(abs(rt_direction-1)<=1e-6,'actual new RT storage discharge commitment must differ from declared DA charge')
   mode_evidence=dict(original_DA_mode='charge',new_RT_mode='discharge',actual_hour_binary=rt_direction,actual_optimized_discharge_MW_at_peak=[values[f'dis[{t}]']for t in (12,13,14)],note='actual solved integer/LP lookahead, not yet executed energy',SCUC_response_sha256=new['SCUC_response_sha256'],SCED_response_sha256=new['SCED_response_sha256'])
   offset=3+3*w;partitions={label:dict(first_index=lo,end_exclusive=hi,start=(ORIGIN+timedelta(minutes=5*lo)).isoformat(),end=(ORIGIN+timedelta(minutes=5*hi)).isoformat(),cells=hi-lo,dt_minutes=5)for label,lo,hi in [('near',offset,offset+24),('far',offset+24,offset+48),('extra_tail',offset+48,N)]}
   history=new;results.append(dict(window=w,partitions=partitions,storage_recommitment=mode_evidence,SCUC_objective=uc['objective'],SCED_objective=ed['objective'],SCUC_status=uc['native_status'],SCED_status=ed['native_status'],history=new))
  write(out/'RESULT.json',dict(status='two_actual_RT_SCUC_SCED_research_windows',native_calls=attempt,results=results,issued_RT_cells=6,executed_RT_cells=0,execution_note='At W1 cutoff20:00 only preparation complete; W0/W1 orders issued into future. No false executed count.',DA_original_unchanged=True,formal_AGC_pending=True,AC_security_pending=True,formal_execution_authority=False,official_certification=False))
 except BaseException as e:write(out/'STOPPED.json',dict(attempts=attempt,error=type(e).__name__+': '+str(e),automatic_retry=False));raise
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--output',required=True);a.add_argument('--execute-native',action='store_true');z=a.parse_args();need(z.execute_native,'main explicit actual authorization flag required');run(z.output)
