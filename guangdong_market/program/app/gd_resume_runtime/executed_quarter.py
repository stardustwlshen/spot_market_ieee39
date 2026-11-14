"""Read-only no-deviation executed boundary, distinct from next issued carry.
No telemetry assertion, solver, KKT replay or restored private authority.
"""
from pathlib import Path
from copy import deepcopy
import gzip,json,re,hashlib
from datetime import datetime,timedelta
from content_hash import sha

def read(p):
 p=Path(p);b=p.read_bytes();return json.loads(gzip.decompress(b)if p.suffix=='.gz'else b)
def need(v,m):
 if not v:raise ValueError(m)
def derive(output,window):
 out=Path(output).resolve();w=window;need(type(w)is int and 0<=w<96,'quarter index')
 folder=out/'pipeline/dispatch'/f'W{w}';result=read(folder/'RESULT.json');issued=read(folder/'EXACT_CARRY.json');incoming=read(folder/'prepared/CARRY.json');pair=read(folder/'actual/ACCEPTED_PAIR.json');first=3*w;end=first+3
 need(result['window']==w and result['executed_end_exclusive']==end and result['issued_end_exclusive']==end+3,'handoff clocks')
 need(pair['status']=='accepted_research_pair' and result['safety_release']is False,'accepted research handoff required')
 need(result['handoff']['carry_sha256']==sha(folder/'EXACT_CARRY.json')and Path(result['handoff']['carry_path']).resolve()==(folder/'EXACT_CARRY.json').resolve(),'issued carry identity')
 need(incoming['delivery_tick']==end and incoming['issued_end_exclusive']==end,'incoming previously issued boundary differs from newly executed end')
 pins={str(p.resolve()):sha(p)for p in (folder/'RESULT.json',folder/'EXACT_CARRY.json',folder/'prepared/CARRY.json',folder/'actual/ACCEPTED_PAIR.json')}
 for code in (Path(__file__).resolve(),Path(__file__).resolve().parents[1]/'content_hash.py',Path(__file__).resolve().parents[1]/'lifecycle_runtime/content_hash.py'):
  if code.is_file():pins[str(code)]=sha(code)
 plan=read(out/'PLAN.json');run=Path(plan['run']);warm=read(run/'seed/WARMSTART_SOURCE.json')
 if w:
  previous=out/'pipeline/dispatch'/f'W{w-1}';names=previous/'prepared/names.json';raw=previous/'actual/SCED/response.json.gz';prior=read(previous/'actual/ACCEPTED_PAIR.json')
  need(prior['SCED_response_sha256']==sha(raw),'executed-source raw hash differs');need(sha(previous/'EXACT_CARRY.json')==sha(folder/'prepared/CARRY.json'),'issued-to-executed carry changed')
  for p in (previous/'EXACT_CARRY.json',previous/'actual/ACCEPTED_PAIR.json',previous/'actual/SCED/COMPLETED.json'):pins[str(p.resolve())]=sha(p)
 else:
  names=Path(warm['names']);raw=Path(warm['response']);need(sha(names)==warm['names_sha256']and sha(raw)==warm['response_sha256'],'seed executed source');pins[str((run/'seed/WARMSTART_SOURCE.json').resolve())]=sha(run/'seed/WARMSTART_SOURCE.json')
 n=read(names)['variables'];r=read(raw);need(len(n)==len(r['primal'])and len(set(n))==len(n),'full raw vector')
 values={};local={}
 for k,v in zip(n,r['primal']):
  m=re.search(r'(\d+)\]$',k)
  if m and int(m[1])<3:
   local[k]=v;values[k[:m.start(1)]+str(first+int(m[1]))+']']=v
 pins[str(names.resolve())]=sha(names);pins[str(raw.resolve())]=sha(raw)
 state={k:deepcopy(incoming[k])for k in ('thermal','thermal_age_h','storage','notice_values','notice_requirements','fixed_curve_state','event_ledger_sha256')if k in incoming}
 thermal={};storage={}
 for g,d in incoming['thermal'].items():
  power=local[f'power[{g},2]'];u=local[f'online[{g},2]']
  if d['phase']=='stable':need(abs(power-d['end_power_mw'])<=1e-6,'stable executed endpoint differs')
  thermal[g]=dict(mean_power_last_cell_MW=power,endpoint_power_MW=d['end_power_mw'],u=u,age_h=incoming['thermal_age_h'][g],phase=d['phase'],remaining_curve=d.get('remaining_curve'),age_basis='accepted previously issued carry promoted only by current no-deviation research handoff')
 for g,d in incoming['storage'].items():
  energy=local[f'battery.energy[{g},2]'];need(abs(energy-d['initial_energy_mwh'])<=1e-6,'executed SOC differs from previously issued end state')
  storage[g]=dict(energy_mwh=energy,power_mw=d['initial_power_mw'],mode=d['initial_mode'],mode_age_h=d['initial_mode_age_h'],cycle_budget=deepcopy(d['cycle_budget']),hour_direction=d.get('initial_hour_direction'))
 origin=datetime.fromisoformat(incoming['origin']);at=lambda tick:(origin+timedelta(minutes=5*tick)).isoformat();need(incoming['boundary_at']==at(end),'executed boundary timestamp')
 current_names=folder/'prepared/names.json';current_raw=folder/'actual/SCED/response.json.gz';current_request=folder/'actual/SCED/request.json.gz'
 need(pair['SCED_response_sha256']==sha(current_raw),'pending issued raw identity')
 cn=read(current_names)['variables'];cr=read(current_raw);need(len(cn)==len(cr['primal']),'pending issued vector length')
 pending_values={}
 for k,v in zip(cn,cr['primal']):
  m=re.search(r'(\d+)\]$',k)
  if m and int(m[1])<3:pending_values[k[:m.start(1)]+str(end+int(m[1]))+']']=v
 for p in (current_names,current_raw,current_request):pins[str(p.resolve())]=sha(p)
 need(issued['delivery_tick']==end+3 and issued['cutoff_tick']==end,'pending forecast boundary clocks')
 resume_contract=dict(logical_executed_end_exclusive=end,next_window=w+1,pending_issued_cells=list(range(end,end+3)),pending_source_window=w,pending_source_names=str(current_names.resolve()),pending_source_names_sha256=sha(current_names),pending_source_raw=str(current_raw.resolve()),pending_source_raw_sha256=sha(current_raw),pending_issued_values=pending_values,next_model_cutoff_tick=end,next_optimization_delivery_tick=end+3,predicted_optimization_initial_state_is_not_current_execution=True,expected_next_handoff_executed_end_exclusive=end+3,consume_pending_once_key=dict(branch=result['branch_id'],period=w+1,first_tick=end,end_exclusive=end+3,raw_sha256=sha(current_raw)),semantics='Keep original notification/rolling contract. At next model cutoff, pending issued prefix is fixed from accepted prior SCED. Only a completed next no-deviation handoff advances executed clock and accounting; do not re-optimize or count the issued interval early.')
 quarter=out/'pipeline/price'/f'q{w}/ACCEPTED.json';price=read(quarter);need(price['context']['day_quarter']==w and price['context']['run_id']==result['branch_id']and price['KKT']['passed']is True,'executed quarter price source');pins[str(quarter.resolve())]=sha(quarter)
 # Economic totals remain authoritative existing HOUR records; pending cells are
 # identified, never silently settled twice or assigned the next quarter price.
 hours=list(range((w+1)//4));accounting_pins={}
 for h in hours:
  p=out/'pipeline/price'/f'HOUR{h}.json';need(p.is_file(),'completed hour arithmetic missing');accounting_pins[str(p.resolve())]=sha(p)
 pins.update(accounting_pins);key=hashlib.sha256(json.dumps(dict(branch=result['branch_id'],window=w,first=first,end=end,raw=sha(raw)),sort_keys=True).encode()).hexdigest()
 return dict(schema='gd.executed_quarter.readonly_evidence.v1',window=w,branch_id=result['branch_id'],execution_basis='counterfactual no-deviation research, not telemetry or authorized dispatch',origin=incoming['origin'],interval=dict(first_tick=first,end_exclusive=end,start=at(first),end=at(end),tick_minutes=5),executed_cells=list(range(first,end)),executed_boundary=dict(tick=end,at=at(end),state=state),executed_boundary_at=at(end),executed_state=dict(thermal=thermal,storage=storage),execution_values=values,source_window=w-1 if w else None,source_raw_response=str(raw.resolve()),source_raw_file_sha256=sha(raw),source_names=str(names.resolve()),issued_carry=dict(path=str((folder/'EXACT_CARRY.json').resolve()),sha256=sha(folder/'EXACT_CARRY.json'),executed_end=end,issued_end=end+3,not_executed_range=[end,end+3]),resume_contract=resume_contract,idempotency_key=key,available_price_quarter=w,next_price_quarter_not_execution=w+1,accounting=dict(included_ticks=list(range(first,end)),cumulative_executed_end_exclusive=end,settled_hours_available=hours,settled_end_exclusive=12*len(hours),pending_partial_hour_cells=list(range(12*len(hours),end)),cumulative_totals_not_materialized=True,duplicate_policy='same branch/window/raw identity is not a new executed interval',price_not_execution=True),source_pins=pins,arithmetic_only=True,new_native_calls=0,KKT_replayed=False,safety_release=False,formal_bill=None)

def write(output,window,path):
 from .hourly_durable import atomic
 z=derive(output,window);atomic(path,z);return z
