"""Mathematical storage boundary, NOT an execution/acceptance capability.
Only TypedSource may place this in a live snapshot; its existing host gate remains.
Dates must be explicit, including future dates. Cumulative energy is internal MWh.
"""
from pathlib import Path
from datetime import datetime, timedelta
import copy, hashlib, json, math

def need(ok, message):
 if not ok: raise ValueError('cycle boundary: '+message)
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_bytes())
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def finite(x): return type(x) in (int,float) and math.isfinite(x)

def produce(request, accepted, report):
 """Called after checked_pair by TypedSource, using CURRENT accepted prep, not handoff."""
 p=Path(report['prepared']).resolve(); a=Path(report['actual']).resolve()
 need(report['window']==request['window']==accepted['window'],'window')
 need(request['run_id']==accepted['run_id'],'branch')
 files=[p/'BOUNDARY.json',p/'CARRY.json',p/'SUMMARY.json',p/'derived_case.json',p/'template.json',a/'ACCEPTED_PAIR.json',a/'SCED/COMPLETED.json']
 bd=read(files[0]); carry=read(files[1]); summary=read(files[2]); pair=read(files[5]); done=read(files[6])
 need(pair['prepared_sha256']==sha(p/'SUMMARY.json'),'accepted SCED prepared input')
 need(pair['SCED_response_sha256']==done['response']['stored_sha256'],'accepted SCED stored response')
 need(done['response']['raw_sha256']==accepted['SCED_response_sha256']==report['SCED_response_sha256'],'SCED response')
 response=Path(done['response']['path']);need(sha(response)==done['response']['stored_sha256'],'SCED bytes');files.append(response)
 # The compiler pins the original incoming file, then serializes a JSON copy.
 # Bind BOTH files; whitespace/key order may differ but no state may differ.
 incoming_hash=summary['carry_sha256']
 if incoming_hash!=sha(p/'CARRY.json'):
  candidates=[Path(name) for name,digest in summary['source_pins'].items() if digest==incoming_hash]
  need(len(candidates)==1,'ambiguous/missing original incoming carry binding')
  original=candidates[0]
  need(sha(original)==incoming_hash,'original incoming carry changed')
  need(read(original)==carry,'prepared carry state differs from compiler-bound original')
  files.append(original)

 first=request['delivery_tick'];zero=request.get('rt_clock_mode')=='provincial_15min_execution';cut=first
 need(zero,'provincial cycle contract rejects old 5-minute clock')
 need(type(first)is int and first>=0,'tick')
 need(carry['delivery_tick']==carry['issued_end_exclusive']==first and carry['cutoff_tick']==carry['executed_end_exclusive']==cut,'issued/executed range')
 need(carry['origin']==request['origin'] and carry['cutoff']==request['cutoff'],'origin/availability')
 origin=datetime.fromisoformat(request['origin']); cutoff=datetime.fromisoformat(request['cutoff'])
 need(cutoff==origin+timedelta(minutes=15*cut),'cutoff time')
 need(datetime.fromisoformat(carry['boundary_at'])==origin+timedelta(minutes=15*first),'boundary time')
 need(carry['seed_case_sha256']==request['case_sha256'],'input case')
 need(bd['storage']==carry['storage'],'storage boundary/carry')
 return dict(schema='gd.price.storage_cycle_boundary.v1',units='used_internal_mwh',branch_id=accepted['run_id'],window=accepted['window'],SCED_response_sha256=accepted['SCED_response_sha256'],case_sha256=request['case_sha256'],origin=request['origin'],delivery_tick=first,previous_tick=first-1,available_at=request['cutoff'],boundary_at=carry['boundary_at'],executed_end_exclusive=cut,issued_range=[cut,first],predicted_issued_prefix=carry['predicted_issued_prefix'],prepared=str(p),actual=str(a),storage=copy.deepcopy(bd['storage']),pins={str(f):sha(f)for f in files},formal=False,safety_release=False)

def validate(contract, request, accepted, case, values, *, report):
 """Validate data only. Never registers a carry, source, acceptance or safety release."""
 expected=produce(request,accepted,report)
 need(contract==expected,'source/contract binding')
 need(set(contract['storage'])==set(case['batteries']),'resource set')
 first=request['delivery_tick'];origin=datetime.fromisoformat(request['origin']);days={(origin+timedelta(minutes=15*(first+i))).date().isoformat() for i in range(min(8,96-first))}
 result={}
 for u,d in case['batteries'].items():
  state=contract['storage'][u]; energy=state['initial_energy_mwh'];power=state['initial_power_mw']
  need(finite(energy) and d['energy_min_mwh']-1e-9<=energy<=d['energy_max_mwh']+1e-9,'SOC range')
  need(finite(power) and -d['charge_max_mw']-1e-9<=power<=d['discharge_max_mw']+1e-9,'power range')
  if first:need(abs(values[f'battery.energy[{u},{first-1}]']-energy)<=1e-10,'SOC mismatch')
  if first:need(abs(values[f'battery.power[{u},{first-1}]']-power)<=1e-10,'previous power mismatch')
  budgets=state['cycle_budget'];need(set(budgets)==set(d['cycle_budget']) and days<=set(budgets),'explicit date set')
  result[u]={}
  for date,budget in budgets.items():
   need(datetime.fromisoformat(date).date().isoformat()==date,'date format')
   limit=budget['limit_cycles'];used=budget['used_internal_mwh']
   need(finite(limit) and limit>=0 and limit==d['cycle_budget'][date]['limit_cycles'],'cycle limit')
   need(finite(used) and 0<=used<=2*d['energy_capacity_mwh']*limit+1e-10,'internal MWh range')
   result[u][date]=used
 return result

def from_request(request, accepted, case, values):
 contract=request['rolling_cycle_boundary']; snapshot=read(request['typed_source_path'])
 need(snapshot['branch_id']==accepted['run_id'] and snapshot['window']==accepted['window'],'snapshot branch/window')
 need(snapshot['accepted']==accepted and snapshot['request']['rolling_cycle_boundary']==contract,'snapshot contract')
 report=snapshot['reports'][-1]
 for f,h in contract['pins'].items():need(snapshot['source_pins'].get(f)==h and sha(f)==h,'snapshot pin')
 return validate(contract,request,accepted,case,values,report=report)
