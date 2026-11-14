"""Explicit pure preparation against the selected accepted DA run; never launches native."""
from pathlib import Path
import hashlib,json,subprocess,sys,shlex

def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,z):
 with Path(p).open('x') as f:json.dump(z,f,ensure_ascii=False,indent=2)
def preflight(cli,parent,output):
 cli=Path(cli).expanduser().resolve();parent=Path(parent).expanduser().resolve();out=Path(output).expanduser().resolve()
 if not cli.is_file() or not (cli.parent/'DEPENDENCIES.json').is_file():raise ValueError('缺少已安装的实时组合程序和依赖清单')
 if out.exists():raise ValueError('输出已存在，禁止覆盖/重复准备；读取原状态，不能自动重试')
 a=read(parent/'ARTIFACTS.json');s=a['stages']['da_sced']
 accept=s['acceptance']; ap=(parent/accept['path']).resolve()
 if sha(ap)!=accept['sha256'] or read(ap).get('status')!='accepted':raise ValueError('所选日前 SCED 未被接受或票据变化')
 pins={str(cli):sha(cli),str(parent/'ARTIFACTS.json'):sha(parent/'ARTIFACTS.json')}
 for key in ('full_names','expanded_response','acceptance'):
  z=s[key];p=(parent/z['path']).resolve()
  if sha(p)!=z['sha256']:raise ValueError('所选日前物理解被修改')
  pins[str(p)]=z['sha256']
 record=read(parent/'RUN.json')
 source_pins=record.get('source_pins')
 if not source_pins:raise ValueError('缺少所选日前运行的来源源码身份')
 for name,h in source_pins.items():
  if sha(name)!=h:raise ValueError('所选日前来源源码变化：'+name)
  pins[name]=h
 for role in ('case','agc','policy'):
  z=a['inputs'][role];p=(parent/z['path']).resolve()
  if not p.is_relative_to(parent) or sha(p)!=z['sha256']:raise ValueError('所选场景输入变化：'+role)
  pins[str(p)]=z['sha256']
 pins[str(parent/'RUN.json')]=sha(parent/'RUN.json')
 for p in [cli.parent/'DEPENDENCIES.json',*sorted((cli.parent/'runtime').glob('*.py'))]:pins[str(p)]=sha(p)
 return dict(cli=str(cli),parent=str(parent),output=str(out),pins=pins,native_calls=0,scope='反事实研究，非安全调度发布')
def prepare(expected,confirmed=False):
 if confirmed is not True:raise ValueError('需明确选择独立反事实研究准备')
 if preflight(**{k:expected[k] for k in ('cli','parent','output')})!=expected:raise ValueError('输入或代码发生变化，请重新预检')
 out=Path(expected['output']);out.mkdir(parents=True,exist_ok=False);write(out/'ONE_SHOT.json',expected)
 run=out/'run';base=[sys.executable,'-B',expected['cli']]
 try:
  for name,args in [('verify',['verify']),('seed',['seed','--da-run',expected['parent'],'--run',str(run)]),('build',['build','--run',str(run),'--window','0']),('dryrun',['run-one-window','--run',str(run),'--window','0'])]:
   cmd=base+args;write(out/(name+'_COMMAND.json'),cmd)
   with (out/(name+'.log')).open('x') as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=240)
   write(out/(name+'_EXIT.json'),{'returncode':r.returncode})
   if r.returncode:raise ValueError(name+' 失败；保留日志，不自动重试，不替换所选场景')
  result=dict(status='已准备，尚未求解',run=str(run),parent=expected['parent'],native_calls=0,safety_release=False,formal_bill=None,command_not_executed=shlex.join(base+['run-one-window','--run',str(run),'--window','0','--execute-native']))
  write(out/'RESULT.json',result);return result
 except Exception as e:write(out/'FAILED.json',dict(error=str(e),automatic_retry=False,native_calls=0));raise

def inspect_run(path):
 p=Path(path).expanduser().resolve();ctx=read(p/'CONTEXT.json');rows=[]
 for w in sorted(p.glob('W[0-9]*'),key=lambda x:int(x.name[1:])):
  if not w.is_dir():continue
  status='仅准备'
  if (w/'ACCEPTED_PAIR.json').exists():status='接受票据存在，需 CLI accept 复核'
  elif any(w.glob('*_native/COMPLETED.json')):status='求解已返回，等待完整数学复核'
  elif any(w.glob('*_native/LAUNCH.json')):status='求解状态未知/进行中，禁止自动重试'
  rows.append({'窗口':w.name,'状态':status})
 return dict(run_id=ctx['run_id'],windows=rows,safety_release=False)
