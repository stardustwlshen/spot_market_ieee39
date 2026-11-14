"""Stable-boundary short RT research user entry. No automatic unknown-stage retry."""
from pathlib import Path
import sys,json,hashlib,importlib.util,argparse,gzip
from copy import deepcopy
B=Path(__file__).resolve().parent;ROOT=B.parent
sys.path.insert(0,str(ROOT/'guangdong_rt_short_rolling_v2_20261112'));import rolling as engine
from evidence_io import save,load,write_json
from acceptance import checks
from contracts import fixed_sced
read=lambda p:json.loads(Path(p).read_bytes());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def pins():
 files=[Path(__file__),*sorted((ROOT/'guangdong_rt_short_rolling_v2_20261112').glob('*.py')),ROOT/'guangdong_rt_short_selected_da_adapter_v1_20261112/seed.py',ROOT/'guangdong_rt_short_physical_v1_20261112/run_actual39.py',ROOT/'guangdong_rt_short_state_review_20261112/thermal_prefix_reader.py',ROOT/'guangdong_rt_short_state_review_20261112/thermal_tail_reader.py',*sorted((ROOT/'guangdong_rt_recommit_program_v32_20261112').glob('*.py')),*sorted((ROOT/'guangdong_rt_recommit_program_v1_20261112').rglob('*.py')),ROOT/'guangdong_rt_recommit_program_v1_20261112/DEPENDENCIES.json']
 return {str(f):sha(f)for f in files}
def context(run):
 r=Path(run).resolve();c=read(r/'SHORT_CONTEXT.json');need(c['code_pins']==pins(),'short engine source changed')
 for p,h in c['seed_pins'].items():need(sha(p)==h,'seed changed')
 return r,c

