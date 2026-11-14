"""Exact fixed-curve prediction at authenticated acceptance clock, not model-cutoff mutation."""
from copy import deepcopy
from datetime import datetime
from exact_fixed import aligned_tick,validate_receipts
from thermal_prefix_reader import derive_prefix
def predict(case,incoming,names,primal,*,issue_tick,executed_cells=3,receipts=()):
 # Check all receipts identically before consuming new or inherited events.
 start=aligned_tick((datetime.fromisoformat(case['contract']['domain_start'])-datetime.fromisoformat(case['contract']['origin'])).total_seconds()/300,'domain clock')
 model_cut=aligned_tick((datetime.fromisoformat(case['contract']['cutoff'])-datetime.fromisoformat(case['contract']['origin'])).total_seconds()/300,'model cutoff clock')
 assert type(issue_tick)is int and issue_tick==start and model_cut<=issue_tick,'accepted issue clock must equal issued prefix start, not old model cutoff'
 cut=issue_tick
 leads=validate_receipts(case,receipts,cut)
 z=derive_prefix(case,incoming,names,primal,executed_cells=executed_cells)
 for state in z['boundary_states'].values():state['available_at']=case['contract']['domain_start']
 z.update(predicted_issued_prefix=True,model_cutoff_tick=model_cut,accepted_issue_tick=issue_tick,actual_execution_claimed=False)
 vals=dict(zip(names['variables'],primal))
 ages={};states={};old=incoming['thermal']
 for g,d in case['energy_inputs']['thermal'].items():
  age=d['initial_state_age_h']*12;assert abs(age-round(age))<1e-7,'declared age must align5min';anchor=start-round(age);phase=old[g]['phase'];disconnect=None
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
  end=start+executed_cells;ages[g]=(end-anchor)/12
  if z['boundary_states'][g]['phase']=='off':assert disconnect is not None
  states[g]=dict(boundary_tick=end,connection_anchor_tick=anchor,last_disconnect_tick=disconnect,connection_state_age_ticks=end-anchor,phase=z['boundary_states'][g]['phase'],event_id=z['boundary_states'][g]['event_id'],event_elapsed_ticks=z['boundary_states'][g]['event_elapsed_ticks'],remaining_curve=deepcopy(z['boundary_states'][g]['remaining_curve']),startup_class=None,startup_class_basis='not supplied: fixed single declared research curve only')
 z.update(thermal_age_h=ages,fixed_curve_state=states,typed_classification_supported=False,source_receipts_verified_by_caller=True)
 return z
