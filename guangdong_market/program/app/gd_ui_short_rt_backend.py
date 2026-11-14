"""One-window background launcher. Core preflight/acceptance remains authoritative."""
from pathlib import Path
import hashlib,json,subprocess,sys,uuid,os
B=Path(__file__).resolve().parent

def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,ensure_ascii=False,indent=2,allow_nan=False)
def command(entry,run,window,action='preflight'):
 entry=Path(entry).expanduser().resolve();run=Path(run).expanduser().resolve()
 if not entry.is_file():raise ValueError('缺少已安装的实时组合程序')
 if type(window)is not int or not 0<=window<=94:raise ValueError('本短域入口目前支持0–94窗；末日扩展尚未接通')
 if not (run/'SHORT_CONTEXT.json').is_file():raise ValueError('缺少研究运行上下文，先准备或明确导入')
 return [sys.executable,'-B',str(entry),action,'--run',str(run),'--window',str(window)]
def preflight(entry,run,window):
 cmd=command(entry,run,window)
 q=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
 if q.returncode:raise ValueError((q.stderr or q.stdout)[-3500:])
 try:z=json.loads(q.stdout.strip().splitlines()[-1])
 except (ValueError,IndexError)as e:raise ValueError('实时预检未返回可识别状态')from e
 if z.get('native_calls')!=0:raise ValueError('预检不得启动求解')
 if z.get('status')not in ('ready','accepted'):raise ValueError('当前状态不允许启动：'+str(z.get('status')))
 return dict(entry=str(Path(entry).resolve()),entry_sha256=sha(entry),run=str(Path(run).resolve()),window=window,command=cmd,core=z)
def launch(proof,confirmed=False):
 if confirmed is not True:raise ValueError('须明确选择研究求解')
 now=preflight(proof['entry'],proof['run'],proof['window'])
 if now!=proof:raise ValueError('状态或输入改变，请重新预检')
 if now['core']['status']=='accepted':return dict(status='已接受，不重新求解',native_calls=0,already_accepted=True)
 run=Path(proof['run']);control=run/'ui_control'/f"W{proof['window']}";control.mkdir(parents=True,exist_ok=True);lock=control/'ACTIVE_OR_UNKNOWN.json'
 cmd=command(proof['entry'],run,proof['window'],'run')+['--execute-native']
 if lock.exists():
  old=read(lock);previous=Path(old['attempt'])
  if previous.parent!=control or not (previous/'EXIT.json').is_file():raise ValueError('此窗口已有未知/进行中的界面记录，不会自动重试')
  if sha(previous/'COMMAND.json')!=old['command_sha256'] or read(previous/'COMMAND.json')!=cmd or sha(previous/'PROOF.json')!=old['proof_sha256']:raise ValueError('原界面发生记录不匹配，拒绝接续')
  prior=read(previous/'PROOF.json')
  if any(prior[k]!=proof[k] for k in ('entry','entry_sha256','run','window')):raise ValueError('原入口或窗口身份不同，拒绝接续')
  exitcode=read(previous/'EXIT.json').get('returncode')
  if type(exitcode)is not int:raise ValueError('原界面退出状态不明')
  lock.rename(control/('EXITED_ATTEMPT_'+previous.name+'.json'))
 attempt=control/uuid.uuid4().hex;attempt.mkdir()
 write(attempt/'COMMAND.json',cmd);write(attempt/'PROOF.json',proof);write(lock,dict(attempt=str(attempt),proof_sha256=sha(attempt/'PROOF.json'),command_sha256=sha(attempt/'COMMAND.json')))
 try:
  with(attempt/'driver.log').open('x')as log:
   child=subprocess.Popen([sys.executable,'-B',str(B/'gd_ui_short_rt_supervisor.py'),str(attempt)],stdout=log,stderr=log,start_new_session=os.name!='nt',env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','TZ':'UTC'})
  write(attempt/'UI_LAUNCH.json',dict(pid=child.pid,entry_sha256=proof['entry_sha256'],automatic_retry=False));return dict(status='后台已启动，尚待求解及完整复核',attempt=str(attempt),pid=child.pid,already_accepted=False)
 except Exception as e:write(attempt/'LAUNCH_ERROR.json',dict(error=str(e),automatic_retry=False));raise

def handoff(entry,run,window,confirmed=False):
 if confirmed is not True:raise ValueError('须明确选择无偏差反事实研究回执')
 p=preflight(entry,run,window)
 if p['core']['status']!='accepted':raise ValueError('尚未严格接受，不能生成回执')
 cmd=command(entry,run,window,'handoff')+['--simulate-no-deviation'];q=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
 if q.returncode:raise ValueError((q.stderr or q.stdout)[-3500:])
 z=json.loads(q.stdout.strip().splitlines()[-1])
 if z.get('formal')is not False or z.get('safety_release')is True:raise ValueError('回执不是未安全放行研究状态')
 # The v1 raw handoff only asserts formal=False. Do not rewrite it or imply AC certification.
 return dict(handoff=z,formal=False,safety_release=False,safety_basis='preflight explicitly false; handoff is counterfactual only')

def prepare(entry,da_run,output,confirmed=False):
 if confirmed is not True:raise ValueError('须明确选择独立反事实初态与纯构模')
 if not str(output).strip() or not str(da_run).strip():raise ValueError('填写来源日前运行和全新输出目录')
 entry=Path(entry).expanduser().resolve();out=Path(output).expanduser().resolve();da=Path(da_run).expanduser().resolve()
 if out.exists():raise ValueError('输出已存在，不覆盖或重复准备')
 ctrl=out.parent/(out.name+'_prepare_control');ctrl.mkdir(parents=True,exist_ok=False)
 write(ctrl/'INPUT.json',dict(entry=str(entry),entry_sha256=sha(entry),selected_DA=str(da),output=str(out),native_calls=0))
 for name,args in [('seed',['seed','--da',str(da),'--run',str(out)]),('build',['build','--run',str(out),'--window','0'])]:
  cmd=[sys.executable,'-B',str(entry)]+args;write(ctrl/(name+'_COMMAND.json'),cmd)
  with(ctrl/(name+'.log')).open('x')as log:q=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=240)
  write(ctrl/(name+'_EXIT.json'),dict(returncode=q.returncode))
  if q.returncode:raise ValueError(name+'失败；保留记录，不替换来源或自动重试。'+str(ctrl))
 proof=preflight(entry,out,0);write(ctrl/'PREPARED_UI.json',proof);return proof

def build_next(entry,run,window):
 cmd=command(entry,run,window,'build');root=Path(run);out=root/f'W{window}'
 if out.exists():return preflight(entry,run,window)
 ctrl=root/'ui_build';ctrl.mkdir(exist_ok=True);p=ctrl/f'W{window}_COMMAND.json';write(p,cmd)
 with(ctrl/f'W{window}.log').open('x')as log:q=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=240)
 write(ctrl/f'W{window}_EXIT.json',dict(returncode=q.returncode))
 if q.returncode:raise ValueError('纯构模失败；保留日志，不自动覆盖或重试')
 return preflight(entry,run,window)
