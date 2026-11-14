"""Independent selected-DA last48 research boundary, fresh single-use worker."""
from pathlib import Path
import sys,json,subprocess,re,os
import gd_live_control as live
B=Path(__file__).resolve().parent
read,write,sha,need=live.read,live.write,live.sha,live.need

def location(da,name):
 need(re.fullmatch(r'[A-Za-z0-9_-]{1,50}',name or ''),'new name: letters/digits/_/- only');return Path(da).resolve().parent/'tail_research_sessions'/name

def plan(da,name,declaration_name):
 need(isinstance(declaration_name,str)and declaration_name.strip(),'explicit independent research declaration name required');pins=live.sources()
 p=subprocess.run([sys.executable,'-B',str(B/'gd_tail_runtime/worker.py'),'inspect',str(Path(da).resolve())],capture_output=True,text=True)
 need(p.returncode==0,p.stderr or p.stdout or 'selected DA tail source invalid');source=json.loads(p.stdout.strip().splitlines()[-1]);pins.update(source['source_pins'])
 return dict(schema='gd.selected_DA_tail.user_plan.v1',selected_DA=str(Path(da).resolve()),output=str(location(da,name)),declaration_name=declaration_name,source=source,source_pins=pins,maximum_native_calls=3,SCUC=dict(calls=1,seconds=3600,mip_gap=.01,threads=1),SCED=dict(calls=1,seconds=3600,mip_gap=None,solver='simplex',threads=1,run_crossover='off',ipm_optimality_tolerance=1e-12),PRICE=dict(calls=1,seconds=3600,mip_gap=None,threads=1,profile='full_application_thermal_DA_anchor_and_fixed_curve_research',scope='last24-cell lookahead within original last48, not executed quarter or formal settlement'),solver_time_limit_scope='each_native_solve',process_grace_seconds=300,worker_wall_seconds=3900,gap_scope='MIP_only; LP_not_applicable',default_native_calls=0,new_AC_calls=0,not_default_50MW_chain=True,not_official_DA98=True,publication_allowed=False,no_automatic_retry=True,durable_restore=False)

def start(p,*,confirmed=False):
 need(confirmed is True,'explicit source/stop permission/no-deviation boundary and3-call confirmation required');need(p==plan(p['selected_DA'],Path(p['output']).name,p['declaration_name']),'reviewed source or plan changed')
 out=Path(p['output']);out.parent.mkdir(parents=True,exist_ok=True);out.mkdir();write(out/'PLAN.json',p);write(out/'INTENT.json',dict(actual_calls='unknown_until_completion',maximum_native_calls=3,not_retryable=True))
 with(out/'stdout.log').open('ab')as f:proc=subprocess.Popen([sys.executable,'-B',str(B/'gd_tail_runtime/worker.py'),'run',str(out),'--execute-native'],stdout=f,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
 write(out/'PID.json',dict(pid=proc.pid));return dict(status='started_unknown_until_receipt',output=str(out),publication_allowed=False)

def status(out):
 out=Path(out)
 if not out.exists():return dict(status='not_started',native_calls=0)
 p=read(out/'PLAN.json');need(p['output']==str(out.resolve()),'tail output binding');live.sources()
 for f,h in p['source_pins'].items():need(sha(f)==h,'tail source changed')
 z=read(out/'EXIT.json')if(out/'EXIT.json').exists()else dict(status='running_or_unknown_no_retry')
 if(out/'result/price/ACCEPTED.json').exists():
  a=read(out/'result/price/ACCEPTED.json');need(a['context']['short_run']==str((out/'result/core').resolve()),'wrong tail price branch');z['lookahead_raw_prices']=a['raw_prices']
 return dict(**z,publication_allowed=False,not_default_50MW_chain=True,not_executed_settlement=True)
