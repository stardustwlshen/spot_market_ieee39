"""Installed PRICE2.2 only; no old engine flags or receipt-directed code execution."""
from pathlib import Path
import json,hashlib,subprocess,sys,os,time,shutil
B=Path(__file__).resolve().parent
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(v,m):
 if not v:raise ValueError(m)
def write(p,z):
 with Path(p).open('x') as f:json.dump(z,f,ensure_ascii=False,indent=2)
def profile(path=None):
 z=read(path or B/'PRICE_INSTALLATION.json')
 if z.get('schema')=='gd.price.ui.installed_profile.v2':
  need(z.get('python_mode')=='current_interpreter','未支持的Python绑定')
  z={**z,'installation_root':str((B/z['installation_relative_to_app']).resolve()),'python':sys.executable}
 root=Path(z['installation_root']).resolve();need(Path(z['python']).is_file(),'Python不可用')
 for rel,h in z['package_pins'].items():need(sha(root/rel)==h,'安装源码变化：'+rel)
 return z

def invoke(z,args,log,timeout=180):
 env={**os.environ,'PYTHONPATH':z['installation_root'],'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'};cmd=[z['python'],'-B','-m','gd_price.cli',*map(str,args)]
 with Path(log).open('x')as f:
  try:r=subprocess.run(cmd,stdout=f,stderr=f,env=env,timeout=timeout,check=False)
  except subprocess.TimeoutExpired:raise RuntimeError('命令超时，结果不明；保留输出与日志，禁止直接重跑')
 need(r.returncode==0,'命令未通过，请检查日志：'+str(log));return cmd

def preflight(da_run,rt_run,window,parameters,output,boundary,installation=None):
 z=profile(installation);da=Path(da_run).resolve();rt=Path(rt_run).resolve();pair=rt/f'W{int(window)}/ACCEPTED_PAIR.json';out=Path(output).resolve();need(boundary in ['DA_reference_R61_compatible_research','current_sced_research'],'定价域未明确')
 a=read(da/'ARTIFACTS.json');c=read(rt/'CONTEXT.json');need(pair.is_file(),'目标实时窗口尚未接受，不准定价');need(not out.exists(),'必须使用全新输出目录')
 if (rt/'seed/case.json').exists():case=rt/'seed/case.json';agc=rt/'seed/agc.json'
 else:
  # Exact source data may be supplied from selected DA, not an arbitrary historical default.
  case=da/a['inputs']['case']['path'];agc=da/a['inputs']['agc']['path']
 pars=Path(parameters).resolve();p=read(pars);need(p['mode']=='research','本入口仅研究参数，不具备正式发布资格');need(read(pair)['run_id']==c['run_id'],'运行与接受票据不符')
 base=out.parent
 while not base.exists():base=base.parent
 need(shutil.disk_usage(base).free>=256*1024**2,'可用空间低于256MiB，暂不准备')
 pins={str(f):sha(f)for f in [da/'ARTIFACTS.json',rt/'CONTEXT.json',pair,case,agc,pars]}
 return dict(da_run=str(da),rt_run=str(rt),window=int(window),parameters=str(pars),output=str(out),boundary=boundary,pins=pins,installation=z,case=str(case),agc=str(agc),pair=str(pair),native_budget_seconds=60,worker_wait_limit_seconds=90,native_calls=0)

def prepare(contract):
 for f,h in contract['pins'].items():need(sha(f)==h,'预检后输入变化，请重新预检')
 z=contract['installation'];out=Path(contract['output']);need(not out.exists(),'准备目录已存在，不覆盖');out.parent.mkdir(parents=True,exist_ok=True)
 log=out.parent/(out.name+'.prepare.log');need(not log.exists(),'本次准备已有日志，不自动重试')
 invoke(z,['prepare','--pair',contract['pair'],'--case',contract['case'],'--agc',contract['agc'],'--da-run',contract['da_run'],'--parameters',contract['parameters'],'--output',out,'--boundary',contract['boundary']],log)
 write(out/'UI_PREPARE.json',contract);return status(out)

