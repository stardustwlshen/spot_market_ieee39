"""Read-only fixed registered RT implementation bridge; no optimizer entry."""
from pathlib import Path
import importlib.util,sys,json,tempfile
ROOT=Path(__file__).resolve().parent.parent
p=ROOT/'guangdong_rt_short_user_cli_v1_20261112/program.py'
s=importlib.util.spec_from_file_location('fixed_registered_rt',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
r=Path(sys.argv[1]).resolve();w=int(sys.argv[2]);mode=sys.argv[3];c=m.read(r/'SHORT_CONTEXT.json')
def carry(k):
 if k==0:return r/'INITIAL_CARRY.json'
 hp=r/f'handoff/W{k}/HANDOFF.json';h=m.read(hp);cp=hp.parent/'CARRY.json';m.need(h['window']==k and h['run_id']==c['run_id']and h['context_sha256']==m.sha(r/'SHORT_CONTEXT.json')and h['carry_sha256']==m.sha(cp)and h['previous_acceptance_sha256']==m.sha(r/f'W{k-1}/actual/ACCEPTED_PAIR.json'),'handoff identity')
 for f,z in h['source_pins'].items():m.need(m.sha(f)==z,'handoff source')
 with tempfile.TemporaryDirectory(prefix='price-readonly-carry-')as tmp:expected=m.engine.carry(r/'seed',r/f'W{k-1}/prepared',r/f'W{k-1}/actual',Path(tmp)/'carry.json')
 m.need(m.read(cp)==expected,'complete replayed boundary');return cp
def stage(p,name,q,n,mip):
 from copy import deepcopy
 d=p/'actual'/name;job=m.read(d/'JOB.json');done=m.read(d/'COMPLETED.json');worker=m.engine.worker.V/('scuc_worker.py'if mip else'lp_worker.py')
 m.need(job['worker'] in c['code_pins'] and c['code_pins'][job['worker']]==job['worker_sha256']==m.sha(worker)==done['worker_sha256'],'registered worker content')
 m.need(Path(job['request']['path']).resolve()==(d/'request.json.gz').resolve() and Path(done['response']['path']).resolve()==(d/'response.json.gz').resolve(),'canonical artifact paths')
 actual=m.load(job['request']);response=m.load(done['response']);expected=deepcopy(q);expected['options']['time_limit']=300. if mip else 120.
 m.need({k:v for k,v in actual.items()if k!='deadline'}=={k:v for k,v in expected.items()if k!='deadline'},'actual prepared math')
 m.need(response['optimize_calls']==response['transport_solve_entries']==done['solve_entries']==1,'single actual occurrence');m.checks(actual,response,n,mip);return actual,response
if mode=='carry':print(json.dumps(dict(status='accepted',carry=m.read(carry(w)),native_calls=0)));raise SystemExit
p=r/f'W{w}';t=m.read(p/'PREPARED.json');m.need(t['run_id']==c['run_id']and t['window']==w and t['context_sha256']==m.sha(r/'SHORT_CONTEXT.json'),'prepared identity');cp=carry(w);m.need(Path(t['carry_path']).resolve()==cp.resolve()and t['carry_sha256']==m.sha(cp),'prepared carry')
for f,h in t['artifact_pins'].items():m.need(m.sha(f)==h,'prepared bytes')
for f,h in m.read(p/'prepared/SUMMARY.json')['source_pins'].items():m.need(m.sha(f)==h,'producer bytes')
q=m.read(p/'prepared/template.json');n=m.read(p/'prepared/names.json');u=stage(p,'SCUC',q,n,True);m.need(u is not None,'pending SCUC');lp=m.fixed_sced(q,u[1]['primal']);lp['options'].update(dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0);e=stage(p,'SCED',lp,n,False);m.need(e is not None,'pending SCED');a=m.read(p/'actual/ACCEPTED_PAIR.json');expected=m.expected_acceptance(r,c,p,w,n,u,e);m.need(a==expected,'actual accepted fields')
print(json.dumps(dict(status='accepted',window=w,native_calls=0,KKT=a['SCED_audit'].get('KKT'),SCED_response_sha256=a['SCED_response_sha256'])))
