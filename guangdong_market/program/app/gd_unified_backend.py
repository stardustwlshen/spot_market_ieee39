"""Trusted unified candidate facade. No solver runs without explicit launch."""
from pathlib import Path
import os,sys,json,re,hashlib,subprocess
B=Path(__file__).resolve().parent
CONFIG=B/'TRUSTED_INSTALL.json'
def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def write(p,z):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:json.dump(z,f,ensure_ascii=False,indent=2,allow_nan=False)
def trusted():
 c=read(CONFIG);root=(B/c['package']).resolve();m=root/'FROZEN_MANIFEST.json';need(sha(m)==c['manifest_sha256'],'固定安装来源清单改变，拒绝执行')
 for n,h in read(m)['files'].items():
  p=(root/n).resolve();need(p.is_relative_to(root)and sha(p)==h,'固定安装源码改变：'+n)
 return root/'user_entry.py'
def call(args,timeout=600):
 e=trusted();cmd=[sys.executable,'-B',str(e),*map(str,args)]
 q=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','TZ':'UTC'})
 try:z=json.loads(q.stdout.strip().splitlines()[-1])
 except Exception:raise ValueError('入口没有完整返回状态；保留原记录，不自动重试：'+q.stderr[-500:])
 need(q.returncode==0 and z.get('status')!='blocked',z.get('error',z.get('reason',q.stderr[-1000:]or str(z))))
 need(z.get('formal')is False,'不能接收正式操作状态');return z

def layout(base,name):
 need(bool(re.fullmatch(r'[A-Za-z0-9_\-\u4e00-\u9fff]{1,60}',name or '')),'运行名仅用文字、数字、下划线或连字符')
 p=Path(base).expanduser().resolve()/name;return dict(root=p,run=p/'rt',days=p/'days.json')
def bound(da,run):
 r=Path(run).expanduser().resolve();need((r/'SHORT_CONTEXT.json').is_file(),'请先准备，或选择已有研究运行')
 c=read(r/'SHORT_CONTEXT.json');need(Path(c['selected_DA']).resolve()==Path(da).resolve(),'日前来源与此运行不匹配');return r

def prepare(da,base,name,published_at,confirmed=False):
 need(confirmed,'须明确接受研究边界假设');need(str(da).strip(),'填写已接受日前来源');trusted();p=layout(base,name);need(not p['root'].exists(),'运行名已存在，改用已有运行；不覆盖或重复准备');p['root'].mkdir(parents=True)
 write(p['root']/'UI_INPUT.json',dict(selected_DA=str(Path(da).resolve()),profile='conservative_DA_protection_research',published_at=published_at,formal=False))
 commands=[['rt','declare','--da',da,'--output',p['days'],'--profile','repeat-selected-da-terminal-and-48h-online-plan-research','--protection-policy','conservative_DA_protection_research','--hypothetical-published-at',published_at,'--hypothetical-available-at',published_at],['rt','seed','--da',da,'--run',p['run'],'--day-declarations',p['days']],['rt','build','--run',p['run'],'--window',0]]
 for i,a in enumerate(commands):
  write(p['root']/f'PREPARE_{i}_COMMAND.json',list(map(str,a)));z=call(a);need(z.get('native_calls')==0,'纯准备不得启动求解');write(p['root']/f'PREPARE_{i}_RESULT.json',z)
 return rt_proof(da,p['run'],0)
def rt_proof(da,run,w):
 need(type(w)is int and 0<=w<=95,'窗口必须为0–95');r=bound(da,run);z=call(['rt','preflight','--run',r,'--window',w]);need(z.get('native_calls')==0,'读取不得求解')
 return dict(da=str(Path(da).resolve()),run=str(r),window=w,core=z,install_sha256=sha(CONFIG))
