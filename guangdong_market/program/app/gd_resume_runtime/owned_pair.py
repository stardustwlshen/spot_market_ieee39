"""Owned due-hour mathematical pair: no native launch or restored live authority."""
from pathlib import Path
from copy import deepcopy
import json,shutil,hashlib
B=Path(__file__).resolve().parent
# Source is selected by the verified normal resume plan, never module constants.
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def historical_phase_names(old,source_pins):
 """Resolve one supported producer layout through immutable evidence, not discovery."""
 from gd_post_scuc_runtime.phase_runtime import digest
 old=Path(old).resolve();accepted=old/'actual/ACCEPTED_PAIR.json'
 def need(ok,msg):
  if not ok:raise ValueError(msg)
 need(source_pins.get(str(accepted))==sha(accepted),'历史数学回执未绑定')
 proof=read(accepted)['post_SCUC_phase'];lineage=old/'HISTORICAL_PAIR_LINEAGE.json'
 if lineage.is_file():
  need(source_pins.get(str(lineage))==sha(lineage),'历史接续来源未绑定')
  z=read(lineage);need(z['schema']=='gd.explicit_owned_historical_pair.v1' and z['new_phase_reconstructed'] is True and z['new_native_calls']==0,'历史接续来源格式错误')
  need(sha(z['historical_pair'])==z['historical_pair_sha256'],'历史原数学回执改变')
  phase_dir=old/'new_owned_phase'
 else:phase_dir=old/'post_scuc_phase'
 names=phase_dir/'full_build_2/names.json'
 need(source_pins.get(str(names))==sha(names),'历史完整阶段列名未绑定')
 result=read(names);need(digest(result)==proof['names'],'历史完整阶段列名与接受证明不一致')
 return result

def attach(x,out,old,*,source_pins,allow_new_binding=False):
 OLD=Path(old).resolve();w=x.host.window;hour=(w+2)//4
 from v6_host import e
 from gd_post_scuc_runtime.live_hook import LivePhase
 from gd_post_scuc_runtime import consumers
 h=x.host;c=x.post_controller;s=h.session
 e.need(w%4==2 and h.pending is None and hour not in c.accepted,'only due unselected hour candidate')
 for f,hs in source_pins.items():e.need(sha(f)==hs,'historical raw changed')
 out=Path(out).resolve();out.mkdir(exist_ok=False);p=out/'prepared';shutil.copytree(OLD/'prepared',p);a=out/'actual';a.mkdir()
 for stage in ('SCUC','SCED'):(a/stage).symlink_to(OLD/'actual'/stage,target_is_directory=True)
 n=read(p/'names.json');incoming=read(p/'CARRY.json');current=read(h.cp)
 for k in ('thermal','thermal_age_h','storage','notice_values','cutoff_tick','delivery_tick'):e.need(incoming[k]==current[k],'historical/new incoming boundary mismatch '+k)
 record=dict(summary_sha256=sha(p/'SUMMARY.json'),carry_path=str(h.cp),carry_sha256=sha(h.cp),ledger_id=h.lid,artifact_pins={str(f):sha(f)for f in p.glob('*.json')},research_commitments=read(p/'NOTICE_AVAILABILITY.json')['scheduled_research_commitments'])
 h.hooks.prepared[str(p)]=record;h.pending=p
 phase=LivePhase(h,out/'new_owned_phase')
 token=phase.register_research_declaration(c.declarations[hour][0],confirmed=True)['token'];phase.prepare(token);phase.target_hour=hour
 # Full normal rebuild must match the historical exact SCED coefficients and columns.
 oldq=e.read(OLD/'actual/SCED/request.json.gz');oldn=historical_phase_names(OLD,source_pins)
 for k in ('variables','rows','objective'):e.need(phase.candidate['model'][k]==oldq[k],'historical model mismatch '+k)
 e.need(phase.candidate['names']==oldn,'historical columns/rows changed')
 z=c.accepted_phase(phase) # normal source/phase/math registration, NO worker call
 original=read(OLD/'actual/ACCEPTED_PAIR.json')
 if not allow_new_binding:e.need(original['post_SCUC_phase']==phase.candidate['proof'],'original accepted phase proof differs')
 else:
  # A separate reviewed extension may change provenance, never the checked model.
  proof=consumers.proof(consumers.lookup(p,a))
  original=deepcopy(original);original.update(post_SCUC_phase=phase.candidate['proof'],SCED_audit=z['SCED_audit'],native_calls=0,historical_native_source=str(OLD/'actual'),execution_attempt_id=x.execution_attempt_id,SCUC_audit=proof['SCUC'])
  for k in ('SCED_response_kind','dual_certificate','dual_certificate_sha256','native_dual_passed'):original[k]=proof[k]
 # Preserve numerical receipt as historical source, with distinct new ownership lineage.
 (a/'ACCEPTED_PAIR.json').write_text(json.dumps(original,indent=2))
 lineage=dict(schema='gd.explicit_owned_historical_pair.v1',logical_branch=h.branch_id,execution_attempt_id=x.execution_attempt_id,historical_pair=str(OLD/'actual/ACCEPTED_PAIR.json'),historical_pair_sha256=sha(OLD/'actual/ACCEPTED_PAIR.json'),historical_native_calls=2,new_native_calls=0,new_phase_reconstructed=True,new_gate_required=True,old_candidate_not_issued=True,safety_release=False,formal=False)
 (out/'HISTORICAL_PAIR_LINEAGE.json').write_text(json.dumps(lineage,indent=2))
 gate=h.before_handoff_gate;gate._generated_after_verified_pair(p,a,original)
 result=h._finish(p,a,original) # current new gate, not copied BEFORE_HANDOFF
 e.need(hour in c.accepted and (out/f'H{hour}_RESEARCH_AGC_RECEIPT.json').is_file(),'new handoff selection absent')
 (out/'NEW_SELECTION_RESULT.json').write_text(json.dumps(result,indent=2));return result

def ordinary(x,old,*,source_pins):
 """Rebuild current ordinary window, compare exact completed pair, new gate/handoff."""
 from v6_host import e
 verify=lambda:[e.need(sha(f)==d,'historical evidence changed')for f,d in source_pins.items()]
 verify();h=x.host;old=Path(old);h.prepare();p=h.pending;a=p.parent/'actual';a.mkdir()
 for stage in ('SCUC','SCED'):(a/stage).symlink_to(old/'actual'/stage,target_is_directory=True)
 q=read(p/'template.json');raw=e.read(a/'SCUC/request.json.gz');names=read(p/'names.json')
 for k in ('variables','rows','objective'):e.need(q[k]==raw[k],'new/old ordinary full model differs '+k)
 e.need(names==read(old/'prepared/names.json'),'ordinary names changed')
 # Existing normal acceptance validates this owned mathematical model; no native call.
 proof=read(old/'actual/ACCEPTED_PAIR.json');proof.update(prepared_sha256=sha(p/'SUMMARY.json'),native_calls=0,historical_native_source=str(old/'actual'),execution_attempt_id=x.execution_attempt_id)
 (a/'ACCEPTED_PAIR.json').write_text(json.dumps(proof,indent=2));h.session.program.engine.checked_pair(p,a);h.before_handoff_gate._generated_after_verified_pair(p,a,proof)
 return h._finish(p,a,proof)
