from pathlib import Path
import sys,json,subprocess
p=Path(sys.argv[1]);cmd=json.loads((p/'COMMAND.json').read_bytes());code=subprocess.call(cmd)
with(p/'EXIT.json').open('x')as f:json.dump(dict(returncode=code,unknown_outcome_requires_receipt_review=code!=0),f)
raise SystemExit(code)
