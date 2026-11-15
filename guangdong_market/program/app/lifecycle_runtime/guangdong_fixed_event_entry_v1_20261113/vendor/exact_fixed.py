"""Fixed-declared-curve exact age adapter; no invented hot/warm/cold classes."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys,math,json,hashlib
sys.path.insert(0,str(Path(__file__).resolve().parent))
from thermal_prefix_reader import derive_prefix

def aligned_tick(value,label):
 assert type(value)in(int,float) and math.isfinite(value) and abs(value-round(value))<=1e-9,label+' must align15min'
 return round(value)

def validate_receipts(case,receipts,cut):
 leads={g:aligned_tick(c['notice_h']*4,'notice lead')for g,c in case['curves'].items()}
 assert all(v>=0 for v in leads.values()),'negative notice lead'
 seen=set()
 for r in receipts:
  raw={k:v for k,v in r.items()if k!='receipt_hash'}
  assert r['receipt_hash']==hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),'receipt digest'
  assert r.get('issued') is True and isinstance(r.get('source'),str) and r['source'].strip(),'receipt not issued/sourced'
  assert type(r['begin_tick'])is int and type(r['issued_tick'])is int,'integer receipt clocks'
  assert r.get('origin')==case['contract']['origin'] and r['issued_tick']<=cut and r['issued_tick']<=r['begin_tick'],'receipt clock'
  g,phase=r['unit'],r['phase'];assert g in case['curves'] and phase in ('startup','shutdown'),'receipt resource/phase'
  key=(g,phase,r['begin_tick']);assert key not in seen,'duplicate receipt';seen.add(key)
  expected=case['curves'][g][phase]
  assert len(r['rows'])==len(expected) and all(all(math.isfinite(a[k]) and abs(a[k]-b[k])<1e-8 for k in ('begin','mean','end'))for a,b in zip(r['rows'],expected)),'receipt full curve mismatch'
  if phase=='startup':
   assert r['issued_tick']+leads[g]<=r['begin_tick'],'startup notice lead'
   if 'notice_tick'in r:assert type(r['notice_tick'])is int and r['notice_tick']==r['begin_tick']-leads[g] and r['issued_tick']<=r['notice_tick'],'notice target mismatch'
 return leads

def derive(case,incoming,names,primal,*,executed_cells=1,receipts=()):
 # Check all receipts identically before consuming new or inherited events.
 start=aligned_tick((datetime.fromisoformat(case['contract']['domain_start'])-datetime.fromisoformat(case['contract']['origin'])).total_seconds()/900,'domain clock')
 cut=aligned_tick((datetime.fromisoformat(case['contract']['cutoff'])-datetime.fromisoformat(case['contract']['origin'])).total_seconds()/900,'cutoff clock')
 leads=validate_receipts(case,receipts,cut)
 z=derive_prefix(case,incoming,names,primal,executed_cells=executed_cells)
 vals=dict(zip(names['variables'],primal))
 ages={};states={};old=incoming['thermal']
 for g,d in case['energy_inputs']['thermal'].items():
  age=d['initial_state_age_h']*4;assert abs(age-round(age))<1e-7,'declared age must align15min';anchor=start-round(age);phase=old[g]['phase'];disconnect=None
  # No historic disconnect invented for a currently online unit.
  if phase=='off':disconnect=anchor
  scheduled=[]
  if phase in ('startup','shutdown'):
   assert old[g]['event_id'] and old[g]['remaining_curve'],'inherited event provenance'
   begin=start-old[g]['event_elapsed_ticks']
   matches=[r for r in receipts if r['unit']==g and r['phase']==phase and r['begin_tick']==begin]
   assert len(matches)==1,'inherited active event receipt required'
   expected=matches[0]['rows'][old[g]['event_elapsed_ticks']:]
   assert len(expected)==len(old[g]['remaining_curve']) and all(all(abs(a[k]-b[k])<1e-8 for k in ('begin','mean','end'))for a,b in zip(expected,old[g]['remaining_curve'])),'inherited receipt curve mismatch'
   if phase=='shutdown':scheduled.append(start+len(old[g]['remaining_curve']))
  for j in range(executed_cells):
   for stem in ('start','stop'):
    v=vals[f'{stem}[{g},{j}]'];assert abs(v-round(v))<1e-6 and round(v)in(0,1)
    if round(v):
     kind='startup'if stem=='start'else'shutdown';at=start+j
     matches=[r for r in receipts if r['unit']==g and r['phase']==kind and r['begin_tick']==at]
     assert len(matches)==1,'executed new event needs exactly one explicit research issued receipt'
     r=matches[0];assert r['issued'] is True and r['source'] and r['receipt_hash'] and r['issued_tick']<=cut and r['issued_tick']<=at,'receipt unavailable'
     expected=case['curves'][g][kind];assert len(r['rows'])==len(expected) and all(all(abs(a[k]-b[k])<1e-8 for k in ('begin','mean','end'))for a,b in zip(r['rows'],expected)),'receipt curve mismatch'
     if stem=='start':assert r['issued_tick']+leads[g]<=at,'startup notice lead';anchor=at
     else:scheduled.append(at+len(expected))
   # connection changes to off only at shutdown completion, not stop initiation.
   if start+j+1 in scheduled:anchor=start+j+1;disconnect=anchor
  end=start+executed_cells;ages[g]=(end-anchor)/4
  if z['boundary_states'][g]['phase']=='off':assert disconnect is not None
  states[g]=dict(boundary_tick=end,connection_anchor_tick=anchor,last_disconnect_tick=disconnect,connection_state_age_ticks=end-anchor,phase=z['boundary_states'][g]['phase'],event_id=z['boundary_states'][g]['event_id'],event_elapsed_ticks=z['boundary_states'][g]['event_elapsed_ticks'],remaining_curve=deepcopy(z['boundary_states'][g]['remaining_curve']),startup_class=None,startup_class_basis='not supplied: fixed single declared research curve only')
 z.update(thermal_age_h=ages,fixed_curve_state=states,typed_classification_supported=False,source_receipts_verified_by_caller=True)
 return z
