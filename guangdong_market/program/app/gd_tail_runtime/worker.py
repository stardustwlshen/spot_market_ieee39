"""Independent DA-tail research entry. No imports from developer experiment directories."""
from pathlib import Path
from datetime import datetime,timedelta
import sys,json,hashlib,subprocess,importlib.util,re,math,uuid
B=Path(__file__).resolve().parent;APP=B.parent;ENGINE=APP.parent/'engines/guangdong_short_unified_v6'
sys.path.insert(0,str(APP));import gd_live_control as gate
read,write,sha,need=gate.read,gate.write,gate.sha,gate.need

def native_engine():
 spec=importlib.util.spec_from_file_location('_tail_fixed_native',ENGINE/'rt/guangdong_rt_short_physical_v1_20261112/run_actual39.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def pair_options(q):
 q['options'].update(time_limit=3600.,threads=1,mip_rel_gap=.01,mip_abs_gap=0.,output_flag=True);return q

def sced_options(lp):
 lp['options'].update(time_limit=3600.,output_flag=True,threads=1,mip_rel_gap=.01,mip_abs_gap=0.,solver='simplex',presolve='off',dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0,run_crossover='off',ipm_optimality_tolerance=1e-12);return lp

def call(args,log):
 p=subprocess.run([sys.executable,'-B',*map(str,args)],capture_output=True,text=True);Path(log).write_text(p.stdout+'\n'+p.stderr);need(p.returncode==0,'stage failed/unknown; no retry: '+str(log));return json.loads(p.stdout.strip().splitlines()[-1])

def snapshot(da,core,actual,*,engine):
 da,core,actual=map(Path,(da,core,actual));ar=read(da/'ARTIFACTS.json');p=core/'prepared';n=read(p/'names.json');uq,u=engine.load(read(actual/'SCUC/JOB.json')['request']),engine.load(read(actual/'SCUC/COMPLETED.json')['response']);eq,ed=engine.load(read(actual/'SCED/JOB.json')['request']),engine.load(read(actual/'SCED/COMPLETED.json')['response'])
 template=read(p/'template.json')
 need(uq['rows']==template['rows']and uq['objective']==template['objective'],'raw SCUC differs from newly prepared tail mathematics')
 need([{k:v for k,v in c.items()if k!='start'}for c in uq['variables']]==[{k:v for k,v in c.items()if k!='start'}for c in template['variables']],'raw SCUC bounds/columns differ from prepared tail')
 engine.checks(uq,u,n,True);engine.checks(eq,ed,n,False)
 fixed=engine.fixed_sced(uq,u['primal'])
 for k in ('variables','rows','objective'):need(eq[k]==fixed[k],'same accepted SCUC/fixed SCED matrix required')
 need(all(abs(v-round(a))<=1e-8 for v,a,col in zip(ed['primal'],u['primal'],uq['variables'])if col['kind']!='C'),'fixed integer mismatch')
 from smm.gd_october_job_codec import loads
 case=loads((core/'seed/case.json').read_bytes());total=len(case['energy_inputs']['horizon']['periods']);need(total==576,'supported complete source576 only')
 dn=read(da/ar['stages']['da_sced']['full_names']['path'])['variables'];dx=read(da/ar['stages']['da_sced']['expanded_response']['path'])['primal'];values=dict(zip(dn,dx));begin=total-48
 for k,v in zip(n['variables'],ed['primal']):
  m=re.search(r',(\d+)\]$',k);key=k[:m.start()]+','+str(begin+int(m[1]))+']'if m else k;values[key]=v
 for k,v in list(values.items()):
  if k.startswith('battery.charge['):values['battery.power'+k[len('battery.charge'):]]=values['battery.discharge'+k[len('battery.charge'):]]-v
 first=total-24;origin=case['contract']['day_start'];cut=(datetime.fromisoformat(origin)+timedelta(minutes=5*(first-3))).isoformat();agc=core/'seed/agc.json';pub=case['contract']['publication_at'];run_id='selected-da-tail-'+uuid.uuid4().hex
 r=dict(read(core/'seed/request.json'));r.update(origin=origin,run_id=run_id,window=first//3-1,delivery_tick=first,cutoff=cut,case_sha256=sha(core/'seed/case.json'),DA_acceptance_sha256=ar['stages']['da_sced']['acceptance']['sha256'],sources={'DA':dict(path=str(da/ar['stages']['da_sced']['acceptance']['path']),available_at=pub,uses_future_actual=False),'AGC':dict(path=str(agc),sha256=sha(agc),available_at=pub,uses_future_actual=False)})
 pins=dict(read(p/'SUMMARY.json')['source_pins']);pins.update({str(f.resolve()):sha(f)for f in [*p.glob('*.json'),*core.glob('*.json'),*[actual/s/f for s in ('SCUC','SCED')for f in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz')],Path(__file__)]});pins.update(gate.sources())
 for f,h in pins.items():need(sha(f)==h,'source changed before PRICE')
 return dict(schema='gd.selected_DA_tail.lookahead_source.v1',source_kind='explicit_tail_no_deviation_boundary_and_accepted_pair_NOT_live50chain',window=first//3-1,base_run=str(core.resolve()),branch=str(core.resolve()),branch_id=run_id,selected_DA=str(da.resolve()),request=r,accepted=dict(run_id=run_id,window=first//3-1,SCED_response_sha256=read(actual/'SCED/COMPLETED.json')['response']['raw_sha256']),values=values,effective_agc=read(agc),source_pins=pins,not_executed_price=True,formal=False,safety_release=False)

def run(out,*,execute_native=False):
 need(execute_native is True,'explicit three-call permission required');out=Path(out).resolve();p=read(out/'PLAN.json');need(p['output']==str(out),'plan/output mismatch');gate.sources()
 for f,h in p['source_pins'].items():need(sha(f)==h,'reviewed source changed')
 from tail_core import prepare
 result=out/'result';result.mkdir();report=prepare(p['selected_DA'],result/'core',declaration_name=p['declaration_name'],research=True);core=result/'core';prep=core/'prepared';actual=result/'actual';actual.mkdir()
 engine=native_engine();q=pair_options(read(prep/'template.json'));names=read(prep/'names.json')
 # Actual accepted DA supplies a warm start only after full candidate primal audit.
 da=Path(p['selected_DA']);ar=read(da/'ARTIFACTS.json');dx=dict(zip(read(da/ar['stages']['da_sced']['full_names']['path'])['variables'],read(da/ar['stages']['da_sced']['expanded_response']['path'])['primal']));v=[]
 for name in names['variables']:
  m=re.search(r',(\d+)\]$',name);key=name[:m.start()]+','+str(int(m[1])+528)+']'if m else name;v.append(dx[key])
 from smm.gd_r23_equivalent_compression import audit
 obj=q['objective']['constant']+math.fsum(a*v[i]for i,a in q['objective']['terms']);proof=audit(q,v,obj);need(proof['max_primal_violation']<=1e-6 and proof['max_integer_violation']<=1e-6,'selected DA warmstart not feasible for this candidate')
 q['warmstart']=True
 for col,value in zip(q['variables'],v):col['start']=value
 write(result/'WARMSTART_REVIEW.json',proof)
 uq,u=engine.native(actual,'SCUC',q,engine.V/'scuc_worker.py',3600.);ua=engine.checks(uq,u,names,True);write(actual/'SCUC_REVIEW.json',ua)
 lp=sced_options(engine.fixed_sced(uq,u['primal']));eq,ed=engine.native(actual,'SCED',lp,ENGINE/'rt/guangdong_rt_short_user_cli_v5_20261112/lp_ipm_worker.py',3600.);ea=engine.checks(eq,ed,names,False);write(actual/'SCED_REVIEW.json',ea)
 sf=result/'PRICE_SOURCE.json';write(sf,snapshot(da,core,actual,engine=engine));profile='fixed_curve_5min_source_mixed_quarter_fixed_RESEARCH_v1'
 call([APP/'gd_live_market_runtime/tail_price_prepare.py',sf,sha(sf),result/'price',profile],result/'price_prepare.log');pp=result/'price/prepared';need(not(pp/'native_once').exists(),'PRICE occurrence already exists; never retry')
 call([ENGINE/'price/program.py','native','--prepared',pp,'--execute-native'],result/'price_native.log');call([ENGINE/'price/program.py','accept','--prepared',pp,'--output',result/'price/ACCEPTED.json'],result/'price_accept.log')
 return dict(status='accepted_independent_tail_research',actual_native_calls=3,SCUC_gap=u['native_mip_gap'],SCED_full_KKT=ea,PRICE_source=str(result/'price/ACCEPTED.json'),not_executed_price=True,safety_release=False)

if __name__=='__main__':
 gate.sources()
 if sys.argv[1]=='inspect':
  from tail_core import inspect_source
  print(json.dumps(inspect_source(sys.argv[2])));sys.exit(0)
 need(sys.argv[1]=='run'and len(sys.argv)==4 and sys.argv[3]=='--execute-native','inspect or explicit run only');out=Path(sys.argv[2])
 try:z=run(out,execute_native=True)
 except Exception as exc:z=dict(status='failed_or_unknown_no_retry',error=str(exc),actual_native_calls=None,maximum_native_calls=3,safety_release=False)
 write(out/'EXIT.json',z)
