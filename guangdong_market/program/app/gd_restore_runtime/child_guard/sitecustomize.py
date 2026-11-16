"""Narrow test guard; every Python worker emits its own receipt. Not OS tracing."""
import os,sys,json,atexit,subprocess,time
ROOT=os.path.realpath(os.environ['RESTORE_FORBIDDEN_ROOT']);LOG=os.environ['RESTORE_GUARD_LOG'];HERE=os.path.dirname(os.path.realpath(__file__))
def note(kind,**kw):
 with open(LOG,'a') as f:f.write(json.dumps(dict(kind=kind,pid=os.getpid(),ppid=os.getppid(),**kw))+'\n')
def audit(event,args):
 if event in ('open','os.listdir','os.scandir') and args and isinstance(args[0],(str,bytes,os.PathLike)):
  p=os.path.realpath(os.fsdecode(args[0]))
  if p==ROOT or p.startswith(ROOT+os.sep):
   note('blocked',event=event,path=p);raise RuntimeError('original run read forbidden: '+p)
 if event=='subprocess.Popen':
  exe,argv,cwd,env=args
  if os.path.realpath(os.fsdecode(exe))!=os.path.realpath(sys.executable):raise RuntimeError('unmonitored non-Python child rejected')
  if any(x in ('-I','-E','-S')for x in argv):raise RuntimeError('child bypasses startup guard')
  env=os.environ if env is None else env
  if env.get('RESTORE_FORBIDDEN_ROOT')!=os.environ['RESTORE_FORBIDDEN_ROOT'] or HERE not in env.get('PYTHONPATH','').split(os.pathsep):raise RuntimeError('child guard env absent')
sys.addaudithook(audit)
Original=subprocess.Popen
class GuardedPopen(Original):
 def __init__(self,*args,**kw):
  super().__init__(*args,**kw);note('spawn',child_pid=self.pid)
subprocess.Popen=GuardedPopen
note('started',executable=sys.executable,guard=__file__)
atexit.register(lambda:note('exited'))
