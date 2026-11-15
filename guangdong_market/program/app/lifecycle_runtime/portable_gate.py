from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parent
EXPECTED='7474a5ef0c635a347ed985fca7a1448050ab583d641d9c8e18b44d8af86554c2'
def verify():
 p=ROOT/'PORTABLE_MANIFEST.json'
 if hashlib.sha256(p.read_bytes()).hexdigest()!=EXPECTED:raise ValueError('portable manifest drift')
 for name,h in json.loads(p.read_bytes())['files'].items():
  p=(ROOT/name).resolve()
  if not p.is_relative_to(ROOT)or hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise ValueError('portable dependency drift: '+name)
