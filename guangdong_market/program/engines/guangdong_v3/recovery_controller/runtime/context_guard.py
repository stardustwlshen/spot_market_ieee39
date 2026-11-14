from pathlib import Path
import json,hashlib
B=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load_context():
 c=json.loads((B/'context.json').read_bytes())
 for name,h in c['runtime_pins'].items():
  p=(B/name).resolve()
  if not p.is_relative_to(B)or sha(p)!=h:raise ValueError('frozen runtime changed')
 for name,h in c['engine_pins'].items():
  p=(Path(c['engine'])/name).resolve()
  if not p.is_relative_to(Path(c['engine']))or sha(p)!=h:raise ValueError('engine revision changed')
 if sha(Path(c['run'])/'RUN.json')!=c['run_sha256']:raise ValueError('context run changed')
 return c
