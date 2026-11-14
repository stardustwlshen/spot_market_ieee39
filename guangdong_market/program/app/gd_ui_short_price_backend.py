"""Only the installed short PRICE CLI; fresh outputs and explicit one-shot native."""
from pathlib import Path
import sys,os,json,hashlib,subprocess
B=Path(__file__).resolve().parent
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(ok,msg):
 if not ok:raise ValueError(msg)
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,ensure_ascii=False,indent=2)
def call(entry,args,timeout=240,log=None):
 e=Path(entry).expanduser().resolve();need(e.is_file(),'短域PRICE程序未安装')
 cmd=[sys.executable,'-B',str(e),*map(str,args)];q=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
 if log:
  with Path(log).open('x')as f:f.write(q.stdout+'\n'+q.stderr)
 try:z=json.loads(q.stdout.strip().splitlines()[-1])
 except(ValueError,IndexError):raise ValueError('PRICE未返回可识别状态；'+q.stderr[-1000:])
 need(q.returncode==0 and z.get('status')!='blocked',z.get('error')or q.stderr[-2000:]or str(z))
 need(z.get('formal')is False and z.get('safety_release')is False and z.get('payable')is False,'研究状态字段不一致')
 return z

def parameters(entry,da,run,w,output,name,domain,confirmed=False):
 need(confirmed,'须明确采用命名研究假设，不是广东正式参数');need(str(output).strip(),'填写全新参数JSON');p=Path(output).resolve();need(not p.exists(),'参数文件已存在，不覆盖')
 z=call(entry,['parameters','--da',da,'--run',run,'--window',w,'--output',p,'--name',name,'--domain',domain]);need(z['native_calls']==0,'参数生成不得求解');return z

def prepare(entry,da,run,w,parameters,output):
 need(str(output).strip(),'填写全新PRICE准备目录');o=Path(output).resolve();need(not o.exists(),'准备目录已存在；保留失败，不覆盖');o.parent.mkdir(parents=True,exist_ok=True);log=o.parent/(o.name+'.ui_prepare.log');need(not log.exists(),'已有准备尝试，先检查原日志，不自动重试')
 z=call(entry,['prepare','--da',da,'--run',run,'--window',w,'--parameters',parameters,'--output',o],log=log);need(z['native_calls']==0 and z['status']=='prepared','纯准备状态不符');return z

def status(entry,prepared):
 need(str(prepared).strip(),'填写已准备目录');p=Path(prepared).resolve();z=call(entry,['native','--prepared',p]);need(z['native_calls']==0,'状态读取不得求解');lock=p/'UI_SHORT_PRICE_ONCE'
 if lock.exists() and z['status']=='ready':
  need((lock/'EXIT.json').exists(),'界面尝试正在运行或未知，禁止重试')
  raise ValueError('已有未形成原生完成的界面尝试；保留记录，禁止自动重复')
 return z

def launch(entry,prepared,confirmed=False):
 need(confirmed,'须明确启动一次研究PRICE');p=Path(prepared).resolve();e=Path(entry).resolve();s=status(e,p)
 if s['status']=='completed':return dict(status='already_completed_no_new_native',native_calls=0)
 need(s['status']=='ready','尚未准备好');lock=p/'UI_SHORT_PRICE_ONCE';lock.mkdir(exist_ok=False)
 cmd=[sys.executable,'-B',str(e),'native','--prepared',str(p),'--execute-native'];write(lock/'COMMAND.json',cmd);write(lock/'JOB.json',dict(entry=str(e),entry_sha256=sha(e),prepared=str(p),ready_sha256=sha(p/'READY.json'),command_sha256=sha(lock/'COMMAND.json'),automatic_retry=False))
 with(lock/'supervisor.log').open('x')as f:proc=subprocess.Popen([sys.executable,'-B',str(B/'gd_ui_short_price_supervisor.py'),str(lock)],stdout=f,stderr=f,start_new_session=os.name!='nt',env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
 write(lock/'LAUNCH.json',dict(pid=proc.pid,automatic_retry=False));return dict(status='started_not_yet_accepted',lock=str(lock),native_budget_seconds=60)

def accept(entry,prepared,output):
 need(not Path(output).exists(),'接受文件已存在，请改用读取，不重签');return call(entry,['accept','--prepared',prepared,'--output',output])
def read_result(entry,accepted):return call(entry,['read','--accepted',accepted])
def hour(entry,da,run,h,quarters,output):
 need(len(quarters)==4 and all(str(q).strip() for q in quarters),'四季度必须显式完整，不补零');o=Path(output).resolve();need(str(output).strip()and not o.exists(),'需全新小时目录');o.mkdir(parents=True)
 fixed=B.parent/'engines/guangdong_short_price_v1/guangdong_short_price_user_cli_v1_20261112/program.py';need(Path(entry).resolve()==fixed.resolve(),'小时适配仅支持固定安装PRICE CLI1')
 v=call(B/'gd_ui_short_read_adapter.py',['view',da,run,h,o/'EXECUTION.json'],timeout=600,log=o/'VIEW.log');need(v['executed_cells']==12,'小时尚未完整执行')
 z=call(entry,['hour','--da',da,'--execution',o/'EXECUTION.json','--quarters',*quarters,'--output',o/'HOUR.json'],log=o/'HOUR.log');return z
