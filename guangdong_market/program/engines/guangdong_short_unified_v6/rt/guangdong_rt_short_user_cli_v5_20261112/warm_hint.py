"""Prior accepted-window values are optional partial hints, not constraints or telemetry."""
from pathlib import Path
import json,gzip,hashlib,re,math,copy
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def attach(prepared):
 p=Path(prepared);carry=read(p/'CARRY.json');sources=carry['source_pins'];nn=[f for f in sources if f.endswith('/names.json')];rr=[f for f in sources if f.endswith('/SCED/response.json.gz')];
 if carry['delivery_tick']==3 and not rr:
  proof=dict(schema='gd.short.partial_mip_hint.v1',status='initial_seed_no_prior_RT_hint',native_calls=0,formal=False)
  
  with (p/'WARM_HINT.json').open('x') as f:json.dump(proof,f,sort_keys=True)
  return proof
 assert len(nn)==len(rr)==1,'noninitial carry requires one accepted prior SCED identity'
 for f in (nn[0],rr[0]):assert sha(f)==sources[f]
 n0=read(nn[0]);r=json.loads(gzip.decompress(Path(rr[0]).read_bytes()));assert r['status']=='optimal'and r['has_primal'];previous=dict(zip(n0['variables'],r['primal']));q=read(p/'template.json');original=copy.deepcopy(q);n=read(p/'names.json');count=integer=omitted=0
 for name,col in zip(n['variables'],q['variables']):
  # Time-indexed columns use relative five-minute ticks; dated hour direction names stay absolute.
  match=re.search(r',([0-9]+)\]$',name)
  source=name[:match.start(1)]+str(int(match.group(1))+3)+name[match.end(1):] if match else name
  x=previous.get(source)
  if x is None or not math.isfinite(x):omitted+=1;continue
  if col['kind']!='C':
   if abs(x-round(x))>1e-6:omitted+=1;continue
   x=float(round(x))
  if (col['lb']is not None and x<col['lb'])or(col['ub']is not None and x>col['ub']):omitted+=1;continue
  col['start']=x;count+=1;integer+=col['kind']!='C'
 q['warmstart']=True
 def math_part(q):return dict(rows=q['rows'],objective=q['objective'],variables=[{k:v for k,v in c.items()if k!='start'}for c in q['variables']])
 assert math_part(q)==math_part(original);assert count>0 and integer>0
 digest=hashlib.sha256(json.dumps(math_part(q),sort_keys=True,separators=(',',':')).encode()).hexdigest()
 (p/'template.json').write_text(json.dumps(q,sort_keys=True))
 proof=dict(schema='gd.short.partial_mip_hint.v1',prior_source_pins={f:sources[f]for f in (nn[0],rr[0])},hint_columns=count,integer_hint_columns=integer,omitted_columns=omitted,shift_ticks=3,all_hint_bounds_and_integrality_checked=True,full_row_feasibility_claimed=False,solver_may_repair_or_ignore=True,math_sha256=digest,rows_objective_bounds_integer_domain_unchanged=True,native_calls=0,formal=False)
 with(p/'WARM_HINT.json').open('x')as f:json.dump(proof,f,sort_keys=True)
 return proof