def build(da,run,w):
 r=bound(da,run)
 if not(r/f'W{w}').exists():
  z=call(['rt','build','--run',r,'--window',w]);need(z.get('native_calls')==0,'纯构模不得求解')
 return rt_proof(da,r,w)
def price_paths(run,w):
 p=Path(run).resolve().parent/'price'/('seed'if w==-1 else f'W{w}');return dict(root=p,parameters=p/'PARAMETERS.json',prepared=p/'prepared',accepted=p/'ACCEPTED.json')
def prepare_price(da,run,w,domain='thermal_DA_DA_previous_quarter_anchor_research',confirmed=False):
 need(confirmed,'须明确采用完整命名定价研究参数');r=bound(da,run);need(type(w)is int and -1<=w<=94,'价格窗口为seed(-1)或0–94');p=price_paths(r,w)
 if w>=0:need(rt_proof(da,r,w)['core']['status']=='accepted','先严格接受此RT窗口，再准备独立价格')
 if p['root'].exists():raise ValueError('该季度已有准备记录；仅可刷新/接受，不自动覆盖')
 p['root'].mkdir(parents=True)
 policies=('thermal_DA_DA_previous_quarter_anchor_research','thermal_DA_storage_current','legacy_all_current_comparison','legacy_DA_storage_comparison')
 need(domain in policies,'请明确选择新profile或旧模型比较，不能静默回退')
 base_domain='DA_reference_R61_compatible_research'if domain=='legacy_DA_storage_comparison'else'current_sced_research'
 a=['price','parameters','--da',da,'--run',r,'--window',w,'--output',p['parameters'],'--name','UI-v9-explicit-thermal-profile','--domain',base_domain];z=call(a);need(z.get('native_calls')==0,'参数不得求解')
 import shutil
 host=B/'gd_thermal_price_runtime/host.py';q=subprocess.run([sys.executable,'-B',str(host),'--da',str(da),'--run',str(r),'--window',str(w),'--parameters',str(p['parameters']),'--output',str(p['root']/'extension'),'--policy',domain],capture_output=True,text=True)
 write(p['root']/'HOST_RETURN.json',dict(returncode=q.returncode,stdout=q.stdout,stderr=q.stderr,native_calls=0));need(q.returncode==0,'扩展准备失败，保留记录，不重试：'+q.stderr[-800:])
 shutil.copytree(p['root']/'extension/prepared',p['prepared']);return price_status(r,w)
def price_status(run,w):
 p=price_paths(run,w);z=call(['price','native','--prepared',p['prepared']]);need(z.get('native_calls')==0,'状态读取不得求解');return z

def native_command(kind,run,w):
 need(kind in ('rt','price'),'未知求解阶段')
 return ['rt','run','--run',str(Path(run).resolve()),'--window',w,'--execute-native']if kind=='rt'else['price','native','--prepared',str(price_paths(run,w)['prepared']),'--execute-native']
