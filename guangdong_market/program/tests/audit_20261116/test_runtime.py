from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
APP=ROOT/'app'
VENDOR=ROOT/'engines/guangdong_v3/guangdong_after_reset_r23_compressed_20261112/repo/src'
sys.path[:0]=[str(APP),str(VENDOR)]
import sys,json,subprocess,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(APP))
import gd_provincial_prepare as prep
import gd_process_guard as guard

def check_prepare_exception(exc,timeout):
 with tempfile.TemporaryDirectory() as temp:
  out=Path(temp)/'out'
  with patch.object(prep,'inputs_identity',return_value={}),patch.object(prep,'commands',return_value=[['fake']]),patch.object(prep.subprocess,'run',side_effect=exc):
   try:prep.prepare_fresh(temp,out,confirmed=True)
   except ValueError:pass
   else:raise AssertionError('must fail')
  progress=json.loads((out/'PROGRESS.json').read_text());step=json.loads((out/'STEP_0.json').read_text())
  assert progress['status']=='failed' and progress['timed_out']==timeout
  assert step['returncode'] is None
  if timeout:assert step['stdout']=='partial log' and step['native_completion_unknown']

check_prepare_exception(subprocess.TimeoutExpired(['fake'],8000,output=b'partial log',stderr=b'partial error'),True)
check_prepare_exception(FileNotFoundError('fake executable'),False)
with tempfile.TemporaryDirectory() as temp:
 out=Path(temp)
 assert guard.observe_run(out)['state']=='unknown'
 (out/'PID.json').write_text(json.dumps({'pid':999999,'process_namespace':guard.process_namespace()}))
 assert guard.observe_run(out)['state']=='unknown'
 lock=guard.acquire(out/'PROCESS.guard')
 # Real OS lock test; Linux independent open-file descriptions conflict.
 assert guard.observe_run(out)['state']=='lock_held'
 lock.close()
 assert guard.observe_run(out)['state']=='interrupted'
 (out/'PID.json').write_text(json.dumps({'pid':999999,'process_namespace':'different'}))
 assert guard.observe_run(out)['state']=='unknown'
 (out/'PID.json').write_text(json.dumps({'pid':999999}))
 assert guard.observe_run(out)['state']=='unknown'
print('PASS: timeout partial logs, launch failure, missing evidence, live lock, released lock, foreign and legacy namespace')
