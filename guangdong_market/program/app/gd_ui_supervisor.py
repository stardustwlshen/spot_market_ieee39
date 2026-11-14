from pathlib import Path
import json,subprocess,sys
p=Path(sys.argv[1]);cmd=json.loads((p/'COMMAND.json').read_bytes());code=subprocess.call(cmd)
with(p/'EXIT.json').open('x')as f:json.dump({'returncode':code,'unknown_outcome_requires_manual_receipt_check':code!=0},f)
raise SystemExit(code)