def status(prepared):
 p=Path(prepared).resolve();ready=read(p/'READY.json')
 for f,h in ready['artifact_pins'].items():need(sha(p/f)==h,'准备内容已改变')
 for f,h in ready['source_pins'].items():need(sha(f)==h,'原求解源码/来源已改变')
 native=p/'native_once';lock=p/'UI_NATIVE_ONCE';done=native/'COMPLETED.json';state='已准备，尚未实际求解'
 if done.exists():state='原生完成；须独立数学验收'
 elif native.exists() or lock.exists():state='运行中或结果不明，禁止重复启动'
 return dict(state=state,prepared=str(p),context=read(p/'CONTEXT.json'),columns=ready['columns'],rows=ready['rows'],completed=done.exists(),may_start=not native.exists()and not lock.exists(),formal_bill=None,safety_release=False)

def start_native(prepared,confirmed,installation=None):
 need(confirmed,'必须明确确认一次研究PRICE求解');z=profile(installation);p=Path(prepared).resolve();s=status(p);need(s['may_start'],'已有尝试或结果不明，禁止重跑')
 lock=p/'UI_NATIVE_ONCE';lock.mkdir(exist_ok=False);job=dict(installation=z,args=['native','--prepared',str(p),'--execute-native'],started_at=time.time(),automatic_retry=False,budget_seconds=60,prepared=str(p));write(lock/'JOB.json',job)
 # Worker and CLI are our pinned installed code, never paths taken from an acceptance ticket.
 worker=B/'gd_price_ui_worker.py'
 with (lock/'supervisor.log').open('x')as f:
  proc=subprocess.Popen([z['python'],'-B',str(worker),str(lock)],stdout=f,stderr=f,start_new_session=True)
 write(lock/'LAUNCH.json',dict(pid=proc.pid,worker_sha256=sha(worker),job_sha256=sha(lock/'JOB.json'),automatic_retry=False));return dict(state='已启动一次研究求解',lock=str(lock),native_budget_seconds=60)

def accept_completed(prepared,output,installation=None):
 z=profile(installation);p=Path(prepared).resolve();need(status(p)['completed'],'尚无原生完成票据；不能假称接受');out=Path(output).resolve();need(not out.exists(),'接受结果已存在，改用核验读取，不重签');out.parent.mkdir(parents=True,exist_ok=True)
 invoke(z,['accept','--prepared',p,'--output',out],Path(str(out)+'.log'),timeout=60);return read_accepted(out)

def read_accepted(path):
 p=Path(path);a=read(p);need(a['schema']=='gd.independent_PRICE.accepted.v2' and a['KKT']['passed'],'未通过独立PRICE验收')
 for f,h in a['source_pins'].items():need(sha(f)==h,'接受证据已变化')
 need(a['formal_bill']is None and a['payable']is False and a['safety_release']is False,'研究结果边界改变');need(len(a['raw_prices'])==39,'缺节点价格');return a

def account_hour(rt_run,da_run,hour,quarter_sources,output,installation=None):
 z=profile(installation);need(type(hour)is int and 0<=hour<24,'小时必须0至23');need(len(quarter_sources)==4 and all(Path(f).is_file()for f in quarter_sources),'必须显式提供完整四季度价格，不补零');out=Path(output).resolve();need(not out.exists(),'小时目录必须全新');out.mkdir(parents=True)
 invoke(z,['execution-view','--run',rt_run,'--da-run',da_run,'--hour-index',hour,'--output',out/'execution_view'],out/'view.log')
 invoke(z,['hour-manifest','--run',out/'execution_view','--da-run',da_run,'--hour-index',hour,'--prices',*quarter_sources,'--output',out/'INPUTS.json'],out/'manifest.log')
 invoke(z,['hour','--run',out/'execution_view','--da-run',da_run,'--hour-index',hour,'--price-manifest',out/'INPUTS.json','--output',out/'HOUR.json'],out/'hour.log')
 r=read(out/'HOUR.json');need(r['executed_cells']==12 and r['formal_bill']is None and not r['payable'],'小时研究边界');return r

def create_research_parameters(output,confirmed=False,installation=None):
 need(confirmed,'须明确选择研究参数，不是官方参数')
 need(str(output).strip(),'填写全新研究参数目录')
 z=profile(installation);out=Path(output).expanduser().resolve();need(not out.exists(),'样例目录已存在，不能覆盖');out.parent.mkdir(parents=True,exist_ok=True)
 log=out.parent/(out.name+'.parameters.log');need(not log.exists(),'已有生成记录，不自动重试')
 invoke(z,['research-parameters','--output',out],log,timeout=60)
 p=out/'parameters.json';d=read(p);need(d.get('mode')=='research','必须明确研究域')
 return dict(parameters=str(p),native_calls=0,mode='research')
