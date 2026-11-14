"""Explicit before-native fence; not solver authority or a process-exit claim."""
from pathlib import Path
import hashlib,json

def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,m):
 if not x:raise ValueError(m)

def validate_abandoned_reference(source):
 source=Path(source).resolve();d=source/'pipeline/POST_D1_REFERENCE';f=d/'ATTEMPT.json'
 need(f.is_file(),'explicit before-native cancellation fence required')
 z=read(f)
 need(z.get('schema')=='gd.reference.abandoned_before_native.v1' and z.get('fencing_only')is True and z.get('never_native_started')is True and type(z.get('native_calls'))is int and z['native_calls']==0,'existing/unknown native ATTEMPT is not a cancellation fence')
 need(Path(z.get('source_output','')).resolve()==source,'reference fence belongs to another run')
 need(not(d/'actual').exists() and not(d/'ACCEPTED_REFERENCE.json').exists(),'old reference native output exists; do not restart')
 need(Path(z.get('reference_dir','')).resolve()==d,'reference fence directory mismatch')
 expected=z.get('prepared_hashes',{})
 need(set(expected)=={'prepared/REFERENCE_LP.json','INTEGER_SOURCE.json'},'fence must bind exact original reference inputs')
 carry=source/'pipeline/dispatch/W94/EXACT_CARRY.json'
 need(carry.is_file() and read(carry).get('cutoff_tick')==285,'fence requires actual W94 handoff boundary')
 need(not(source/'pipeline/dispatch/W95/actual').exists() and not(source/'pipeline/price/q95/prepared/native_once').exists(),'later native stage exists; fence does not cover it')
 pins={str(f):sha(f),str(carry):sha(carry)}
 for name,digest in expected.items():
  p=d/name;need(p.is_file() and sha(p)==digest,'fenced prepared reference changed');pins[str(p)]=digest
 return dict(source_output=str(source),fence=str(f),pins=pins)
