from pathlib import Path
import sys,os,json,hashlib,math,argparse
from copy import deepcopy
from datetime import datetime,timedelta
from evidence_io import save,load,write_json,sha,read_bytes
from native_transport import native
B=Path(__file__).resolve().parent;R=B.parent;I=R/'guangdong_rt_three_state_integration_v1_20261112';V=R/'guangdong_rt_recommit_program_v1_20261112'
sys.path[:0]=[str(I),str(V/'runtime'),str(V/'vendor/guangdong_after_reset_r23_compressed_20261112/repo/src')];os.environ['GD_RECOMMIT_DEPENDENCY_ROOT']=str(V/'vendor')
from acceptance import checks
from contracts import fixed_sced
from smm.highs_solver import serialize_model,validate_options
from smm.gd_r23_equivalent_compression import audit
from rt_bridge import configure_rt,expected_prefix,binding
import pyomo.environ as p
read=lambda f:json.loads(Path(f).read_bytes())
hashfile=lambda f:sha(Path(f).read_bytes())
def need(ok,msg):
 if not ok:raise ValueError(msg)
def pins():
 paths=list(B.glob('*.py'))+list(I.glob('*.py'))+list((V/'runtime').glob('*.py'))
 paths += [V/'vendor'/n for n in read(V/'DEPENDENCIES.json')]
 return {str(f.resolve()):hashfile(f)for f in paths}
def verify(c):
 for f,h in c['source_pins'].items():need(hashfile(f)==h,'source changed '+f)
def prepare(out,parent):
 out=Path(out).resolve();need(not out.exists(),'fresh output required');out.mkdir()
 from prepare_model import original_witness
 m,case,agc,parameters,phases=original_witness(parent)
 original=read(V/'user_ready_run_v1/seed/request.json');art=read(Path(parent)/'ARTIFACTS.json');policy=read(Path(parent)/art['inputs']['policy']['path'])
 # DA online derives selected parent's trajectory, not hard-coded baseline declaration.
 online={g:[bool(round(p.value(m.online[g,t])))for t in m.T]for g in m.G}
 state=dict(schema='gd.rt.three_state.issued_prefix.v1',origin=case['contract']['day_start'],cutoff_tick=0,delivery_tick=3,DA_online=online,**binding(case,parameters));state['fixed_values']={n:p.value(v)for n,v in expected_prefix(m,3,0,datetime.fromisoformat(state['origin'])).items()}
 configure_rt(m,case,parameters,policy,state)
 q,vs,cs=serialize_model(m,validate_options(dict(time_limit=300.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=True)));q['preparation_elapsed_s']=0.;x=[p.value(v)for v in vs];names=dict(variables=[v.name for v in vs],constraints=[v.name for v in cs]);obj=q['objective']['constant']+math.fsum(a*x[j]for j,a in q['objective']['terms']);proof=audit(q,x,obj);need(proof['max_primal_violation']<=1e-6 and proof['max_integer_violation']<=1e-6,'full warmstart rejected')
 for z,v in zip(q['variables'],x):z['start']=v
 q['warmstart']=True
 ident='three-state-'+hashfile(Path(parent)/'ARTIFACTS.json')[:12]+'-'+sha(json.dumps(parameters,sort_keys=True).encode())[:8]
 c=dict(schema='gd.rt.three_state.program.v1',run_id=ident,parent=str(Path(parent).resolve()),parent_artifacts_sha256=hashfile(Path(parent)/'ARTIFACTS.json'),source_pins=pins(),original400=True,safety_release=False,formal=False,native_calls=0)
 write_json(out/'CONTEXT.json',c);refs={k:save(out/(k+'.json.gz'),v)for k,v in dict(template=q,names=names,state=state,parameters=parameters).items()};write_json(out/'PREPARED.json',dict(context_sha256=hashfile(out/'CONTEXT.json'),refs=refs,witness=proof,rows=len(cs),columns=len(vs),integers=sum(z['kind']!='C'for z in q['variables']),phases=phases));return dict(status='prepared',native_calls=0,witness=proof)
def native_check(out,stage,template):
 z=read(out/stage/'COMPLETED.json');job=read(out/stage/'JOB.json');launch=read(out/stage/'LAUNCH.json');q=load(z['request']);r=load(z['response']);read_bytes(z['log'])
 need({k:v for k,v in q.items()if k!='deadline'}==template,'actual request mismatch');need(z['returncode']==0 and z['worker_sha256']==hashfile(z['worker']) and z['job_sha256']==hashfile(out/stage/'JOB.json') and launch['job_sha256']==z['job_sha256'],'worker receipt mismatch');need(r['optimize_calls']==1 and r['transport_solve_entries']==1,'actual optimize count');need(job['request']==z['request'] and z['wrapper_sha256']==hashfile(B/'compressed_worker.py'),'transport mismatch');return q,r,z
