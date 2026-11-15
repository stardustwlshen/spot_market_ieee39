"""Candidate evidence is not consumable. Commit only after same-host handoff.
No restoration from files, new solver, AC invocation or policy override.
"""
from pathlib import Path
from copy import deepcopy
import json,os
from .phase_runtime import need,digest
from .live_hook import read,sha
from . import consumers

def stage(c,phase):
 key=str(phase.p.resolve());hour=phase.target_hour
 need(key not in c.candidates and hour not in c.accepted and hour not in c.h.agc.issued,'candidate occurrence/issued hour immutable')
 z=consumers.register(phase);record=consumers.lookup(phase.p,phase.p.parent/'actual')
 path=phase.p.parent/f'H{hour}_AGC_MATH_CANDIDATE.json'
 payload=dict(schema='gd.agc.math_candidate.v1',branch_id=c.h.branch_id,hour_index=hour,receipt=z['receipt'],receipt_digest=digest(z['receipt']),phase_proof=z['phase_proof'],source_pins=dict(record['pins']),consumable=False,selected=False,safety_release=False,formal=False)
 with path.open('x') as f:json.dump(payload,f,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
 c.candidates[key]=dict(record=record,phase=phase,evidence=path,evidence_sha256=sha(path),identity=(c.h.window,str(c.h.cp),c.h.lid),issued=deepcopy(c.h.agc.issued))
 return z

def commit(c,p,actual,decision,nxt,*,grant):
 h=c.h;key=str(Path(p).resolve());entry=c.candidates.get(key)
 if entry is None:return None # no newly due hour in this window
 need(grant is c._receipt_commit_grant,'runner commit capability required')
 need(h.pending==p and entry['identity']==(h.window,str(h.cp),h.lid),'candidate host clock changed')
 phase=entry['phase'];hour=phase.target_hour
 need(hour not in c.accepted and hour not in h.agc.issued,'issued hour immutable')
 need(h.agc.issued==entry['issued'],'previously issued receipts changed')
 need(sha(entry['evidence'])==entry['evidence_sha256'],'candidate evidence changed')
 need(Path(actual).resolve()==phase.p.parent/'actual','wrong actual source')
 gate=getattr(h,'before_handoff_gate',None)
 need(gate is not None and gate.host is h and gate.last==decision,'same complete handoff gate required')
 need(decision.get('carry_advance_allowed') is True and decision.get('branch_id')==h.branch_id and decision.get('window')==h.window,'gate did not select current candidate')
 dc_selected=(getattr(gate,'model_scope',None)=='active_power_dc' and decision.get('model_scope')=='active_power_dc' and decision.get('status')=='DC_VERIFIED_PAIR_SELECTED' and decision.get('ac_assessment')=='not_in_model_scope')
 need(dc_selected or gate.counterfactual is True or decision.get('safety_release') is True,'no explicit selection authority')
 carry=Path(nxt['carry_path']).resolve()
 need(h.hooks.carries.get(str(carry))==nxt['carry_sha256']==sha(carry),'handoff carry not registered')
 need(nxt['ledger_id'] in h.hooks.ledgers,'handoff ledger absent')
 need(read(Path(actual)/'ACCEPTED_PAIR.json').get('status')=='accepted_research_pair','complete numerical pair missing')
 z=consumers.verified_state(entry['record'])
 need(entry['record'] is consumers.lookup(phase.p,phase.p.parent/'actual'),'candidate registration changed')
 selection=dict(schema='gd.agc.handoff_selection.v1',handoff_complete=True,kind='dc_model_simulation'if dc_selected else'explicit_counterfactual_research' if gate.counterfactual else 'gate_selected_research',before_handoff=deepcopy(decision),carry_path=str(carry),carry_sha256=sha(carry),candidate_sha256=entry['evidence_sha256'],safety_release=False,formal=False)
 if dc_selected:selection.update(model_scope='active_power_dc',ac_assessment='not_in_model_scope',reactive_power_modeled=False,execution_allowed=True,execution_scope='model simulation only',publication_allowed=False)
 from .receipt_output import persist
 persist(c,phase,z,selection=selection) # exclusive durable write must succeed first
 c.accepted[hour]=entry['record']
 c.planning_evidence.pop(hour,None);c.planning_tokens.pop(hour,None)
 return deepcopy(selection)
