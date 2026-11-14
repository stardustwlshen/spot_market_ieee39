"""Fresh run PRICE-only one-shot occurrences. Import/default dry-run never solves."""
from pathlib import Path
import json,hashlib,sys,time,subprocess,os,signal
from rt_price_settlement import build_rt_price,digest,json_safe,module,need
B=Path(__file__).resolve().parent;R=B.parent
WORKER=R/'safe_workers/price_standard.py'
WORKER_SHA='fbc23effbf1e428a2c86daab6a7def7276d89604ae6969800059c3bfc34188b3'
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:json.dump(json_safe(x),f,sort_keys=True,separators=(',',':'),allow_nan=False)
def ref(root,p):return dict(path=str(Path(p).resolve().relative_to(Path(root).resolve())),sha256=sha(p))
def source_manifest():
 roots=['guangdong_r69_tail_source_price_20261112','guangdong_r61_price_boundary_20261112','guangdong_r74_quarter_price_20261112','guangdong_r62_same_source_da_rt_20261112','guangdong_r46_curtailable_full_day_20261112','guangdong_r75_simulated_energy_settlement_20261112','guangdong_r36_energy_settlement_20261112','guangdong_r47_price_settlement_contract_20261112','guangdong_after_reset_r23_compressed_20261112/repo/src/smm','guangdong_r21_day_verified_final_v1_20261112/source/repo/src/smm']
 files=[Path(__file__),B/'rt_price_settlement.py',WORKER,R/'safe_workers/price_review.py']
 for name in roots:files.extend((R/name).rglob('*.py'))
 # Exact actual R75 directory may carry a longer name.
 for d in R.glob('guangdong_r75*'):files.extend(d.glob('*.py'))
 pins={str(p.resolve()):sha(p)for p in files if p.is_file()};return dict(stage='RT_PRICE_and_simulation_settlement',pins=pins,source_identity=digest(pins))
def verify_manifest(m):
 need(digest(m['pins'])==m['source_identity'],'stage manifest identity')
 for p,h in m['pins'].items():need(sha(p)==h,'stage source changed '+p)
def verify_prepared(p):
 m=read(p/'PREPARATION.json');verify_manifest(m['stage_manifest'])
 for f,h in m['artifacts'].items():need(sha(p/f)==h,'PRICE artifact changed')
 for f,h in m['source_pins'].items():need(sha(f)==h,'PRICE current RT input changed')
 return m

def prepare_window(run_root,rt_root,output_root,window):
 root=Path(run_root).resolve();rt=Path(rt_root).resolve();out=Path(output_root).resolve();need(type(window)is int and 0<=window<96,'window0..95');need(rt.is_relative_to(root)and out.is_relative_to(root),'current-run directories only')
 w=rt/f'W{window}';ap=w/'accepted/ACCEPTANCE.json';a=read(ap);ip=w/'prepared/input.json';inp=read(ip);source=rt/'SOURCE_CONTRACT.json';identity=rt/'SOURCE_IDENTITY.json';art=root/'ARTIFACTS.json'
 da=read(art);need(da['schema']=='gd.pipeline.da_artifacts.v1'and da['status']=='accepted','new DA accepted')
 need(a['window']==window and a['KKT']['passed']is True and a['issued_cells']==3,'strict accepted current RT')
 need(a['local_RT_input_sha256']==sha(ip)and a['source_contract_sha256']==sha(source)and a['source_identity_sha256']==read(identity)['sha256'],'same actual RT identity')
 sc=read(source);need(sc['DA_pipeline_run_id']==da['run_id']and sc['DA_receipt_sha256']==sha(art),'same new DA chain')
 pins={str(p):sha(p)for p in (ap,ip,source,identity,art)}
 for z in a['event_pins'].values():
  p=Path(z['path']).resolve();need(p.is_relative_to(root)and sha(p)==z['sha256'],'current RT native occurrence');pins[str(p)]=z['sha256']
 receipt=dict(stage='RT',accepted=True,run_id=inp['projection']['run_id'],source_contract_sha256=sha(source),local_input_content_sha256=digest(inp),window=window,event_id=a['event_id'],request_sha256=a['event_pins']['request']['sha256'],response_sha256=a['event_pins']['response']['sha256'],research_only=True)
 p=out/f'W{window}'/'prepared'
 if p.exists():
  m=verify_prepared(p);need(m['receipt']==receipt,'prepared different run/window');return p
 q,n,t=build_rt_price(inp,receipt);p.mkdir(parents=True)
 for name,z in [('input.json',inp),('template.json',q),('names.json',n),('trace.json',t)]:write(p/name,z)
 m=dict(schema='gd.pipeline.rt_price_prepared.v1',window=window,receipt=receipt,source_pins=pins,stage_manifest=source_manifest(),artifacts={f.name:sha(f)for f in p.iterdir()},native_calls=0);write(p/'PREPARATION.json',m);return p

