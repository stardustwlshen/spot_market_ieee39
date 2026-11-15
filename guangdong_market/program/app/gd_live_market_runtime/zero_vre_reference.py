"""Source-bound numerical reference normalization; no authority, solve or raw edit.
Only zero-availability quarters are supported. Other negative values fail closed.
"""
from pathlib import Path
from copy import deepcopy
import json,gzip,hashlib,math
LIMIT=1e-10
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(v,msg):
 if not v:raise ValueError(msg)
def digest(z):return hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def read(p):return json.load(gzip.open(p,'rt'))if str(p).endswith('.gz')else json.loads(Path(p).read_bytes())
def canonicalize(z,case):
 x=z['values'];first=z['request']['delivery_tick'];changes=[]
 for b in case['energy_inputs']['buses']:
  for j in range(min(8,96-first)):
   ts=list(range(first+j,first+j+1));vs=[x[f"renewable_power['{b}',{t}]"]for t in ts]
   need(all(math.isfinite(v)for v in vs),'nonfinite VRE reference')
   if any(v<0 for v in vs):changes.append((b,ts,vs))
 if not changes:return x,dict(schema='gd.zero_vre.reference.v1',changes=[],source_values_unchanged=True)
 need(z['window']>=0,'negative seed DA reference requires separate sourced adapter')
 report=z['reports'][-1];need(report['window']==z['window']==z['accepted']['window'],'wrong RT source window')
 p=Path(report['prepared']);a=Path(report['actual']);paths=[p/'names.json',a/'SCED/request.json.gz',a/'SCED/response.json.gz']
 for f in paths:need(str(f)in z['source_pins'] and sha(f)==z['source_pins'][str(f)],'unregistered or changed raw source')
 need(digest(read(paths[2]))==z['accepted']['SCED_response_sha256']==report['SCED_response_sha256'],'wrong native response')
 n,q,r=map(read,paths);vi={v:i for i,v in enumerate(n['variables'])};ri={v:i for i,v in enumerate(n['constraints'])};out=deepcopy(x);proof=[]
 for b,ts,vs in changes:
  cells=[]
  for t,v in zip(ts,vs):
   local=t-first;name=f"renewable_power['{b}',{local}]";i=vi[name];rowname=f"c_renewable_available['{b}',{local}]";row=q['rows'][ri[rowname]]
   need(r['primal'][i]==v,'snapshot/raw mapping mismatch')
   need(case['energy_inputs']['renewable_mw'][b,t]==0,'nonzero available resource cannot use zero-only normalization')
   need(abs(v)<=LIMIT,'zero-reference residual exceeds fixed1e-10 limit')
   terms=dict(row['terms']);need(len(row['terms'])==len(terms)==2 and terms.get(i)==1 and row['lb']==row['ub']==0,'not exact zero-availability equality')
   other=next(k for k in terms if k!=i)
   need(terms[other]==1 and n['variables'][other]==f"renewable_curtailment['{b}',{local}]",'wrong curtailment source column')
   need(q['variables'][i]['lb']==q['variables'][other]['lb']==0,'nonnegative source bounds required')
   need(abs(r['primal'][other])<=LIMIT,'curtailment residual exceeds fixed limit')
   out[f"renewable_power['{b}',{t}]"]=0.
   cells.append(dict(tick=t,local_tick=local,raw=v,canonical=0.,delta=-v,source_row=rowname,row_sha256=digest(row)))
  proof.append(dict(bus=b,cells=cells))
 return out,dict(schema='gd.zero_vre.reference.v1',changes=proof,limit=LIMIT,source_pins={str(f):sha(f)for f in paths},source_values_unchanged=True,raw_model_unchanged=True,not_equivalent_to_inconsistent_floating_band=True,justification='exact source zero availability and two nonnegative summands imply zero; numerical reference normalization only')
