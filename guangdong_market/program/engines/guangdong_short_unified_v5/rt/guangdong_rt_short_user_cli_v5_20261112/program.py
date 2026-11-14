"""Stable-boundary short RT research user entry. No automatic unknown-stage retry."""
from pathlib import Path
import sys,json,hashlib,importlib.util,argparse,gzip
from copy import deepcopy
B=Path(__file__).resolve().parent;ROOT=B.parent
sys.path.insert(0,str(ROOT/'guangdong_rt_short_midnight_v1_20261112'));import rolling_midnight as engine
from evidence_io import save,load,write_json
from acceptance import checks
from contracts import fixed_sced
read=lambda p:json.loads(Path(p).read_bytes());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def pins():
 files=[*sorted(B.glob('*.py')),ROOT/'guangdong_short_hour_agc_receipt_v2_20261112/project.py',ROOT/'guangdong_short_hour_agc_receipt_v1_20261112/hour_receipt.py',ROOT/'guangdong_short_hour_agc_receipt_v1_20261112/vendor/adapter.py',ROOT/'guangdong_short_hour_agc_receipt_v1_20261112/vendor/projection.py',*sorted((ROOT/'guangdong_rt_short_midnight_v1_20261112').glob('*.py')),*sorted((ROOT/'guangdong_rt_short_rolling_v2_20261112').glob('*.py')),ROOT/'guangdong_rt_short_selected_da_adapter_v1_20261112/seed.py',ROOT/'guangdong_rt_short_physical_v1_20261112/run_actual39.py',ROOT/'guangdong_rt_short_state_review_20261112/thermal_prefix_reader.py',ROOT/'guangdong_rt_short_state_review_20261112/thermal_tail_reader.py',*sorted((ROOT/'guangdong_rt_recommit_program_v32_20261112').glob('*.py')),*sorted((ROOT/'guangdong_rt_recommit_program_v1_20261112').rglob('*.py')),ROOT/'guangdong_rt_recommit_program_v1_20261112/DEPENDENCIES.json']
 return {str(f):sha(f)for f in files}
def context(run):
 r=Path(run).resolve();c=read(r/'SHORT_CONTEXT.json');need(c['code_pins']==pins(),'short engine source changed')
 for p,h in c['seed_pins'].items():need(sha(p)==h,'seed changed')
 for p,h in c.get('recovery_import',{}).get('source_pins',{}).items():need(sha(p)==h,'explicit recovery source changed')
 return r,c

