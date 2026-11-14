"""Fresh PRICE extension of fixed v6; RT/engine originals are never modified."""
from pathlib import Path
import json,hashlib,sys,subprocess,shutil,math,gzip
B=Path(__file__).resolve().parent
from thermal_reference import apply_to_serialized_model
read=lambda p:json.loads(Path(p).read_bytes());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2,allow_nan=False)
def trusted():
 r=read(B/'PRODUCER.json');p=(B/r['package']).resolve();need(sha(p/'FROZEN_MANIFEST.json')==r['manifest_sha256'],'fixed v6 registry drift')
 for n,h in read(p/'FROZEN_MANIFEST.json')['files'].items():need(sha(p/n)==h,'fixed producer source changed')
 return p

def prepare(da,run,window,parameters,output,policy='thermal_DA_DA_previous_quarter_anchor_research'):
 need(policy in ('thermal_DA_DA_previous_quarter_anchor_research','thermal_DA_storage_current','legacy_all_current_comparison','legacy_DA_storage_comparison'),'explicit supported policy')
 p=trusted();da=Path(da).resolve();run=Path(run).resolve();out=Path(output).resolve();need(not out.exists(),'fresh host output only');params=read(parameters)
 wanted='DA_reference_R61_compatible_research'if policy=='legacy_DA_storage_comparison'else'current_sced_research';need(params['domain']==wanted,'parameter domain must match named host policy; storage DA is separate comparison')
 out.mkdir();base=out/'base_prepared';q=subprocess.run([sys.executable,'-B',str(p/'price/program.py'),'prepare','--da',str(da),'--run',str(run),'--window',str(window),'--parameters',str(parameters),'--output',str(base)],capture_output=True,text=True)
 (out/'BASE_PREPARE.log').write_text(q.stdout+'\n'+q.stderr);need(q.returncode==0,'original pure preparation failed, preserve output; no retry');ready=read(base/'READY.json')
 for n,h in ready['artifact_pins'].items():need(sha(base/n)==h,'base artifact changed')
 for f,h in ready['source_pins'].items():need(sha(f)==h,'base source changed')
 model=read(base/'template.json');names=read(base/'names.json');ctx=read(base/'CONTEXT.json');specs={};sourcepins={str(f):sha(f)for f in [base/'READY.json',base/'template.json',base/'CONTEXT.json',base/'names.json',B/'host.py',B/'thermal_reference.py',B/'PRODUCER.json']}
 if policy in ('thermal_DA_DA_previous_quarter_anchor_research','thermal_DA_storage_current'):
  # Fixed original codec and explicit current response; no second producer replay.
  sys.path.insert(0,str(p/'price'));import loader
  need(Path(loader.__file__).resolve()==p/'price/loader.py','another producer loader in process');casefile=run/'seed/case.json';case=loader.loads(casefile.read_bytes());sp=ctx['source_pins'];agp=next(Path(f)for f in sp if f.endswith('agc.json'));ag=read(agp);art=read(da/'ARTIFACTS.json');phys=art['stages']['da_sced']['physical_solution'];php=da/phys['path'];need(sha(php)==phys['sha256'],'DA physical drift');ph=read(php)['physical_cells'];first=ctx['delivery_tick'];x=None
  if window>=0:
   a=run/f'W{window}/actual';rn=run/f'W{window}/prepared/names.json';rp=a/'SCED/response.json.gz';cp=a/'SCED/COMPLETED.json';done=read(cp)['response'];raw=gzip.decompress(rp.read_bytes());need(sha(rp)==done['stored_sha256']and hashlib.sha256(raw).hexdigest()==done['raw_sha256']==ctx['SCED_response_sha256'],'current SCED identity');vals=read(rn)['variables'];pr=json.loads(raw)['primal'];need(len(vals)==len(pr),'current SCED dimensions');x=dict(zip(vals,pr));sourcepins.update({str(f):sha(f)for f in [rn,rp,cp]})
  for g,d in case['energy_inputs']['thermal'].items():
   for j in range(8):
    ts=range(first+3*j,first+3*j+3);avg=lambda a:math.fsum(a)/3;da_ref=avg([ph[t]['thermal'][g]['mean_mw']for t in ts]);current=da_ref if window==-1 else avg([x[f'power[{g},{t}]']for t in range(3*j,3*j+3)])
    specs[f'P[{g},{j}]']=dict(status=params['eligibility'][g][j],current_sced=current,da_sced=da_ref,current_min=avg([d['min_mw'][t]+ag[g]['down_mw'][str(t)]for t in ts]),current_max=avg([d['max_mw'][t]-ag[g]['up_mw'][str(t)]for t in ts]),da_min=avg([d['min_mw'][t]+ph[t]['thermal'][g]['agc_down_reserved_mw']for t in ts]),da_max=avg([d['max_mw'][t]-ph[t]['thermal'][g]['agc_up_reserved_mw']for t in ts]),delta=params['delta'])
  new,trace=apply_to_serialized_model(model,names,specs);sourcepins.update({str(f):sha(f)for f in [casefile,agp,php,da/'ARTIFACTS.json']})
 else:new,trace=model,[]
 ramp_trace=[]
 if policy=='thermal_DA_DA_previous_quarter_anchor_research':
  from ramp_anchor import preceding_anchor,apply_first_ramp,POLICY
  ramp_specs={}
  for g,d in case['energy_inputs']['thermal'].items():
   ticks=range(first,first+3);ramp_specs[f'P[{g},0]']=dict(DA_anchor_MW=preceding_anchor([cell['thermal'][g]['mean_mw']for cell in ph],first,d['initial_power_mw']),up_budget_MW=min(d['ramp_up_mw_per_h'][t]for t in ticks)*.25,down_budget_MW=min(d['ramp_down_mw_per_h'][t]for t in ticks)*.25)
  new,ramp_trace=apply_first_ramp(new,names,ramp_specs,policy=POLICY);sourcepins[str(B/'ramp_anchor.py')]=sha(B/'ramp_anchor.py')
 changed_rows={i for r in ramp_trace for i in [r['up_row'],r['down_row']]}
 need(all(a==b for i,(a,b)in enumerate(zip(new['rows'],model['rows']))if i not in changed_rows)and new['objective']==model['objective'],'only named first-ramp RHS may change');thermal=set(specs);need(all(v==model['variables'][j]for j,v in enumerate(new['variables'])if names['variables'][j]not in thermal),'nonthermal bound changed')
 dest=out/'prepared';dest.mkdir()
 for f in base.iterdir():
  if f.is_file()and f.name not in ('READY.json','template.json','CONTEXT.json'):shutil.copy2(f,dest/f.name)
 ctx['thermal_reference_extension']=dict(policy=policy,thermal_specs=specs,trace=trace,first_ramp_trace=ramp_trace,first_ramp_policy='DA_previous_complete_quarter_research'if ramp_trace else'current_or_original_declared_research',official_first_anchor_certified=False,all_formula_bounds_no_threshold=True,storage_current=(policy!='legacy_DA_storage_comparison'),delta_and_storage_remain_research=True,formal=False);ctx['ramp_anchor_policy']='thermal_DA_reference_plus_DA_preceding_complete_15min_first_ramp_RESEARCH'if ramp_trace else'current_or_original_comparison';ctx['research_interpretation_scope']='PRICE thermal first-ramp anchor not official certification; storage current and delta research';ctx['first_ramp_source']=dict(first=ctx['delivery_tick'],source_cells=list(range(ctx['delivery_tick']-3,ctx['delivery_tick']))if ctx['delivery_tick']else[],seed_boundary='declared_initial_power_at_day_start'if ctx['delivery_tick']==0 else None);ctx['base_boundary_mode']=ctx['boundary_mode'];ctx['boundary_mode']=policy;ctx['price_producer']='fixed_v6_plus_thermal_DA_anchor_host_v9';ctx['source_pins'].update(sourcepins)
 write(dest/'template.json',new);write(dest/'CONTEXT.json',ctx);write(dest/'THERMAL_REFERENCE.json',ctx['thermal_reference_extension']);pins=dict(ready['source_pins']);pins.update(sourcepins)
 for f,h in pins.items():need(sha(f)==h,'source changed during extension')
 write(dest/'READY.json',dict(columns=ready['columns'],rows=ready['rows'],artifact_pins={f.name:sha(f)for f in dest.iterdir()if f.is_file()},source_pins=pins,native_calls=0,formal=False));write(out/'RESULT.json',dict(status='prepared_thermal_reference_extension',policy=policy,prepared=str(dest),base_prepare_calls=1,new_native_calls=0,changed_columns=sum(t['old_bounds']!=tuple(t['new_bounds'])for t in trace),thermal_domains=len(specs),native_requires_explicit_separate_authorization=True));return read(out/'RESULT.json')
if __name__=='__main__':
 import argparse
 a=argparse.ArgumentParser();a.add_argument('--da',required=True);a.add_argument('--run',required=True);a.add_argument('--window',type=int,required=True);a.add_argument('--parameters',required=True);a.add_argument('--output',required=True);a.add_argument('--policy',default='thermal_DA_DA_previous_quarter_anchor_research',choices=['thermal_DA_DA_previous_quarter_anchor_research','thermal_DA_storage_current','legacy_all_current_comparison','legacy_DA_storage_comparison']);x=a.parse_args();print(json.dumps(prepare(x.da,x.run,x.window,x.parameters,x.output,x.policy)))
