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
 verify({str(ROOT/'rt'/name):h for name,h in manifest.items()})
 proc=subprocess.run([sys.executable,'-B',str(B/'trusted_rt_bridge.py'),'registry'],capture_output=True,text=True)
 need(proc.returncode==0,'fixed registry import failed: '+proc.stderr[-500:])
 return json.loads(proc.stdout.strip().splitlines()[-1])
def context(da,run):
 expected=registry();da=Path(da).resolve();run=Path(run).resolve();ctx=read(run/'SHORT_CONTEXT.json')
 need(ctx['schema']=='gd.short.user.context.v2' and ctx.get('start_window')==0 and not any(k in ctx for k in ('migration','recovery_import')),'only new portable start0 contexts; no historical imports')
 need(Path(ctx['selected_DA']).resolve()==da,'wrong selected DA');verify(ctx['seed_pins']);need(ctx['code_pins']==expected,'exact fixed portable producer identity');verify(expected)
 ar=read(da/'ARTIFACTS.json');r=read(run/'seed/request.json');need(ar['run_id']==ctx['selected_DA_run_id'] and ar['stages']['da_sced']['acceptance']['sha256']==r['DA_acceptance_sha256'],'DA binding')
 need(sha(run/'seed/case.json')==r['case_sha256'],'case identity');return da,run,ctx,r

def preflight(run,w):
 need((Path(run)/f'W{w}/actual/ACCEPTED_PAIR.json').exists(),'selected short window not accepted; complete RT SCUC/SCED and accept first')
 registry();p=subprocess.run([sys.executable,str(B/'trusted_rt_bridge.py'),str(run),str(w),'pair'],capture_output=True,text=True)
 try:z=json.loads(p.stdout.strip().splitlines()[-1])
 except Exception:raise ValueError('registered RT reader failed: '+p.stderr[-500:])
 need(p.returncode==0 and z['status']=='accepted','short pair not accepted: '+str(z));return z

def seed_price_inputs(da,run):
 # Independent PRICE from the same accepted DA dispatch, not DA prices/SCED duals.
 from source_contracts import selected_da
 da,run,ctx,r=context(da,run);selected_da(da);warm=read(run/'seed/WARMSTART_SOURCE.json');need(sha(warm['names'])==warm['names_sha256'] and sha(warm['response'])==warm['response_sha256'],'seed DA raw binding')
 case=loads((run/'seed/case.json').read_bytes());x=dict(zip(read(warm['names'])['variables'],read(warm['response'])['primal']))
 for k,v in list(x.items()):
  if k.startswith('battery.charge['):x['battery.power'+k[len('battery.charge'):]]=x['battery.discharge'+k[len('battery.charge'):]]-v
 request=copy.deepcopy(r);cut=datetime.fromisoformat(r['origin'])-timedelta(minutes=15);request.update(window=-1,delivery_tick=0,cutoff=cut.isoformat(),effective_agc_path=str(run/'seed/agc.json'))
 need(all(datetime.fromisoformat(v['available_at'])<=cut and not v['uses_future_actual'] for v in request['sources'].values()),'seed sources not available before first quarter')
 request['seed_price_scope']='Independent first-quarter PRICE from selected accepted DA dispatch; hypothetical issue at origin minus15min; no future RT solution, no copied DA price'
 a=dict(run_id=ctx['run_id'],window=-1,SCED_response_sha256=warm['response_sha256'])
 files=[run/'SHORT_CONTEXT.json',run/'seed/request.json',run/'seed/case.json',run/'seed/agc.json',run/'seed/WARMSTART_SOURCE.json',Path(warm['names']),Path(warm['response']),da/'ARTIFACTS.json']
 return (a,request,x),dict(source_pins={str(p):sha(p)for p in files},formal=False,safety_release=False,seed_scope=request['seed_price_scope']),case

def inputs(da,run,w,*,execution_only=False):
 if w==-1:return seed_price_inputs(da,run)
 da,run,ctx,r=context(da,run);need(type(w)is int and 0<=w<=95,'window0..95; W95 PRICE is next-day, not a D-day quarter');seed=run/'seed';warm=read(seed/'WARMSTART_SOURCE.json');need(sha(warm['names'])==warm['names_sha256'] and sha(warm['response'])==warm['response_sha256'],'selected DA initial prefix')
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
 p=run/f'W{w}/prepared';derived=loads((p/'derived_case.json').read_bytes());agc=read(seed/'agc.json');local=read(p/'agc.json');first=3*(w+1)
 for g in agc:
  for k in ('up_mw','down_mw'):
   for t,v in local[g][k].items():agc[g][k][str(first+int(t))]=v
 cache=run/'price_read_cache';cache.mkdir(exist_ok=True);af=cache/f'W{w}_effective_agc.json'
 if not af.exists():write(af,agc)
 need(read(af)==agc,'effective AGC cache differs from current consumed awards')
 if (p/'AGC_CONSUMPTION.json').exists():
  binding=read(p/'AGC_CONSUMPTION.json');verify(binding['source_pins']);need(binding['locked_prefix_untouched'] and binding['replacement_not_addition'] and not binding['old_SCED_retroactive_claim'],'AGC consumption semantics');need(sha(p/'AGC_CONSUMPTION.json')==read(p/'SUMMARY.json')['AGC_consumption_sha256'],'AGC binding');sourcepins[str(p/'AGC_CONSUMPTION.json')]=sha(p/'AGC_CONSUMPTION.json')
 request=copy.deepcopy(r);request.update(window=w,delivery_tick=first,cutoff=(datetime.fromisoformat(r['origin'])+timedelta(minutes=15*w)).isoformat())
 request['effective_agc_path']=str(af);request['sources']['AGC']={**request['sources']['AGC'],'path':str(af),'sha256':sha(af),'available_at':request['cutoff']}
 request['declared_terminal_by_resource']={u:{k:z[k]for k in ('terminal_at','terminal_energy_mwh')}for u,z in derived['batteries'].items()}
 carry=read(p/'CARRY.json');request['day_declaration_sha256']=sha(carry['day_declaration_path']);need(request['day_declaration_sha256']==carry['day_declaration_sha256'],'day source changed')
 for f in (af,p/'agc.json',p/'CARRY.json',Path(carry['day_declaration_path'])):sourcepins[str(f)]=sha(f)
 accepted=dict(run_id=ctx['run_id'],window=w,SCED_response_sha256=done['response']['raw_sha256'])
 return (accepted,request,x),dict(source_pins=sourcepins,review=reports,formal=False,safety_release=False),case
