"""Portable fixed-registry short-run reader. No receipt-selected code execution."""
from pathlib import Path
from datetime import datetime,timedelta
import json,sys,subprocess,re,copy,math,hashlib
B=Path(__file__).resolve().parent;ROOT=B.parent;LEG=ROOT/'guangdong_rt_price_program_v22_20261112/gd_price'
sys.path.append(str(LEG));sys.path.insert(0,str(LEG/'vendor/guangdong_after_reset_r23_compressed_20261112/repo/src'))
from evidence_io import load
from smm.gd_october_job_codec import loads
from contracts import need,sha
read=lambda p:json.loads(Path(p).read_bytes())
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,sort_keys=True,allow_nan=False,indent=2)
def verify(pins):
 for f,h in pins.items():need(sha(f)==h,'source changed: '+str(f))
def registry():
 manifest=read(B/'TRUSTED_RT.json')
 for name,z in manifest.items():need(sha(ROOT/name)==z['sha256'],'unregistered RT producer '+name)
 return ROOT/'guangdong_rt_short_user_cli_v1_20261112/program.py'
def context(da,run):
 entry=registry();da=Path(da).resolve();run=Path(run).resolve();ctx=read(run/'SHORT_CONTEXT.json');need(ctx['schema']=='gd.short.user.context.v1' and Path(ctx['selected_DA']).resolve()==da,'wrong selected DA/short run');verify(ctx['seed_pins'])
 # Pin inventory is matched by suffix against a fixed bundled registry, not executed.
 expected=read(B/'TRUSTED_RT.json');actual={}
 for path,h in ctx['code_pins'].items():
  matches=[k for k,z in expected.items()if Path(path).as_posix().endswith('/'+k) and z['sha256']==h];need(len(matches)==1,'unknown producer pin');actual[matches[0]]=h;need(sha(path)==h,'run producer bytes changed')
 need(set(actual)==set(expected),'complete176 producer identity')
 ar=read(da/'ARTIFACTS.json');r=read(run/'seed/request.json');need(ar['run_id']==ctx['selected_DA_run_id'] and ar['stages']['da_sced']['acceptance']['sha256']==r['DA_acceptance_sha256'],'DA binding')
 need(sha(run/'seed/case.json')==r['case_sha256'],'case identity');return da,run,ctx,r

def preflight(run,w):
 need((Path(run)/f'W{w}/actual/ACCEPTED_PAIR.json').exists(),'selected short window not accepted; complete RT SCUC/SCED and accept first')
 registry();p=subprocess.run([sys.executable,str(B/'trusted_rt_bridge.py'),str(run),str(w),'pair'],capture_output=True,text=True)
 try:z=json.loads(p.stdout.strip().splitlines()[-1])
 except Exception:raise ValueError('registered RT reader failed: '+p.stderr[-500:])
 need(p.returncode==0 and z['status']=='accepted','short pair not accepted: '+str(z));return z

def inputs(da,run,w,*,execution_only=False):
 da,run,ctx,r=context(da,run);need(type(w)is int and 0<=w<=94,'window0..94');seed=run/'seed';warm=read(seed/'WARMSTART_SOURCE.json');need(sha(warm['names'])==warm['names_sha256'] and sha(warm['response'])==warm['response_sha256'],'selected DA initial prefix')
 v=dict(zip(read(warm['names'])['variables'],read(warm['response'])['primal']));prefix={k:z for k,z in v.items()if (m:=re.search(r'(\d+)\]$',k)) and int(m[1])<3};sourcepins={str(run/'SHORT_CONTEXT.json'):sha(run/'SHORT_CONTEXT.json'),str(da/'ARTIFACTS.json'):sha(da/'ARTIFACTS.json')};case=loads((seed/'case.json').read_bytes());reports=[]
 for i in range(w+1):
  proof=preflight(run,i);p=run/f'W{i}';names=read(p/'prepared/names.json');done=read(p/'actual/SCED/COMPLETED.json');response=load(done['response']);x=dict(prefix);first=3*(i+1)
  for k,z in zip(names['variables'],response['primal']):
   m=re.search(r'(\d+)\]$',k)
   if m:k=k[:m.start(1)]+str(first+int(m[1]))+']'
   x[k]=z
  for k,z in list(x.items()):
   if k.startswith('battery.charge['):x['battery.power'+k[len('battery.charge'):]]=x['battery.discharge'+k[len('battery.charge'):]]-z
  d=loads((p/'prepared/derived_case.json').read_bytes())
  for u,z in d['batteries'].items():
   need(abs(z['initial_energy_mwh']-x[f'battery.energy[{u},{first-1}]'])<=1e-7,'new chain prefix SOC')
   used=copy.deepcopy(case['batteries'][u]['cycle_budget']);eta=math.sqrt(z['roundtrip_efficiency'])
   for t in range(first):
    date=(datetime.fromisoformat(r['origin'])+timedelta(minutes=5*t)).date().isoformat();used[date]['used_internal_mwh']+=(eta*x[f'battery.charge[{u},{t}]']+x[f'battery.discharge[{u},{t}]']/eta)/12
   for date in used:need(abs(used[date]['used_internal_mwh']-z['cycle_budget'][date]['used_internal_mwh'])<=1e-7,'new prefix dated cycles')
  for f in [p/'PREPARED.json',p/'actual/ACCEPTED_PAIR.json',p/'actual/SCED/COMPLETED.json',Path(done['response']['path']),p/'prepared/derived_case.json']:sourcepins[str(f)]=sha(f)
  prefix={k:z for k,z in x.items()if (m:=re.search(r'(\d+)\]$',k)) and int(m[1])<first+3};reports.append(proof)
 request=copy.deepcopy(r);request.update(window=w,delivery_tick=3*(w+1),cutoff=(datetime.fromisoformat(r['origin'])+timedelta(minutes=15*w)).isoformat());accepted=dict(run_id=ctx['run_id'],window=w,SCED_response_sha256=done['response']['raw_sha256'])
 return (accepted,request,x),dict(source_pins=sourcepins,review=reports,formal=False,safety_release=False),case
