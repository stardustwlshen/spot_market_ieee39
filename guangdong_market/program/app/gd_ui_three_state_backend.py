"""UI invokes trusted installed CLI in isolated processes; never imports its solver modules."""
from pathlib import Path
import sys,os,json,subprocess,hashlib
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,ensure_ascii=False,indent=2)
def command(engine,action,run,extra=()):
 engine=Path(engine).resolve();pkg=engine/'gd_three_state'
 if not(pkg/'adapter.py').is_file():raise ValueError('未找到已安装三态引擎')
 for name,h in read(pkg/'DEPENDENCIES.json').items():
  p=(pkg/'deps'/name).resolve()
  if not p.is_relative_to(pkg/'deps')or sha(p)!=h:raise ValueError('安装依赖变化：'+name)
 return [sys.executable,'-B','-m','gd_three_state.adapter',action,'--run',str(Path(run).resolve()),*extra],{**os.environ,'PYTHONPATH':str(engine),'PYTHONDONTWRITEBYTECODE':'1'}
def inspect(engine,run):
 cmd,env=command(engine,'preflight',run);r=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=90)
 if r.returncode:raise ValueError('预检失败，保留原记录；'+r.stderr[-1200:])
 return json.loads(r.stdout.strip().splitlines()[-1])
def prepare(engine,parent,out,selection,parameters='',confirmed=False):
 if not confirmed:raise ValueError('需明确确认独立研究假设')
 if selection not in('degenerate','file'):raise ValueError('必须显式选择参数来源')
 out=Path(out).resolve()
 if out.exists():raise ValueError('输出已存在，不覆盖/不重复准备；可只读预检')
 if selection=='file'and not Path(parameters).is_file():raise ValueError('请提供明确的三态参数JSON')
 if not(Path(parent)/'ARTIFACTS.json').is_file():raise ValueError('所选日前运行缺少接受资料')
 # UI receipt is separate from adapter run so failed partial preparation remains inspectable.
 out.mkdir(parents=True);run=out/'run';write(out/'UI_PREPARE.json',dict(parent=str(Path(parent).resolve()),selection=selection,parameters=parameters,engine=str(Path(engine).resolve()),native_calls=0))
 args=['--parent',str(Path(parent).resolve())]+(['--degenerate-research']if selection=='degenerate'else['--parameters',str(Path(parameters).resolve())])
 for action,extra in [('seed',args),('build',[]),('preflight',[])]:
  cmd,env=command(engine,action,run,extra);write(out/(action+'_COMMAND.json'),cmd)
  with(out/(action+'.log')).open('x')as log:r=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=300)
  write(out/(action+'_EXIT.json'),dict(returncode=r.returncode))
  if r.returncode:raise ValueError(action+'失败；原日志保留，不自动重试/更换场景')
 z=dict(status='纯准备通过，尚未求解',run=str(run),native_calls=0,safety_release=False);write(out/'RESULT.json',z);return z

def launch(engine,run,confirmed=False):
 if not confirmed:raise ValueError('实际求解须单独明确确认')
 run=Path(run).resolve();status=inspect(engine,run)
 if status['unknown_locked']:raise ValueError('存在未知求解结果，拒绝启动/自动重试')
 if all(v=='completed_needs_review'for v in status['stages'].values()):raise ValueError('两阶段已完成，只可使用accept复核，不重复求解')
 ticket=run/'UI_NATIVE_ATTEMPT'
 if ticket.exists():raise ValueError('本UI票据已经启动；先查看EXIT/原生结果，不重复发送或强制取消')
 cmd,env=command(engine,'run',run,['--execute-native']);ticket.mkdir();write(ticket/'COMMAND.json',cmd);write(ticket/'ENV.json',{'PYTHONPATH':env['PYTHONPATH']})
 supervisor=Path(__file__).with_name('gd_ui_three_state_supervisor.py')
 with(ticket/'supervisor.log').open('xb')as f:p=subprocess.Popen([sys.executable,'-B',str(supervisor),str(ticket)],stdout=f,stderr=subprocess.STDOUT,env=env,start_new_session=os.name!='nt')
 write(ticket/'LAUNCH.json',dict(pid=p.pid,automatic_retry=False,safe_cancel_supported=False));return dict(status='独立研究求解已启动，尚未接受',ticket=str(ticket),safety_release=False)