def review_window(p,native):
 p=Path(p);native=Path(native);m=verify_prepared(p);rp=native.parent/'PRICE_RECEIPT.json'
 if rp.exists():
  z=read(rp);need(z['preparation_sha256']==sha(p/'PREPARATION.json'),'receipt preparation');
  for f,h in z['raw_hashes'].items():need(sha(native/f)==h,'accepted raw changed')
  need(z['source_manifest_identity']==m['stage_manifest']['source_identity'],'receipt source identity');need(z['window']==m['window']and z['run_id']==m['receipt']['run_id']and z['source_contract_sha256']==m['receipt']['source_contract_sha256']and z['local_input_content_sha256']==m['receipt']['local_input_content_sha256'],'same receipt run/source/input');need(z['status']=='verified_research_raw_price'and z['KKT']['passed']is True and z['RT_dual_used']is False,'accepted PRICE only');kr=native.parent/'KKT_PRICE_REVIEW.json';need(sha(kr)==z['KKT_review_sha256']and read(kr)['first_quarter_research_nodal_price_yuan_mwh']==z['raw_prices'],'accepted price review binding');return z
 need((native/'CONFIRMED_NATIVE_RESULT.json').exists(),'native unknown/pending; no resubmission')
 audit=module('_pipeline_frozen_R74_audit',R/'safe_workers/price_review.py').audit
 z=audit(p,native);need(z['status']=='verified_research_raw_price','nonOptimal PRICE remains pending; no fallback')
 r=read(native/'response.json');need(r['backend']=='highs'and r['solver_version']=='1.15.1','native backend')
 for key,value in [('threads',1),('mip_rel_gap',.001),('mip_abs_gap',0)]:need(r['native_settings'][key]==value,'native option')
 rec=dict(schema='gd.pipeline.rt_price_receipt.v1',status=z['status'],window=m['window'],run_id=m['receipt']['run_id'],source_contract_sha256=m['receipt']['source_contract_sha256'],local_input_content_sha256=m['receipt']['local_input_content_sha256'],response_sha256=sha(native/'response.json'),raw_prices=z['first_quarter_research_nodal_price_yuan_mwh'],KKT=dict(passed=True,max_primal_violation=z['max_primal_violation'],max_dual_sign_violation=z['max_dual_sign_violation'],max_complementarity=z['max_complementarity_yuan'],absolute_gap=abs(z['duality_gap_yuan'])),raw_hashes=z['raw_hashes'],preparation_sha256=sha(p/'PREPARATION.json'),source_manifest_identity=m['stage_manifest']['source_identity'],RT_dual_used=False,mathematical_domain_changed=True,source_transport_only_delta=False,market_price=None,settlement_price=None,formal_bill=None)
 # Raw audit inherited transport-only label is corrected in new result, not copied.
 z['source_transport_only_delta']=False;z['mathematical_domain_changed']=True;write(native.parent/'KKT_PRICE_REVIEW.json',z);rec['KKT_review_sha256']=sha(native.parent/'KKT_PRICE_REVIEW.json');write(rp,rec);return rec