def launch(kind,da,run,w,confirmed=False,proof=None):
 need(confirmed,'须明确允许本阶段一次研究求解');r=bound(da,run)
 state=rt_proof(da,r,w)if kind=='rt'else price_status(r,w)
 if kind=='rt':
  need(proof is not None and proof==state,'实时输入或状态改变，请重新预检');completed=state['core']['status']=='accepted'
 else:completed=state['status']=='completed'
 if completed:return dict(status='already_completed_no_new_native',native_calls=0)
 lock=r/'ui_unified_control'/f'{kind}_{w}'
 need(not lock.exists(),'本阶段已有进行中/失败/未知界面记录，不自动重试；先核验原记录')
 lock.mkdir(parents=True);job=dict(kind=kind,da=str(Path(da).resolve()),run=str(r),window=w,config_sha256=sha(CONFIG),state=state,automatic_retry=False)
 write(lock/'JOB.json',job);write(lock/'COMMAND.json',list(map(str,native_command(kind,r,w))))
 with(lock/'SUPERVISOR.log').open('x')as f:proc=subprocess.Popen([sys.executable,'-B',str(B/'gd_unified_supervisor.py'),str(lock)],stdout=f,stderr=f,start_new_session=os.name!='nt',env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'})
 write(lock/'LAUNCH.json',dict(pid=proc.pid,automatic_retry=False));return dict(status='started_not_yet_accepted',control=str(lock),native_calls='pending_check_receipts')
def handoff(da,run,w,confirmed=False):
 need(confirmed,'须明确选择无偏差反事实回执');p=rt_proof(da,run,w);need(p['core']['status']=='accepted','尚未严格接受');r=Path(p['run'])
 # Never sign or overwrite an existing handoff. Core will verify it at the next build.
 need(not(r/f'handoff/W{w+1}').exists(),'交接已有记录，不重新生成；构建下一窗时会严格重放')
 z=call(['rt','handoff','--run',r,'--window',w,'--simulate-no-deviation']);need(z.get('native_calls')==0,'交接不得求解');return z
def accept_price(run,w):
 p=price_paths(run,w)
 if not p['accepted'].exists():call(['price','accept','--prepared',p['prepared'],'--output',p['accepted']])
 return call(['price','read','--accepted',p['accepted']])
def read_price(run,w):return call(['price','read','--accepted',price_paths(run,w)['accepted']])
def hour(da,run,h):
 r=bound(da,run);need(type(h)is int and 0<=h<24,'小时0–23');o=r.parent/'hours'/f'H{h}';done=o/'COMPLETED.json'
 if done.exists():
  d=read(done);p=Path(d['result']);need(p.resolve().is_relative_to(o.resolve())and sha(p)==d['sha256'],'小时结果身份改变')
  qs=[price_paths(r,4*h+j-1)['accepted']for j in range(4)]
  import tempfile
  with tempfile.TemporaryDirectory(prefix='gd-ui-hour-reread-')as tmp:
   fresh=Path(tmp)/'HOUR.json';call(['price','hour','--da',da,'--execution',p.parent/'EXECUTION.json','--quarters',*qs,'--output',fresh]);need(read(fresh)==read(p),'小时金额或执行账与原始数据完整重算不符')
  return call(['price','read','--accepted',p])
 qs=[price_paths(r,4*h+j-1)['accepted']for j in range(4)];need(all(p.is_file()for p in qs),'缺同源季度价，不补零/不借其他场景')
 o.mkdir(parents=True,exist_ok=True)
 for previous in o.glob('attempt_*'):need((previous/'EXIT.json').exists(),'已有未知纯核算尝试，请先核记录，不自动重试')
 import uuid
 a=o/('attempt_'+uuid.uuid4().hex);a.mkdir();write(a/'JOB.json',dict(native_calls=0,da=str(Path(da).resolve()),run=str(r),hour=h))
 try:
  call(['price','execution-view','--da',da,'--run',r,'--hour',h,'--output',a/'EXECUTION.json'])
  z=call(['price','hour','--da',da,'--execution',a/'EXECUTION.json','--quarters',*qs,'--output',a/'HOUR.json'])
  write(a/'EXIT.json',dict(returncode=0,native_calls=0));write(done,dict(result=str(a/'HOUR.json'),sha256=sha(a/'HOUR.json'),native_calls=0));return z
 except Exception as e:write(a/'EXIT.json',dict(returncode=1,native_calls=0,error=str(e)));raise

def ensure_price_policy(run,w,selected):
 p=price_paths(run,w);ctx=read(p['prepared']/'CONTEXT.json');actual=ctx.get('thermal_reference_extension',{}).get('policy')
 if actual is None:actual='legacy_DA_storage_comparison'if read(p['parameters'])['domain']=='DA_reference_R61_compatible_research'else'legacy_all_current_comparison'
 need(actual==selected,'已有PRICE策略与当前选择不同，拒绝静默启动或将旧价标为新profile')
 return actual
