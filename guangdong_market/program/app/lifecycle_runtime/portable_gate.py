from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parent
EXPECTED='28fc42686df276d883b64d1c7f53852821a4b712cf9f026e851e4b2508bbbc86'
def verify():
 p=ROOT/'PORTABLE_MANIFEST.json'
 if hashlib.sha256(p.read_bytes()).hexdigest()!=EXPECTED:raise ValueError('portable manifest drift')
 for name,h in json.loads(p.read_bytes())['files'].items():
  p=(ROOT/name).resolve()
  if not p.is_relative_to(ROOT)or hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise ValueError('portable dependency drift: '+name)