def execute_window(prepared,*,execute_native=False):
 p=Path(prepared);m=verify_prepared(p);n=p.parent/'native'
 if n.exists():return review_window(p,n)
 if not execute_native:return dict(status='prepared_dryrun',window=m['window'],native_calls=0)
 need(sha(WORKER)==WORKER_SHA,'frozen PRICE worker');q=read(p/'template.json');o=q['options'];need(q['dual']is True and not q['warmstart']and all(v['kind']=='C'for v in q['variables']),'independent LP')
 need(o['threads']==1 and o['time_limit']==60 and o['mip_rel_gap']==.001 and o['mip_abs_gap']==0,'PRICE options')
 n.mkdir();q['deadline']=time.perf_counter()+60;write(n/'request.json',q);write(n/'IDENTITY.json',dict(schema='gd.pipeline.rt_price_native.v1',request_sha256=sha(n/'request.json'),preparation_sha256=sha(p/'PREPARATION.json'),worker_sha256=WORKER_SHA,window=m['window'],run_id=m['receipt']['run_id'],automatic_retry=False))
 sys.path.insert(0,str(R/'guangdong_after_reset_r23_compressed_20261112/repo/src'));from smm import highs_solver as hs
 with(n/'worker.log').open('xb')as log:
  c=subprocess.Popen([sys.executable,'-B',str(WORKER),str(n/'request.json'),str(n/'response.json')],stdout=log,stderr=log,env={**hs.child_environment(),'PYTHONDONTWRITEBYTECODE':'1'},start_new_session=True);write(n/'launch.json',dict(pid=c.pid,attempt=1,worker_sha256=WORKER_SHA))
  while c.poll()is None:
   if (p.parents[1]/'CANCEL_REQUESTED').exists()or time.perf_counter()>q['deadline']+5:
    os.killpg(c.pid,signal.SIGKILL);c.wait();write(n/'PENDING.json',dict(automatic_retry=False));raise RuntimeError('native pending/stopped; inspect without retry')
   time.sleep(.1)
 need(c.returncode==0 and(n/'response.json').exists(),'native incomplete; no retry');r=read(n/'response.json');need('error'not in r,'native error; no retry')
 write(n/'TRANSPORT_RECEIPT.json',dict(status='optimal_pending_original_KKT' if r.get('status')=='optimal' else 'nonoptimal_not_mathematically_accepted',native_status=r.get('native_status'),mathematical_acceptance=False,response_sha256=sha(n/'response.json'),worker_sha256=WORKER_SHA));
 write(n/'CONFIRMED_NATIVE_RESULT.json',dict(pid=c.pid,returncode=c.returncode,response_sha256=sha(n/'response.json'),status=r['status'],native_status=r['native_status']));return review_window(p,n)

def run_prices(run_root,rt_root,output_root,*,windows=None,execute_native=False):
 """Explicit windows may be partial; only all96 receipts create final manifest."""
 windows=list(range(96))if windows is None else list(windows);need(len(windows)==len(set(windows))and all(type(w)is int and 0<=w<96 for w in windows),'unique scoped windows')
 results=[]
 for w in windows:results.append(execute_window(prepare_window(run_root,rt_root,output_root,w),execute_native=execute_native))
 out=Path(output_root);records=[]
 if all((out/f'W{w}/PRICE_RECEIPT.json').exists()for w in range(96)):
  identity=None
  for w in range(96):
   z=review_window(out/f'W{w}/prepared',out/f'W{w}/native');pair=(z['run_id'],z['source_contract_sha256']);need(identity is None or identity==pair,'mixed PRICE manifest');identity=pair;records.append(dict(window=w,receipt=ref(run_root,out/f'W{w}/PRICE_RECEIPT.json'),response=ref(run_root,out/f'W{w}/native/response.json')))
  man=dict(schema='gd.pipeline.rt_price_manifest.v1',run_id=identity[0],source_contract_sha256=identity[1],records=records,formal=False);mp=out/'PRICE_MANIFEST.json'
  if mp.exists():need(read(mp)==man,'final manifest changed')
  else:write(mp,man)
 return dict(results=results,complete=len(records)==96,manifest=str(out/'PRICE_MANIFEST.json')if len(records)==96 else None)