def seed(da,run):
 spec=importlib.util.spec_from_file_location('_selected_short_DA',ROOT/'guangdong_rt_short_selected_da_adapter_v1_20261112/seed.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);
 import contextlib,io
 with contextlib.redirect_stdout(io.StringIO()):result=m.prepare(da,run)
 r=Path(run).resolve();q=read(r/'seed/request.json')
 c=dict(schema='gd.short.user.context.v1',run_id=q['run_id'],selected_DA=str(Path(da).resolve()),selected_DA_run_id=read(r/'INITIAL_CARRY.json')['selected_DA_run_id'],code_pins=pins(),seed_pins={str(p):sha(p)for p in [*sorted((r/'seed').glob('*.json')),r/'INITIAL_CARRY.json']},event_model='uniform_research_curves_not_typed',formal=False)
 write_json(r/'SHORT_CONTEXT.json',c);return dict(status='seeded',run_id=c['run_id'],native_calls=0,formal=False)
def current_carry(r,c,w):
 need(type(w)is int and 0<=w<=94,'window 0..94 with complete four-hour source coverage; future last-day extension unsupported')
 if w==0:return r/'INITIAL_CARRY.json'
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

def build(run,w):
 r,c=context(run);cp=current_carry(r,c,w);out=r/f'W{w}';need(not out.exists(),'window exists; inspect preflight instead of rebuild');out.mkdir();z=engine.build(r/'seed',cp,out/'prepared');write_json(out/'PREPARED.json',dict(schema='gd.short.user.prepared.v1',run_id=c['run_id'],window=w,context_sha256=sha(r/'SHORT_CONTEXT.json'),carry_path=str(cp),carry_sha256=sha(cp),artifact_pins={str(p):sha(p)for p in sorted((out/'prepared').glob('*.json'))},formal=False));return dict(status='prepared',window=w,native_calls=0,columns=z['columns'],rows=z['rows'],formal=False)
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
 job=read(d/'JOB.json');done=read(d/'COMPLETED.json');expected_worker=engine.worker.V/('scuc_worker.py'if mip else'lp_worker.py');need(Path(job['worker'])==expected_worker and sha(expected_worker)==job['worker_sha256'],'worker binding');need(Path(job['request']['path']).resolve()==(d/'request.json.gz').resolve()and Path(job['response_path']).resolve()==(d/'response.json.gz').resolve()and Path(done['response']['path']).resolve()==(d/'response.json.gz').resolve(),'native canonical request/response paths');need(done['worker_sha256']==job['worker_sha256']and done['solve_entries']==1,'completion worker/counter binding');actual=load(job['request']);r=load(done['response']);need(all(r['native_settings'][k]==v for k,v in [('threads',1),('mip_rel_gap',.001),('mip_abs_gap',0.)]),'actual solver settings');expected=deepcopy(q);expected['options']['time_limit']=300. if mip else 120.
 need({k:v for k,v in actual.items()if k!='deadline'}=={k:v for k,v in expected.items()if k!='deadline'},'actual request differs from selected prepared model')
 need(r['optimize_calls']==1 and r['transport_solve_entries']==1,'native call count');checks(actual,r,n,mip);return actual,r

def expected_acceptance(r,c,p,w,n,u,e):
 a=dict(schema='gd.short.user.accepted.v1',status='accepted_research_pair',run_id=c['run_id'],window=w,context_sha256=sha(r/'SHORT_CONTEXT.json'),prepared_sha256=sha(p/'PREPARED.json'),SCUC_response_sha256=sha(p/'actual/SCUC/response.json.gz'),SCED_response_sha256=sha(p/'actual/SCED/response.json.gz'),SCUC_objective=u[1]['objective'],SCUC_gap=u[1]['native_mip_gap'],SCED_objective=e[1]['objective'],SCUC_audit=checks(*u,n,True),SCED_audit=checks(*e,n,False),domain_start=read(p/'prepared/SUMMARY.json')['domain_start'],domain_end=read(p/'prepared/SUMMARY.json')['domain_end'],formal=False,safety_release=False,native_calls=2)
 return a

def preflight(run,w):
 r,c=context(run);p,t,q,n=prepared(r,c,w);u=stage(p,'SCUC',q,n,True);statuses={'SCUC':'completed'if u else'pending','SCED':'pending'};e=None
 if u:
  lp=fixed_sced(q,u[1]['primal']);lp['options'].update(dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0);e=stage(p,'SCED',lp,n,False);statuses['SCED']='completed'if e else'pending'
 else:need(not(p/'actual/SCED').exists(),'SCED exists without accepted SCUC')
 accepted=p/'actual/ACCEPTED_PAIR.json';status='ready'
 if accepted.exists():
  need(u is not None and e is not None,'accepted without completed pair');a=read(accepted);need(a['context_sha256']==sha(r/'SHORT_CONTEXT.json')and a['prepared_sha256']==sha(p/'PREPARED.json')and a['window']==w and a['run_id']==c['run_id'],'acceptance context/window');need(a==expected_acceptance(r,c,p,w,n,u,e),'accepted fields differ from verified raw pair');status='accepted'
 return dict(status=status,window=w,native_calls=0,stage_status=statuses,pending_stages=[k for k,v in statuses.items()if v=='pending'],context_sha256=sha(r/'SHORT_CONTEXT.json'),prepared_sha256=sha(p/'PREPARED.json'),source_identity=c['selected_DA_run_id'],formal=False,safety_release=False)
def accept(run,w):
 proof=preflight(run,w);need(not proof['pending_stages'],'incomplete native pair');r,c=context(run);p,t,q,n=prepared(r,c,w)
 if proof['status']=='accepted':return proof
 u=stage(p,'SCUC',q,n,True);lp=fixed_sced(q,u[1]['primal']);lp['options'].update(dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0);e=stage(p,'SCED',lp,n,False)
 a=expected_acceptance(r,c,p,w,n,u,e)
 write_json(p/'actual/ACCEPTED_PAIR.json',a);return preflight(run,w)
def run_one(run,w,enabled):
 f=preflight(run,w)
 if not enabled or f['status']=='accepted':return f
 r,c=context(run);p,t,q,n=prepared(r,c,w);out=p/'actual';out.mkdir(exist_ok=True)
 calls=0
 if f['stage_status']['SCUC']=='pending':engine.worker.native(out,'SCUC',q,engine.worker.V/'scuc_worker.py',300.);calls+=1
 u=stage(p,'SCUC',q,n,True);lp=fixed_sced(q,u[1]['primal']);lp['options'].update(dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0)
 if f['stage_status']['SCED']=='pending':engine.worker.native(out,'SCED',lp,engine.worker.V/'lp_worker.py',120.);calls+=1
 result=accept(run,w);result['native_calls']=calls;return result
def handoff(run,w,simulate):
 need(simulate,'explicit --simulate-no-deviation required');proof=preflight(run,w);need(proof['status']=='accepted','accepted pair required');r,c=context(run);p=r/f'W{w}';d=r/f'handoff/W{w+1}';need(not d.exists(),'handoff exists; never overwrite');d.mkdir(parents=True);cp=d/'CARRY.json';engine.carry(r/'seed',p/'prepared',p/'actual',cp);z=read(cp);need(z['cutoff_tick']==(w+1)*3 and z['delivery_tick']==(w+2)*3,'handoff clock');write_json(d/'HANDOFF.json',dict(run_id=c['run_id'],window=w+1,context_sha256=sha(r/'SHORT_CONTEXT.json'),carry_sha256=sha(cp),previous_acceptance_sha256=sha(p/'actual/ACCEPTED_PAIR.json'),source_pins=z['source_pins'],formal=False));return dict(status='counterfactual_handoff',window=w+1,executed_cells=z['executed_end_exclusive'],issued_cells=z['issued_end_exclusive'],native_calls=0,formal=False)
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['seed','build','preflight','run','accept','handoff']);p.add_argument('--run',required=True);p.add_argument('--da');p.add_argument('--window',type=int,default=0);p.add_argument('--execute-native',action='store_true');p.add_argument('--simulate-no-deviation',action='store_true');a=p.parse_args()
 try:
  z=seed(a.da,a.run)if a.action=='seed'else build(a.run,a.window)if a.action=='build'else preflight(a.run,a.window)if a.action=='preflight'else run_one(a.run,a.window,a.execute_native)if a.action=='run'else accept(a.run,a.window)if a.action=='accept'else handoff(a.run,a.window,a.simulate_no_deviation);print(json.dumps(z))
 except Exception as e:print(json.dumps(dict(status='blocked',reason=str(e),window=a.window,native_calls=0 if a.action!='run'or not a.execute_native else'check retained stage receipts; no retry')));raise SystemExit(1)
if __name__=='__main__':main()
