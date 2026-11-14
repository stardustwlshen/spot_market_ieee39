"""Data-only registered reviewer compatibility; never execute evidence-named code."""
from pathlib import Path
import sys,math
from domain import read,sha,need,digest,ROOT
from scoped_kkt import metrics
sys.path.insert(0,str(ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'))
from smm.gd_r23_equivalent_compression import audit
B=Path(__file__).resolve().parent

def vector(n,r):
 need(len(n['variables'])==len(r['primal']) and len(set(n['variables']))==len(n['variables']),'full unique vector')
 x=dict(zip(n['variables'],r['primal']));need(all(math.isfinite(v) for v in x.values()),'finite primal')
 for k,v in list(x.items()):
  if k.startswith('battery.charge['):
   tail=k[len('battery.charge'):];x['battery.power'+tail]=x['battery.discharge'+tail]-v
 return x

def optimal(q,r,n,mip=False):
 need(r.get('native_status')=='Optimal' and r.get('has_primal') is True and r.get('backend')=='highs','real optimal HiGHS')
 a=audit(q,r['primal'],r['objective']);need(a['max_primal_violation']<=1e-6 and a['max_integer_violation']<=1e-6,'primal/integrality')
 if mip:need(r['native_mip_gap']<=.01+1e-12,'SCUC gap')
 else:need(r.get('dual_valid') is True and metrics(q,r,n,angle_tolerance=1e-8)['passed'],'full strict KKT')
 return a

def pair_inputs(path):
 a=read(path)
 if a['schema']=='gd.rt.accepted_pair.v2':return compressed_pair_inputs(path)
 need(a['schema']=='gd.research.generic_rt.accepted_pair.v1','known pair schema')
 for p,h in a['pins'].items():need(sha(p)==h,'evidence hash '+p)
 registered=read(B/'REVIEWERS.json');hits=[p for p,h in a['pins'].items() if h in registered['sha256'] and Path(p).name=='acceptance.py'];need(len(hits)==1,'exactly one explicitly registered reviewer')
 r=read(a['request_path']);need(r['run_id']==a['run_id'] and r['window']==a['window'] and digest(r)==a['request_sha256'],'run/window binding')
 n=read(a['names_path']);ep=Path(a['SCED_response_path']);need(sha(ep)==a['SCED_response_sha256'] and str(ep) in a['pins'],'SCED pinned')
 up=[Path(p) for p,h in a['pins'].items() if h==a['SCUC_response_sha256'] and Path(p).name=='response.json'];need(len(up)==1,'unique SCUC response')
 uq=read(up[0].parent/'request.json');u=read(up[0]);eq=read(ep.parent/'request.json');ed=read(ep)
 for p in [ep.parent/'request.json',up[0].parent/'request.json']:need(str(p) in a['pins'],'raw matrix pinned')
 optimal(uq,u,n,True);optimal(eq,ed,n)
 need(uq['rows']==eq['rows'] and uq['objective']==eq['objective'] and len(uq['variables'])==len(eq['variables']),'same SCUC/SCED math')
 for c,d,v in zip(uq['variables'],eq['variables'],u['primal']):
  if c['kind']=='C':need(c==d,'continuous bounds preserved')
  else:need(d['kind']=='C' and d['lb']==d['ub']==float(round(v)),'new integers fixed')
 return a,r,vector(n,ed)

def seed_inputs(da_run,seed_request,case,agc):
 from datetime import datetime,timedelta
 da=Path(da_run);ar=read(da/'ARTIFACTS.json');s=ar['stages']['da_sced'];r=read(seed_request)
 for ref in s.values():
  if isinstance(ref,dict) and 'path' in ref:need(sha(da/ref['path'])==ref['sha256'],'DA stage pin')
 need(r['DA_acceptance_sha256']==s['acceptance']['sha256'] and sha(case)==r['case_sha256'] and sha(agc)==r['sources']['AGC']['sha256'],'same accepted DA seed')
 # Full expanded DA matrix has exact reconstructed primal/dual; review rather than repurpose RT ticket.
 n=read(da/s['full_names']['path']);q=read(da/s['full_template']['path']);ed=read(da/s['expanded_response']['path']);optimal(q,ed,n)
 r={**r,'window':'seed','delivery_tick':0,'cutoff':(datetime.fromisoformat(r['origin'])-timedelta(minutes=15)).isoformat()}
 for z in r['sources'].values():need(datetime.fromisoformat(z['available_at'])<=datetime.fromisoformat(r['cutoff']) and z['uses_future_actual'] is False,'D-1 causality')
 a=dict(run_id=r['run_id'],window='seed',SCED_response_sha256=s['expanded_response']['sha256'],evidence_kind='accepted_DA_fallback_seed_not_RT_SCUC')
 return a,r,vector(n,ed)


def compressed_pair_inputs(path):
 from evidence_io import load,read_bytes
 p=Path(path).resolve();a=read(p);root=p.parent.parent
 need(a['schema']=='gd.rt.accepted_pair.v2' and sha(root/'CONTEXT.json')==a['context_sha256'],'compressed context binding')
 from compressed_contracts import producer
 context=read(root/'CONTEXT.json');version=producer(context,a['code_pins']);ticket=read(p.parent/'PREPARED.json')
 need(ticket['context_sha256']==a['context_sha256'] and a['request']==ticket['request'] and a['names']==ticket['names'],'accepted PREPARED identity')
 need(a['run_id']==context['run_id'] and ticket['window']==a['window'],'accepted run/window identity')
 for group in ['code_pins','receipt_pins']:
  for f,h in a[group].items():need(sha(f)==h,'compressed source/receipt changed')
 for ref in a['refs']:read_bytes(ref)
 req=load(a['request']);names=load(a['names']);need(req['run_id']==a['run_id'] and req['window']==a['window'],'compressed run/window')
 native={}
 for stage in ['SCUC','SCED']:
  d=p.parent/stage;done=read(d/'COMPLETED.json');job=read(d/'JOB.json');launch=read(d/'LAUNCH.json')
  need(str(d/'COMPLETED.json') in a['receipt_pins'] and done['returncode']==0 and launch['attempt']==1 and launch['automatic_retry'] is False,'completed single native occurrence')
  need(job['request']==done['request'] and job['worker_sha256']==done['worker_sha256']==sha(job['worker']),'native worker/request binding')
  need(launch['job_sha256']==done['job_sha256']==sha(d/'JOB.json'),'native job binding')
  need(launch['wrapper_sha256']==done['wrapper_sha256'] and done['wrapper_sha256'] in a['code_pins'].values(),'registered wrapper')
  need(read(d/'RESPONSE_REF.json')['response']==done['response'],'native response binding')
  need(done['request'] in a['refs'] and done['response'] in a['refs'],'accepted full raw refs')
  q=load(done['request']);r=load(done['response']);need(r['transport_solve_entries']==1,'one wrapper solve entry');need(version==2 or r.get('optimize_calls')==1,'v32 explicit optimizer count');optimal(q,r,names,stage=='SCUC');native[stage]=(q,r,done)
 uq,u,_=native['SCUC'];eq,ed,done=native['SCED'];need(done['response']==a['SCED_response'],'accepted SCED identity')
 need(uq['rows']==eq['rows'] and uq['objective']==eq['objective'] and len(uq['variables'])==len(eq['variables']),'same compressed SCUC SCED math')
 for c,d,v in zip(uq['variables'],eq['variables'],u['primal']):
  if c['kind']=='C':need(c==d,'continuous bounds preserved')
  else:need(d['kind']=='C' and d['lb']==d['ub']==float(round(v)),'new accepted integers fixed')
 a={**a,'SCED_response_sha256':a['SCED_response']['raw_sha256']}
 return a,req,vector(names,ed)
