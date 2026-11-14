from pathlib import Path
import hashlib,json
B=Path(__file__).resolve().parent
EXPECTED='f70a54823b354f71bd5f50d18eec2464d24b74ac1200a85b343519b7b4f04f74'
def verify_package():
 f=B/'SOURCE_MANIFEST.json'
 if hashlib.sha256(f.read_bytes()).hexdigest()!=EXPECTED:raise ValueError('safety source manifest changed')
 for n,h in json.loads(f.read_bytes())['files'].items():
  p=(B/n).resolve()
  if not p.is_relative_to(B)or hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise ValueError('safety dependency changed '+n)
