"""Pure CLI preparation. No native/AC invocation, no automatic retries."""
from pathlib import Path
import json,hashlib,subprocess,sys,shlex

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(z):return hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,ensure_ascii=False,indent=2)
def preflight(cli,deps,parent,hour,zone,asof,output,mode,thermal=''):
 paths={k:str(Path(v).expanduser().resolve())for k,v in dict(cli=cli,deps=deps,parent=parent,output=output).items()if v.strip()}
 if len(paths)!=4:raise ValueError('必须明确 CLI、依赖、父run、新输出目录')
 if mode not in ('provided','synthetic'):raise ValueError('研究报价模式错误')
 c=Path(paths['cli']);d=Path(paths['deps']);r=Path(paths['parent']);o=Path(paths['output'])
 if not c.is_file()or not(d/'DEPENDENCIES.json').is_file()or not(r/'RUN.json').is_file():raise ValueError('CLI/依赖/父run不存在')
 if o.exists():raise ValueError('输出已存在，禁止覆盖或重复准备；请读取已有记录或指定新目录')
 if not hour.strip()or not zone.strip()or not asof.strip():raise ValueError('小时、zone、清算截止必须明确')
 pins={str(c):sha(c),str(d/'DEPENDENCIES.json'):sha(d/'DEPENDENCIES.json'),str(r/'RUN.json'):sha(r/'RUN.json')}
 manifest=json.loads((d/'DEPENDENCIES.json').read_bytes())
 for n,h in manifest['files'].items():
  p=(d/n).resolve()
  if not p.is_relative_to(d)or sha(p)!=h:raise ValueError('依赖变化: '+n)
  pins[str(p)]=h
 thermal_path=''
 if mode=='provided':
  if not thermal.strip():raise ValueError('需指定报价JSON；不默认使用样例')
  p=Path(thermal).resolve();json.loads(p.read_bytes());thermal_path=str(p);pins[str(p)]=sha(p)
 z=dict(schema='gd.ui.agc.pure_preflight.v1',**paths,hour=hour,zone=zone,asof=asof,mode=mode,thermal=thermal_path,pins=pins,native_calls=0,AC_calls=0);z['identity']=digest(z);return z

def prepare(expected,synthetic_confirmed=False):
 if expected['mode']=='synthetic'and synthetic_confirmed is not True:raise ValueError('须明确确认纯合成研究声明，非正式报价或新资格')
 current=preflight(**{k:expected[k]for k in ('cli','deps','parent','hour','zone','asof','output','mode','thermal')})
 if current!=expected:raise ValueError('输入/源码/依赖变化，请重新预检')
 out=Path(expected['output']);out.mkdir(parents=True,exist_ok=False);write(out/'ONE_SHOT.json',dict(preflight=expected,status='started_no_retry',native_calls=0,AC_calls=0))
 base=[sys.executable,'-B',expected['cli'],'--dependency-root',expected['deps']]
 def step(name,args):
  cmd=base+args;write(out/(name+'_COMMAND.json'),cmd)
  with(out/(name+'.log')).open('x')as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=180)
  write(out/(name+'_EXIT.json'),dict(returncode=r.returncode))
  if r.returncode:raise ValueError('CLI '+name+' 失败；保留输出/日志，不自动重试')
 try:
  context=out/'context.json';step('context',['context','--parent-run',expected['parent'],'--hour',expected['hour'],'--zone',expected['zone'],'--as-of',expected['asof'],'--output',str(context)])
  thermal=expected['thermal']
  if expected['mode']=='synthetic':
   thermal=str(out/'synthetic_offers.json');step('example',['example','--context',str(context),'--output',thermal])
  else:
   dst=out/'provided_offers.json';data=Path(thermal).read_bytes()
   if hashlib.sha256(data).hexdigest()!=expected['pins'][thermal]:raise ValueError('报价在预检后变化')
   with dst.open('xb')as f:f.write(data)
   thermal=str(dst)
  candidate=out/'candidate';step('prepare',['prepare','--context',str(context),'--thermal-input',thermal,'--output',str(candidate)])
  state=json.loads((candidate/'LOOP_PENDING.json').read_bytes());result=dict(status=state['status'],candidate=str(candidate),snapshot=str(candidate/'LOOP_PENDING.json'),mode=expected['mode'],native_calls=0,AC_calls=0,formal_submission=False,payable=False,native_cli_not_executed=shlex.join(base+['native','--candidate',str(candidate),'--output',str(out/'new_native_attempt'),'--execute-native']))
  result['CLI_instructions_not_executed']=cli_commands(expected,candidate)
  write(out/'RESULT.json',result);return result
 except Exception as e:
  write(out/'FAILED.json',dict(error=str(e),outcome='pure_preparation_failed_or_incomplete_no_auto_retry',native_calls=0,AC_calls=0));raise


def cli_commands(p,candidate):
 out=Path(p['output']);base=[sys.executable,'-B',p['cli'],'--dependency-root',p['deps']];native=str(out/'new_native_attempt');ac=str(out/'new_AC_attempt');c=str(candidate)
 return {'native_requires_explicit_execution':shlex.join(base+['native','--candidate',c,'--output',native,'--execute-native']), 'AC_requires_explicit_execution':shlex.join(base+['ac-check','--candidate',c,'--native',native,'--output',ac,'--execute-ac']), 'review_readonly_after_actual_evidence':shlex.join(base+['review','--candidate',c,'--native',native,'--ac-proof',str(Path(ac)/'NUMERIC_EVIDENCE.json'),'--output',str(out/'review.json')])}
