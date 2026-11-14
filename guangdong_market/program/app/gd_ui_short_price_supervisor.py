from pathlib import Path
import json,hashlib,subprocess,sys,traceback
p=Path(sys.argv[1]);read=lambda q:json.loads(q.read_bytes());sha=lambda q:hashlib.sha256(q.read_bytes()).hexdigest();code=1
try:
 j=read(p/'JOB.json');assert sha(p/'COMMAND.json')==j['command_sha256'];assert sha(Path(j['entry']))==j['entry_sha256'];assert sha(Path(j['prepared'])/'READY.json')==j['ready_sha256'];code=subprocess.call(read(p/'COMMAND.json'))
except Exception as e:
 with(p/'ERROR.json').open('x')as f:json.dump({'error':str(e),'traceback':traceback.format_exc(),'automatic_retry':False},f)
finally:
 with(p/'EXIT.json').open('x')as f:json.dump({'returncode':code,'requires_core_review':True,'automatic_retry':False},f)
raise SystemExit(code)
