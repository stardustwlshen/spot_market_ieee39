from pathlib import Path
import hashlib,json
B=Path(__file__).resolve().parent
EXPECTED='3fdfa53f205bda16622132814739f4de4d5daaa2393cebefc1a18cdfae744d78'
def verify_package():
 f=B/'SOURCE_MANIFEST.json'
 if hashlib.sha256(f.read_bytes()).hexdigest()!=EXPECTED:raise ValueError('safety source manifest changed')
 for n,h in json.loads(f.read_bytes())['files'].items():
  p=(B/n).resolve()
  if not p.is_relative_to(B)or hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise ValueError('safety dependency changed '+n)
