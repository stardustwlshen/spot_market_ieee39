"""Portable detached command supervision; no OS process probing or cancellation."""
from pathlib import Path
import json,subprocess,sys
p=Path(sys.argv[1]);cmd=json.loads((p/'COMMAND.json').read_bytes());code=subprocess.call(cmd)
with(p/'EXIT.json').open('x')as f:json.dump(dict(returncode=code,requires_core_preflight_review=True,automatic_retry=False),f)
raise SystemExit(code)
