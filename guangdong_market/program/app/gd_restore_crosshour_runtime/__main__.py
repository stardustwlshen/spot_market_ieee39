"""Explicit bounded restore CLI. No automatic retries or source rewrites."""
from pathlib import Path
import argparse,json,os,sys,subprocess
from .preflight import check,sha,need,read_pinned

def main():
 p=argparse.ArgumentParser(description='受限恢复验证：Q07之后仅续Q08至Q11；不是通用恢复')
 p.add_argument('action',choices=['preflight','run']);p.add_argument('--config',required=True);p.add_argument('--config-sha',required=True);p.add_argument('--report',required=True);p.add_argument('--preflight-sha');a=p.parse_args()
 result=check(a.config,a.config_sha);report=Path(a.report).resolve()
 if a.action=='preflight':
  with report.open('x')as f:json.dump(result,f,indent=2)
  print(json.dumps({'report':str(report),'sha256':sha(report.read_bytes()),'native_calls':0}));return
 need(a.preflight_sha,'explicit reviewed preflight SHA required');need(read_pinned(report,a.preflight_sha)==result,'preflight changed; review required')
 c=read_pinned(a.config,a.config_sha);root=Path(c['output_root']).resolve();root.mkdir(parents=True,exist_ok=True)
 lock=root/(c['execution_attempt_id']+'.lock');lock.mkdir() # never reuse an attempt, including failure
 (lock/'OWNER.json').write_text(json.dumps({'pid':os.getpid(),'config_sha256':a.config_sha,'preflight_sha256':a.preflight_sha}))
 module=Path(__file__).resolve().parent;env=dict(os.environ);env['RESTORE_FORBIDDEN_ROOT']=result['original_output'];env['RESTORE_GUARD_LOG']=str(lock/'PYTHON_GUARD.jsonl');env['PYTHONPATH']=str(module/'child_guard')+os.pathsep+env.get('PYTHONPATH','')
 with (lock/'worker.log').open('x')as log:
  done=subprocess.run([sys.executable,'-B',str(module/'runner.py'),str(Path(a.config).resolve()),a.config_sha,str(report),a.preflight_sha],env=env,stdout=log,stderr=subprocess.STDOUT)
 exitfile=Path(result['new_output'])/'EXIT.json';need(done.returncode==0 and exitfile.is_file(),'restore stopped; preserve lock/logs; no retry')
 final=json.loads(exitfile.read_bytes());print(json.dumps(final));need(final['status']=='bounded_independent_restore_complete','restore blocked; inspect immutable attempt')
if __name__=='__main__':main()