def execute(out,enabled):
 out=Path(out).resolve();c=read(out/'CONTEXT.json');verify(c);t=read(out/'PREPARED.json');need(t['context_sha256']==hashfile(out/'CONTEXT.json'),'context mismatch');q=load(t['refs']['template']);n=load(t['refs']['names'])
 if not enabled:return dict(status='dryrun',native_calls=0,SCUC_seconds=300,SCED_seconds=120)
 need(not(out/'SCUC').exists(),'existing occurrence no automatic retry')
 native(out,'SCUC',q,B/'scuc_worker.py');uq,ur,ue=native_check(out,'SCUC',q);ua=checks(uq,ur,n,True)
 sq=fixed_sced(q,ur['primal']);sq['options'].update(time_limit=120.,dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0);sr=save(out/'SCED_template.json.gz',sq)
 native(out,'SCED',sq,B/'lp_worker.py');eq,er,ee=native_check(out,'SCED',sq);ea=checks(eq,er,n,False)
 need(all(abs(v-round(w))<=1e-8 for v,w,col in zip(er['primal'],ur['primal'],q['variables'])if col['kind']!='C'),'integer drift')
 z=dict(schema='gd.rt.three_state.accepted_pair.v1',run_id=c['run_id'],context_sha256=hashfile(out/'CONTEXT.json'),refs=t['refs'],SCED_template=sr,SCUC=ue,SCED=ee,SCUC_audit=ua,SCED_audit=ea,native_calls=2,safety_release=False,formal=False,receipt_pins={str(out/s/f):hashfile(out/s/f)for s in('SCUC','SCED')for f in('COMPLETED.json','JOB.json','LAUNCH.json')})
 write_json(out/'ACCEPTED_PAIR.json',z);return dict(status='accepted',SCUC_gap=ur['native_mip_gap'],SCUC_objective=ur['objective'],SCED_objective=er['objective'],native_calls=2)
def handoff(out,confirmed):
 need(confirmed,'explicit no-deviation research simulation required');out=Path(out).resolve();c=read(out/'CONTEXT.json');verify(c);a=read(out/'ACCEPTED_PAIR.json');need(a['context_sha256']==hashfile(out/'CONTEXT.json'),'accepted context mismatch')
 for f,h in a['receipt_pins'].items():need(hashfile(f)==h,'receipt changed')
 q=load(a['refs']['template']);n=load(a['refs']['names']);uq,ur,_=native_check(out,'SCUC',q);checks(uq,ur,n,True);lp=fixed_sced(q,ur['primal']);lp['options'].update(time_limit=120.,dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0);need(lp==load(a['SCED_template']),'fixed new integer mismatch');eq,er,_=native_check(out,'SCED',lp);checks(eq,er,n,False)
 values=dict(zip(n['variables'],er['primal']));state=load(a['refs']['state']);prefix=state['fixed_values'];need(all(abs(values[k]-v)<=1e-7 for k,v in prefix.items()),'issued prefix drift');new=deepcopy(state);new.update(cutoff_tick=3,delivery_tick=6)
 # Name inventory from exact validated model names, including internal phase and typed notices.
 import re
 fixed={}
 for name,value in values.items():
  if name.startswith('three['):
   token=name.rsplit('[',1)[1].split(',',1)[0].rstrip(']');tick=int(token);limit=3 if '.notice[' in name else 6
   if tick<limit:fixed[name]=value
  elif name.startswith('battery.hour_direction['):
   hour=name.rsplit(',',1)[1].rstrip(']').strip("'\"")
   if datetime.fromisoformat(hour)<datetime.fromisoformat(state['origin'])+timedelta(minutes=30):fixed[name]=value
  elif '['in name:
   token=name.rsplit(',',1)[-1].split('[')[-1].rstrip(']')
   if token.isdigit()and int(token)<6:fixed[name]=value
 new['fixed_values']=fixed;h=out/'handoff_W1';h.mkdir();ref=save(h/'state.json.gz',new);receipt=dict(schema='gd.rt.three_state.counterfactual_handoff.v1',run_id=c['run_id'],executed_cells=3,issued_cells=6,executed_source='initial DA seed only; new RT first3 issued not yet executed',safety_release=False,formal=False,state=ref,accepted_pair_sha256=hashfile(out/'ACCEPTED_PAIR.json'),prefix_drift=0,initial_origin_retained=True,phase_category_notice_clock_retained=True);write_json(h/'HANDOFF.json',receipt);return receipt
if __name__=='__main__':
 p0=argparse.ArgumentParser();p0.add_argument('action',choices=['prepare','run','handoff']);p0.add_argument('--run',required=True);p0.add_argument('--parent');p0.add_argument('--execute-native',action='store_true');p0.add_argument('--simulate-no-deviation',action='store_true');args=p0.parse_args()
 z=prepare(args.run,args.parent)if args.action=='prepare'else execute(args.run,args.execute_native)if args.action=='run'else handoff(args.run,args.simulate_no_deviation);print(json.dumps(z))
