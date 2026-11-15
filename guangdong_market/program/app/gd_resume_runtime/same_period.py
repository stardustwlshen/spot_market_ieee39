"""Executed first-quarter recovery evidence, never the rolling horizon endpoint."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timedelta
import gzip, hashlib, json, re
from content_hash import sha
MODE='provincial_15min_execution'
def read(path):
 p=Path(path);data=p.read_bytes();return json.loads(gzip.decompress(data) if p.suffix=='.gz' else data)
def need(ok,msg):
 if not ok:raise ValueError(msg)
def enabled(plan):return plan.get('rt_clock_mode')==MODE

def derive(output,window):
 out=Path(output).resolve();w=window;need(type(w)is int and 0<=w<96,'quarter index')
 plan=read(out/'PLAN.json');need(enabled(plan),'same-period PLAN required')
 folder=out/'pipeline/dispatch'/f'W{w}';result=read(folder/'RESULT.json');carry=read(folder/'EXACT_CARRY.json');incoming=read(folder/'prepared/CARRY.json');pair=read(folder/'actual/ACCEPTED_PAIR.json')
 first=w;end=first+1
 need(incoming.get('rt_clock_mode')==MODE and carry.get('rt_clock_mode')==MODE,'mixed checkpoint clock modes')
 need(result['window']==w and result['executed_end_exclusive']==end and result['issued_end_exclusive']==end,'same-period handoff clocks')
 need(carry['delivery_tick']==end and carry['cutoff_tick']==end and carry['executed_end_exclusive']==end and carry['issued_end_exclusive']==end,'executed carry is not first-quarter endpoint')
 need(incoming['delivery_tick']==first and incoming['cutoff_tick']==first,'incoming carry clock')
 need(pair['status']=='accepted_research_pair' and result['safety_release']is False,'accepted research pair required')
 cp=folder/'EXACT_CARRY.json'
 need(result['handoff']['carry_sha256']==sha(cp) and Path(result['handoff']['carry_path']).resolve()==cp.resolve(),'handoff carry identity')
 paths=[out/'PLAN.json',folder/'RESULT.json',cp,folder/'prepared/CARRY.json',folder/'actual/ACCEPTED_PAIR.json']
 if w:
  previous=out/'pipeline/dispatch'/f'W{w-1}/EXACT_CARRY.json';need(read(previous)==incoming,'previous execution state continuity');paths.append(previous)
 names=folder/'prepared/names.json';raw=folder/'actual/SCED/response.json.gz';request=folder/'actual/SCED/request.json.gz';completed=folder/'actual/SCED/COMPLETED.json'
 need(pair['SCED_response_sha256']==sha(raw),'SCED raw identity')
 n=read(names)['variables'];r=read(raw);need(len(n)==len(r['primal'])and len(n)==len(set(n)),'raw vector dimensions')
 local={};values={}
 for key,value in zip(n,r['primal']):
  m=re.search(r'(\d+)\]$',key)
  if m and int(m[1])<1:
   local[key]=value;values[key[:m.start(1)]+str(first+int(m[1]))+']']=value
 thermal={};storage={}
 for g,d in carry['thermal'].items():
  p=local[f'power[{g},0]'];u=local[f'online[{g},0]']
  if d['phase']=='stable':need(abs(p-d['end_power_mw'])<=1e-6,'executed thermal endpoint')
  thermal[g]=dict(mean_power_last_cell_MW=p,endpoint_power_MW=d['end_power_mw'],u=u,age_h=carry['thermal_age_h'][g],phase=d['phase'],remaining_curve=d.get('remaining_curve'),age_basis='current accepted SCED first executed 15-minute cell')
 for g,d in carry['storage'].items():
  energy=local[f'battery.energy[{g},0]'];need(abs(energy-d['initial_energy_mwh'])<=1e-6,'executed SOC must be first-quarter endpoint')
  storage[g]=dict(energy_mwh=energy,power_mw=d['initial_power_mw'],mode=d['initial_mode'],mode_age_h=d['initial_mode_age_h'],cycle_budget=deepcopy(d['cycle_budget']),hour_direction=d.get('initial_hour_direction'))
 origin=datetime.fromisoformat(carry['origin']);at=lambda t:(origin+timedelta(minutes=15*t)).isoformat();need(carry['boundary_at']==at(end),'execution timestamp')
 price_path=out/'pipeline/price'/f'q{w}/ACCEPTED.json';price=read(price_path)
 need(price['context']['day_quarter']==w and price['context']['run_id']==result['branch_id']and price['KKT']['passed']is True,'same-quarter price identity')
 need(price['context']['SCED_response_sha256']==read(completed)['response']['raw_sha256'],'price not bound to same-quarter SCED')
 paths.extend([names,raw,request,completed,price_path,Path(__file__).resolve()])
 hours=list(range((w+1)//4))
 for h in hours:
  p=out/'pipeline/price'/f'HOUR{h}.json';need(p.is_file(),'completed hour missing');paths.append(p)
 pins={str(p.resolve()):sha(p)for p in paths}
 state={k:deepcopy(carry[k])for k in ('thermal','thermal_age_h','storage','pumped_storage','pumped_storage_declaration_path','pumped_storage_declaration_sha256','notice_values','notice_requirements','fixed_curve_state','event_ledger_sha256')if k in carry}
 key=hashlib.sha256(json.dumps(dict(branch=result['branch_id'],window=w,first=first,end=end,raw=sha(raw)),sort_keys=True).encode()).hexdigest()
 contract=dict(rt_clock_mode=MODE,logical_executed_end_exclusive=end,next_window=w+1,pending_issued_cells=[],pending_source_window=None,pending_issued_values={},next_model_cutoff_tick=end,next_optimization_delivery_tick=end,predicted_optimization_initial_state_is_not_current_execution=False,expected_next_handoff_executed_end_exclusive=end+1,semantics='Resume from current first-quarter executed endpoint; future horizon decisions are not executed, issued, or settled.')
 if w==95:
  contract.update(terminal_day_complete=True,next_window=None,next_model_cutoff_tick=None,next_optimization_delivery_tick=None,expected_next_handoff_executed_end_exclusive=None,semantics='Simulated day ends at24:00; no next-day execution is authorized or required.')
 return dict(schema='gd.executed_quarter.readonly_evidence.v1',rt_clock_mode=MODE,window=w,branch_id=result['branch_id'],execution_basis='model simulation first-quarter execution, not telemetry',origin=carry['origin'],interval=dict(first_tick=first,end_exclusive=end,start=at(first),end=at(end),tick_minutes=15),executed_cells=list(range(first,end)),executed_boundary=dict(tick=end,at=at(end),state=state),executed_boundary_at=at(end),executed_state=dict(thermal=thermal,storage=storage,pumped_storage=deepcopy(carry.get('pumped_storage',{}))),execution_values=values,source_window=w,source_raw_response=str(raw.resolve()),source_raw_file_sha256=sha(raw),source_names=str(names.resolve()),issued_carry=dict(path=str(cp.resolve()),sha256=sha(cp),executed_end=end,issued_end=end,not_executed_range=[]),resume_contract=contract,idempotency_key=key,available_price_quarter=w,next_price_quarter_not_execution=w+1,accounting=dict(included_ticks=list(range(first,end)),cumulative_executed_end_exclusive=end,settled_hours_available=hours,settled_end_exclusive=4*len(hours),pending_partial_hour_cells=list(range(4*len(hours),end)),cumulative_totals_not_materialized=True,duplicate_policy='same branch/window/raw identity is not a new executed interval',price_not_execution=True),source_pins=pins,arithmetic_only=True,new_native_calls=0,KKT_replayed=False,safety_release=False,formal_bill=None)

def validate(state,output,window):
 expected=derive(output,window)
 need(state==expected,'same-period executed projection differs from bound current raw first-quarter state')
