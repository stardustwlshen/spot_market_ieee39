"""Uniform fixed incoming history view, not raw primal or issuance authority."""
from pathlib import Path
from copy import deepcopy
import hashlib,json,math
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def need(x,m):
 if not x:raise ValueError(m)
def history_view(*,case,carry,receipts,validate_receipts,values):
 """Pure arithmetic only. Caller authenticates carry/ledger; never accepts a host."""
 validate_receipts(case,receipts,carry['cutoff_tick'])
 x=deepcopy(values);trace={};first=carry['delivery_tick']
 for g,t in carry['thermal'].items():
  if t['phase']not in ('startup','shutdown'):continue
  need(not case['curves'][g].get('classes'),'typed curves unsupported')
  f=carry['fixed_curve_state'][g];elapsed=t['event_elapsed_ticks'];phase=t['phase']
  need(type(elapsed)is int and elapsed>0,'active prior row needs positive elapsed history')
  need(f['boundary_tick']==first and f['phase']==phase and f['event_id']==t['event_id'] and bool(t['event_id']) and f['event_elapsed_ticks']==elapsed,'fixed boundary identity')
  need(f['connection_state_age_ticks']==first-f['connection_anchor_tick'] and abs(carry['thermal_age_h'][g]*12-f['connection_state_age_ticks'])<1e-8,'connection age mismatch')
  begin=first-elapsed;need(begin>=0 and type(f['connection_anchor_tick'])is int and f['connection_anchor_tick']<=begin,'unsupported pre-origin event or connection anchor');matches=[r for r in receipts if r['unit']==g and r['phase']==phase and r['begin_tick']==begin];need(len(matches)==1,'one issued inherited receipt required');r=matches[0];rows=r['rows'];need(elapsed<len(rows),'active suffix empty')
  suffix=rows[elapsed:]
  for actual in (t['remaining_curve'],f['remaining_curve']):
   need(len(actual)==len(suffix) and all(all(abs(a[k]-b[k])<1e-8 for k in ('begin','mean','end'))for a,b in zip(actual,suffix)),'remaining suffix mismatch')
  prior=rows[elapsed-1];need(abs(prior['end']-t['end_power_mw'])<1e-8 and abs(suffix[0]['begin']-t['end_power_mw'])<1e-8,'incoming endpoint mismatch')
  if phase=='startup':need(f['connection_anchor_tick']==begin,'startup connection age mismatch')
  # Explicit receipt-derived history view. Never change accepted raw values.
  stem='start'if phase=='startup'else'stop'
  need(f'{stem}[{g},{begin}]'not in x,'history overlaps current raw domain')
  x[f'{stem}[{g},{begin}]']=1
  for key,v in [('power',prior['mean']),('online',1),('stable',0),('off',0)]:x[f'{key}[{g},{first-1}]']=v
  trace[g]=dict(receipt_hash=r['receipt_hash'],begin_tick=begin,elapsed_ticks=elapsed,previous_mean_mw=prior['mean'],previous_end_mw=prior['end'],source='issued fixed-curve history view; not accepted raw past primal')
 return x,trace

def bind_registered(*,hooks,record,case,carry,values,program_root,event_profile):
 from event_gate import bind_incoming
 active={g:t for g,t in carry['thermal'].items()if t['phase']in ('startup','shutdown')}
 if not active:return bind_incoming(values,carry['thermal'],carry['delivery_tick']),{},{}
 from state_adapter import PROFILE
 need(event_profile==PROFILE,'incoming active needs explicit fixed profile')
 import event_path as ep
 from exact_fixed import validate_receipts
 root=Path(program_root).resolve();vendor=root/'app/lifecycle_runtime/guangdong_fixed_event_entry_v1_20261113'
 need(Path(ep.__file__).resolve()==vendor/'event_path.py','existing host ledger verifier identity')
 import exact_fixed
 need(Path(exact_fixed.__file__).resolve()==vendor/'vendor/exact_fixed.py','existing fixed verifier identity')
 need(sha(record['carry_path'])==record['carry_sha256'] and hooks.carries[str(Path(record['carry_path']).resolve())]==record['carry_sha256'],'registered carry source')
 need(carry['event_ledger_sha256']==record['ledger_id'],'carry/ledger identity');ledger=hooks.ledgers[record['ledger_id']];ep.verify_ledger(ledger)
 need(ledger['run_id']==hooks.session.registry['run_id'] and ledger['case_identity']==hooks.session.registry['case_identity'] and ledger['origin']==carry['origin'],'ledger source identity')
 need(ledger['cutoff_tick']<=carry['cutoff_tick'],'ledger unavailable')
 pins=dict(carry['source_pins']);ref=carry['accepted_control_phase'];need(pins.get(ref['path'])==ref['sha256'],'phase not in carry source pins')
 for p,h in pins.items():need(sha(p)==h,'incoming source drift')
 phase=json.loads(Path(ref['path']).read_bytes());need(phase['predicted_prefix_end_tick']==carry['delivery_tick'] and phase['executed_clock_end_tick']==carry['executed_end_exclusive'] and phase['research_instruction_issue_tick']<=carry['cutoff_tick'],'accepted phase clocks')
 x=deepcopy(values);bind_incoming(x,{g:t for g,t in carry['thermal'].items()if g not in active},carry['delivery_tick'])
 x,trace=history_view(case=case,carry=carry,receipts=list(ledger['receipts'].values()),validate_receipts=validate_receipts,values=x)
 pins.update({str(vendor/'event_path.py'):sha(vendor/'event_path.py'),str(vendor/'vendor/exact_fixed.py'):sha(vendor/'vendor/exact_fixed.py')})
 return x,trace,pins