def seed(da,run,day_declarations):
 spec=importlib.util.spec_from_file_location('_selected_short_DA',ROOT/'guangdong_rt_short_selected_da_adapter_v1_20261112/seed.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);
 import contextlib,io
 with contextlib.redirect_stdout(io.StringIO()):result=m.prepare(da,run)
 r=Path(run).resolve();attach_declarations(r,Path(day_declarations).resolve());q=read(r/'seed/request.json')
 c=dict(schema='gd.short.user.context.v2',run_id=q['run_id'],selected_DA=str(Path(da).resolve()),selected_DA_run_id=read(r/'INITIAL_CARRY.json')['selected_DA_run_id'],code_pins=pins(),seed_pins={str(p):sha(p)for p in [*sorted((r/'seed').glob('*.json')),r/'INITIAL_CARRY.json']},event_model='uniform_research_curves_not_typed',start_window=0,formal=False)
 write_json(r/'SHORT_CONTEXT.json',c);return dict(status='seeded',run_id=c['run_id'],native_calls=0,formal=False)
def attach_declarations(r,path):
 from day_contract import apply
 from smm.gd_october_job_codec import loads
 d=read(path);c=read(r/'INITIAL_CARRY.json');case=loads((r/'seed/case.json').read_bytes());w=read(r/'seed/WARMSTART_SOURCE.json')
 for day in d['days'].values():
  need(day['source']['case_sha256']==sha(r/'seed/case.json')and day['source']['accepted_original_DA_response_sha256']==w['response_sha256'],'day declaration source not selected DA')
 apply(case,c,d)
 c.update(day_declaration_path=str(path),day_declaration_sha256=sha(path));c['source_pins'][str(path)]=sha(path)
 # New run only. Preserve pre-annotation bytes as explicit migration/seed evidence.
 (r/'INITIAL_CARRY_BEFORE_DAY_DECLARATION.json').write_bytes((r/'INITIAL_CARRY.json').read_bytes());(r/'INITIAL_CARRY.json').write_text(json.dumps(c,sort_keys=True))

def verify_migration(r,c):
 import tempfile
 m=c['migration']
 for p,h in m['source_pins'].items():need(sha(p)==h,'imported original source drift')
 with tempfile.TemporaryDirectory(prefix='gd-short-import-proof-')as temp:
  # Original uniform producer, no new declaration injected into old receipts.
  sys.path.insert(0,str(ROOT/'guangdong_rt_short_rolling_v2_20261112'))
  spec=importlib.util.spec_from_file_location('_old_short_replay',ROOT/'guangdong_rt_short_rolling_v2_20261112/rolling.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
  replay=old.carry(m['seed_source'],m['prepared'],m['actual'],Path(temp)/'carry.json')
 need(replay==read(m['carry_source']),'import original carry not reproduced')
 imported=read(r/'INITIAL_CARRY.json');before=read(r/'INITIAL_CARRY_BEFORE_DAY_DECLARATION.json');need(before==replay,'import mutated previous executed/issued/state')
 expected=deepcopy(before);path=imported['day_declaration_path'];expected.update(day_declaration_path=path,day_declaration_sha256=sha(path));expected['source_pins'][path]=sha(path);need(expected==imported,'only explicit day-declaration attachment permitted')
 need(imported['cutoff_tick']==3*c['start_window']and imported['delivery_tick']==3*(c['start_window']+1),'import start clock')

def import_existing(da,run,seed_source,prepared,actual,carry_source,day_declarations):
 import shutil
 r=Path(run).resolve();need(not r.exists(),'fresh import context required');seed_source=Path(seed_source).resolve();prepared=Path(prepared).resolve();actual=Path(actual).resolve();carry_source=Path(carry_source).resolve();da=Path(da).resolve()
 spec=importlib.util.spec_from_file_location('_import_DA_guard',ROOT/'guangdong_rt_recommit_program_v32_20261112/interface_validators.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard);checked=guard.selected_da(da)
 original=read(carry_source);w=read(seed_source/'WARMSTART_SOURCE.json');art=read(da/'ARTIFACTS.json');stage_da=art['stages']['da_sced'];need(sha(da/art['inputs']['case']['path'])==sha(seed_source/'case.json'),'import different selected case');need(w['names_sha256']==stage_da['full_names']['sha256']and w['response_sha256']==stage_da['expanded_response']['sha256'],'import different parent DA witness')
 need(original['cutoff_tick']%3==0,'import quarter boundary');start=original['cutoff_tick']//3;need(0<start<=95,'import start window range')
 r.mkdir();shutil.copytree(seed_source,r/'seed');shutil.copyfile(carry_source,r/'INITIAL_CARRY.json');attach_declarations(r,Path(day_declarations).resolve());q=read(r/'seed/request.json')
 migration=dict(seed_source=str(seed_source),prepared=str(prepared),actual=str(actual),carry_source=str(carry_source),source_pins={**checked,**{str(p):sha(p)for p in [carry_source,prepared/'SUMMARY.json',prepared/'CARRY.json',actual/'ACCEPTED_PAIR.json',actual/'SCED/response.json.gz',*sorted(seed_source.glob('*.json'))]}},replay_native_calls=0)
 c=dict(schema='gd.short.user.context.v2',run_id=q['run_id'],selected_DA=str(da),selected_DA_run_id=art['run_id'],code_pins=pins(),seed_pins={str(p):sha(p)for p in [*sorted((r/'seed').glob('*.json')),r/'INITIAL_CARRY.json',r/'INITIAL_CARRY_BEFORE_DAY_DECLARATION.json']},event_model='uniform_research_curves_not_typed',start_window=start,migration=migration,formal=False)
 verify_migration(r,c);write_json(r/'SHORT_CONTEXT.json',c);return dict(status='imported_verified_short_boundary',start_window=start,executed_cells=original['executed_end_exclusive'],issued_cells=original['issued_end_exclusive'],native_calls=0,formal=False)

def current_carry(r,c,w):
 need(type(w)is int and c.get('start_window',0)<=w<=95,'window before imported start or beyond96; D+1 declarations separately required')
 if w==c.get('start_window',0):
  if 'migration' in c:verify_migration(r,c)
  return r/'INITIAL_CARRY.json'
 h=read(r/f'handoff/W{w}/HANDOFF.json');p=r/f'handoff/W{w}/CARRY.json';need(h['window']==w and h['run_id']==c['run_id']and h['context_sha256']==sha(r/'SHORT_CONTEXT.json'),'handoff run/context/window');need(h['carry_sha256']==sha(p),'handoff carry hash');need(h['previous_acceptance_sha256']==sha(r/f'W{w-1}/actual/ACCEPTED_PAIR.json'),'handoff predecessor changed')
 for f,hh in h['source_pins'].items():need(sha(f)==hh,'handoff source drift')
 # Recompute complete predicted boundary, not only its self-declared digest.
 tmp=r/f'handoff/W{w}/CARRY.json';old=read(tmp);source=r/f'W{w-1}';
 import tempfile
 with tempfile.TemporaryDirectory(prefix='gd-short-pure-replay-')as temp:
  reproduced=engine.carry(r/'seed',source/'prepared',source/'actual',Path(temp)/'carry.json')
 need(old==reproduced,'handoff complete boundary differs from accepted predecessor replay')
 need(old['cutoff_tick']==w*3 and old['delivery_tick']==(w+1)*3,'carry clock')
 need(old['seed_case_sha256']==sha(r/'seed/case.json')and old['seed_request_sha256']==sha(r/'seed/request.json'),'carry seed identity')
 return p

def build(run,w,agc_projection=None):
 r,c=context(run);cp=current_carry(r,c,w);out=r/f'W{w}';need(not out.exists(),'window exists; inspect preflight instead of rebuild');out.mkdir();z=engine.build(r/'seed',cp,out/'prepared',agc_projection=agc_projection);__import__('warm_hint').attach(out/'prepared');write_json(out/'PREPARED.json',dict(schema='gd.short.user.prepared.v1',run_id=c['run_id'],window=w,context_sha256=sha(r/'SHORT_CONTEXT.json'),carry_path=str(cp),carry_sha256=sha(cp),artifact_pins={str(p):sha(p)for p in sorted((out/'prepared').glob('*.json'))},formal=False));return dict(status='prepared',window=w,native_calls=0,columns=z['columns'],rows=z['rows'],formal=False)
def prepared(r,c,w):
 p=r/f'W{w}';t=read(p/'PREPARED.json');need(t['run_id']==c['run_id']and t['window']==w and t['context_sha256']==sha(r/'SHORT_CONTEXT.json'),'prepared binding');cp=current_carry(r,c,w);need(t['carry_path']==str(cp)and t['carry_sha256']==sha(cp),'prepared boundary binding')
 for f,h in t['artifact_pins'].items():need(sha(f)==h,'prepared bytes changed')
 s=read(p/'prepared/SUMMARY.json')
 for f,h in s['source_pins'].items():need(sha(f)==h,'prepared producer drift')
 return p,t,read(p/'prepared/template.json'),read(p/'prepared/names.json')
def stage(p,name,q,n,mip):
 d=p/'actual'/name
 if not d.exists():return None
 need((d/'COMPLETED.json').exists()and(d/'JOB.json').exists(),'unknown native occurrence; no retry '+name)
 job=read(d/'JOB.json');done=read(d/'COMPLETED.json');expected_worker=(engine.worker.V/'scuc_worker.py' if mip else B/'lp_ipm_worker.py');need(Path(job['worker'])==expected_worker and sha(expected_worker)==job['worker_sha256'],'worker binding');need(Path(job['request']['path']).resolve()==(d/'request.json.gz').resolve()and Path(job['response_path']).resolve()==(d/'response.json.gz').resolve()and Path(done['response']['path']).resolve()==(d/'response.json.gz').resolve(),'native canonical request/response paths');need(done['worker_sha256']==job['worker_sha256']and done['solve_entries']==1,'completion worker/counter binding');actual=load(job['request']);r=load(done['response']);need(all(r['native_settings'][k]==v for k,v in [('threads',1),('mip_rel_gap',.001),('mip_abs_gap',0.)]),'actual solver settings');expected=deepcopy(q);expected['options']['time_limit']=300. if mip else 120.
 need({k:v for k,v in actual.items()if k!='deadline'}=={k:v for k,v in expected.items()if k!='deadline'},'actual request differs from selected prepared model')
 need(r['optimize_calls']==1 and r['transport_solve_entries']==1,'native call count');checks(actual,r,n,mip);return actual,r

def expected_acceptance(r,c,p,w,n,u,e):
 a=dict(schema='gd.short.user.accepted.v1',status='accepted_research_pair',run_id=c['run_id'],window=w,context_sha256=sha(r/'SHORT_CONTEXT.json'),prepared_sha256=sha(p/'PREPARED.json'),SCUC_response_sha256=sha(p/'actual/SCUC/response.json.gz'),SCED_response_sha256=sha(p/'actual/SCED/response.json.gz'),SCUC_objective=u[1]['objective'],SCUC_gap=u[1]['native_mip_gap'],SCED_objective=e[1]['objective'],SCUC_audit=checks(*u,n,True),SCED_audit=checks(*e,n,False),domain_start=read(p/'prepared/SUMMARY.json')['domain_start'],domain_end=read(p/'prepared/SUMMARY.json')['domain_end'],formal=False,safety_release=False,native_calls=2)
 return a

def preflight(run,w):
 r,c=context(run);p,t,q,n=prepared(r,c,w);u=stage(p,'SCUC',q,n,True);statuses={'SCUC':'completed'if u else'pending','SCED':'pending'};e=None
 if u:
  lp=fixed_sced(q,u[1]['primal']);lp['options'].update(presolve='off',dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0,solver='ipm',run_crossover='off',ipm_optimality_tolerance=1e-12);e=stage(p,'SCED',lp,n,False);statuses['SCED']='completed'if e else'pending'
 else:need(not(p/'actual/SCED').exists(),'SCED exists without accepted SCUC')
 accepted=p/'actual/ACCEPTED_PAIR.json';status='ready'
 if accepted.exists():
  need(u is not None and e is not None,'accepted without completed pair');a=read(accepted);need(a['context_sha256']==sha(r/'SHORT_CONTEXT.json')and a['prepared_sha256']==sha(p/'PREPARED.json')and a['window']==w and a['run_id']==c['run_id'],'acceptance context/window');need(a==expected_acceptance(r,c,p,w,n,u,e),'accepted fields differ from verified raw pair');status='accepted'
 return dict(status=status,window=w,native_calls=0,stage_status=statuses,pending_stages=[k for k,v in statuses.items()if v=='pending'],context_sha256=sha(r/'SHORT_CONTEXT.json'),prepared_sha256=sha(p/'PREPARED.json'),source_identity=c['selected_DA_run_id'],formal=False,safety_release=False)
def accept(run,w):
 proof=preflight(run,w);need(not proof['pending_stages'],'incomplete native pair');r,c=context(run);p,t,q,n=prepared(r,c,w)
 if proof['status']=='accepted':return proof
 u=stage(p,'SCUC',q,n,True);lp=fixed_sced(q,u[1]['primal']);lp['options'].update(presolve='off',dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0,solver='ipm',run_crossover='off',ipm_optimality_tolerance=1e-12);e=stage(p,'SCED',lp,n,False)
 a=expected_acceptance(r,c,p,w,n,u,e)
 write_json(p/'actual/ACCEPTED_PAIR.json',a);return preflight(run,w)
def run_one(run,w,enabled):
 f=preflight(run,w)
 if not enabled or f['status']=='accepted':return f
 r,c=context(run);p,t,q,n=prepared(r,c,w);out=p/'actual';out.mkdir(exist_ok=True)
 calls=0
 if f['stage_status']['SCUC']=='pending':engine.worker.native(out,'SCUC',q,engine.worker.V/'scuc_worker.py',300.);calls+=1
 u=stage(p,'SCUC',q,n,True);lp=fixed_sced(q,u[1]['primal']);lp['options'].update(presolve='off',dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0,solver='ipm',run_crossover='off',ipm_optimality_tolerance=1e-12)
 if f['stage_status']['SCED']=='pending':engine.worker.native(out,'SCED',lp,B/'lp_ipm_worker.py',120.);calls+=1
 result=accept(run,w);result['native_calls']=calls;return result
def handoff(run,w,simulate):
 need(simulate,'explicit --simulate-no-deviation required');proof=preflight(run,w);need(proof['status']=='accepted','accepted pair required');r,c=context(run);p=r/f'W{w}';d=r/f'handoff/W{w+1}';need(not d.exists(),'handoff exists; never overwrite');d.mkdir(parents=True);cp=d/'CARRY.json';engine.carry(r/'seed',p/'prepared',p/'actual',cp);z=read(cp);need(z['cutoff_tick']==(w+1)*3 and z['delivery_tick']==(w+2)*3,'handoff clock');write_json(d/'HANDOFF.json',dict(run_id=c['run_id'],window=w+1,context_sha256=sha(r/'SHORT_CONTEXT.json'),carry_sha256=sha(cp),previous_acceptance_sha256=sha(p/'actual/ACCEPTED_PAIR.json'),source_pins=z['source_pins'],formal=False));return dict(status='counterfactual_handoff',window=w+1,executed_cells=z['executed_end_exclusive'],issued_cells=z['issued_end_exclusive'],native_calls=0,formal=False)
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['seed','import','build','preflight','run','accept','handoff']);p.add_argument('--run',required=True);p.add_argument('--da');p.add_argument('--agc-projection');p.add_argument('--day-declarations');p.add_argument('--seed-source');p.add_argument('--previous-prepared');p.add_argument('--previous-actual');p.add_argument('--carry-source');p.add_argument('--window',type=int,default=0);p.add_argument('--execute-native',action='store_true');p.add_argument('--simulate-no-deviation',action='store_true');a=p.parse_args()
 try:
  z=import_existing(a.da,a.run,a.seed_source,a.previous_prepared,a.previous_actual,a.carry_source,a.day_declarations)if a.action=='import'else seed(a.da,a.run,a.day_declarations)if a.action=='seed'else build(a.run,a.window,a.agc_projection)if a.action=='build'else preflight(a.run,a.window)if a.action=='preflight'else run_one(a.run,a.window,a.execute_native)if a.action=='run'else accept(a.run,a.window)if a.action=='accept'else handoff(a.run,a.window,a.simulate_no_deviation);print(json.dumps(z))
 except Exception as e:print(json.dumps(dict(status='blocked',reason=str(e),window=a.window,native_calls=0 if a.action!='run'or not a.execute_native else'check retained stage receipts; no retry')));raise SystemExit(1)
if __name__=='__main__':main()
