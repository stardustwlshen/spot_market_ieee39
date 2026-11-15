"""Single reviewed continuation attempt; inherited kernel lock, no automatic retry."""
from pathlib import Path
import os,sys,json,traceback
from gd_live_control import sources
from gd_resume_runtime.engine import execute
out=Path(sys.argv[1])
if os.name=='nt':
 from gd_process_guard import claim_windows_worker
 guard=claim_windows_worker(out)
else:
 import fcntl
 fd=int(os.environ['GD_RESUME_LOCK_FD']);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
sources()
try:result=execute(json.loads((out/'RESUME_PLAN.json').read_bytes()),out,execute_native=True)
except BaseException as exc:result=dict(status='blocked_no_retry',error=str(exc),traceback=traceback.format_exc(),unknown_is_not_zero_calls=True)
with(out/'EXIT.json').open('x')as f:json.dump({**result,'publication_allowed':False,'safety_release':False},f,indent=2)
