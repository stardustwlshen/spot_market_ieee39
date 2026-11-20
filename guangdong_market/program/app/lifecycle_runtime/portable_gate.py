from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parent
EXPECTED='b70c8f21b7903a592a36789ff1b236a98986dd8d8360272aaa328019c09d54c3'
def verify():
 p=ROOT/'PORTABLE_MANIFEST.json'
 if hashlib.sha256(p.read_bytes()).hexdigest()!=EXPECTED:raise ValueError('portable manifest drift')
 for name,h in json.loads(p.read_bytes())['files'].items():
  p=(ROOT/name).resolve()
  if not p.is_relative_to(ROOT)or hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise ValueError('portable dependency drift: '+name)
