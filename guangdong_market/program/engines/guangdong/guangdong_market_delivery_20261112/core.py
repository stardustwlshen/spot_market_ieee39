"""Portable source-preserving model preparation and explicit fresh MIP worker invocation."""
from pathlib import Path
import sys,json,hashlib,time,subprocess,math
HOME=Path(__file__).resolve().parent;V=HOME/'vendor'
sys.path[:0]=[str(V/'guangdong_after_reset_r23_compressed_20261112/repo/src'),str(V/'guangdong_r68_three_state_builder_20261112'),str(V/'guangdong_market_program_v22_20261112'),str(V/'guangdong_r56_market_builder_20261112')]
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,s):
 if not x:raise ValueError(s)
def write(p,v):
 with Path(p).open('x')as f:json.dump(v,f,indent=2,allow_nan=False)
def source_hash():return sha(HOME/'SOURCE_MAP.json')
def prepare(output,case_file=None):
 from smm.highs_solver import serialize_model,validate_options
 from preflight import validate
 o=Path(output).resolve();need(not o.exists(),'fresh output required')
 if case_file is None:
  from prepare_small import build
  m,_,_=build();scope='R68 synthetic small thermal three-state energy balance; not network/full market';inputs={}
 else:
  from smm.gd_october_job_codec import loads
  from market_builder import build_market
  doc=read(case_file);need(doc.get('schema')=='gd.delivery.scuc_inputs.v1','explicit SCUC bundle schema')
  def path(n):return Path(case_file).resolve().parent/doc[n]
  c=loads(path('case_file').read_bytes());g=read(path('grid_file'));p=read(path('policy_file'));a=read(path('agc_file'))
  from smm.gd_horizon import EnergyHorizon,EnergyPeriod
  h=c['energy_inputs']['horizon'];c['energy_inputs']['horizon']=EnergyHorizon(**dict(h,periods=tuple(EnergyPeriod(**z)for z in h['periods'])))
  for award in a.values():
   for key in ('up_mw','down_mw'):award[key]={int(t):v for t,v in award[key].items()}
  need(len(g)==98 and sorted(x.get('kind','')for x in g[96:])==['peak','valley'],'one peak and valley required')
  need(math.isfinite(p['curtailment_penalty']['yuan_per_mwh']),'finite penalty required')
  m=build_market(c,g,p,a);scope='R56 uniform-state direct 48h/DA98 research SCUC; not formal';inputs={n:sha(path(n))for n in ('case_file','grid_file','policy_file','agc_file')}
 q,vs,cs=serialize_model(m,validate_options(dict(time_limit=30.,threads=1,mip_rel_gap=.001,mip_abs_gap=0.,output_flag=False)));q['preparation_elapsed_s']=0.;validate(q)
 o.mkdir();write(o/'template.json',q);write(o/'names.json',dict(variables=[v.name for v in vs],constraints=[c.name for c in cs]));r=dict(schema='gd.delivery.prepared.v1',source_map_sha256=source_hash(),core_sha256=sha(__file__),template_sha256=sha(o/'template.json'),names_sha256=sha(o/'names.json'),scope=scope,inputs=inputs,variables=len(vs),rows=len(cs),native_calls=0);write(o/'PREPARED.json',r);return r
def solve(prepared,output,seconds,execute=False):
 from preflight import validate
 p=Path(prepared).resolve();o=Path(output).resolve();need(not o.exists()and not(p/'ATTEMPT.json').exists(),'fresh output and unconsumed prepared required');need(type(seconds)in(int,float)and math.isfinite(seconds)and 5<=seconds<=86400,'explicit 5..86400 time limit')
 r=read(p/'PREPARED.json');need(r['schema']=='gd.delivery.prepared.v1'and r['source_map_sha256']==source_hash()and r['core_sha256']==sha(__file__),'prepared source contract changed')
 for n in ('template','names'):need(sha(p/(n+'.json'))==r[n+'_sha256'],'prepared artifact changed')
 q=read(p/'template.json');need(any(v['kind']!='C'for v in q['variables']),'this portable solve review supports MIP only; LP KKT adapter not yet included')
 need(not q['dual']and not q['warmstart'],'MIP only, no restored live state');q['options']['time_limit']=float(seconds);validate(q)
 worker=V/'guangdong_r65_three_state_startup_20261112/standard_counted_worker.py';cmd=[sys.executable,'-B',str(worker),str(o/'request.json'),str(o/'response.json')]
 if not execute:return dict(status='dryrun_only',command=cmd,native_calls=0,explicit_execute_native_required=True)
 write(p/'ATTEMPT.json',dict(output=str(o),automatic_retry=False));o.mkdir();q['deadline']=time.perf_counter()+seconds;write(o/'request.json',q)
 with(o/'worker.log').open('x')as log:
  try:proc=subprocess.run(cmd,stdout=log,stderr=log,timeout=seconds+15,check=False)
  except subprocess.TimeoutExpired:
   z=dict(status='pending_timeout',automatic_retry=False);write(o/'REVIEW.json',z);return z
 response=read(o/'response.json')if(o/'response.json').exists()else{}
 z=dict(status='pending_not_accepted',automatic_retry=False,native_calls=response.get('optimize_calls'),formal_compliance=False)
 try:
  need(proc.returncode==0 and not response.get('error'),'worker error')
  need(response.get('optimize_calls')==1 and response.get('backend')=='highs'and response.get('solver_version')=='1.15.1','worker identity/counter')
  need(response.get('native_status')=='Optimal'and response.get('status')=='optimal'and response.get('has_primal')is True,'Optimal primal required')
  settings=response['native_settings'];need(settings['threads']==1 and settings['mip_rel_gap']==.001 and settings['mip_abs_gap']==0,'native settings')
  from smm.gd_r23_equivalent_compression import audit
  a=audit(q,response['primal'],response['objective']);obj=a['objective'];bound=response['bound'];need(math.isfinite(bound)and bound<=obj+1e-6+1e-9*abs(obj),'bound')
  gap=max(0.,obj-bound)/max(1.,abs(obj));need(gap<=.001+1e-9,'gap');z.update(status='accepted_mathematical_MIP_not_operator_dispatch',audit=a,bound=bound,gap=gap)
 except (ValueError,KeyError,TypeError)as e:z['reason']=str(e)
 write(o/'REVIEW.json',z);return z
