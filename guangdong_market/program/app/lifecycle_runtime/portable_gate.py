from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parent
EXPECTED='70367ee21ac47e443d1b4879d256c23d69bae3b10f97632b64ea80c982bd68e4'
def verify():
 p=ROOT/'PORTABLE_MANIFEST.json'
 if hashlib.sha256(p.read_bytes()).hexdigest()!=EXPECTED:raise ValueError('portable manifest drift')
 for name,h in json.loads(p.read_bytes())['files'].items():
  p=(ROOT/name).resolve()
  if not p.is_relative_to(ROOT)or hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise ValueError('portable dependency drift: '+name)
