"""Durable display export after live phase acceptance; never a restore capability."""
from pathlib import Path
import json,os
from .phase_runtime import need,digest

def persist(controller,phase,result,*,selection=None):
 from . import consumers
 record=controller.candidates[str(phase.p.resolve())]['record']
 need(selection is not None and selection['handoff_complete'] is True,'completed handoff selection required')
 need(record is consumers.lookup(phase.p,phase.p.parent/'actual'),'same registered accepted phase required')
 verified=consumers.verified_state(record)
 need(digest(verified)==digest(result),'accepted phase payload changed')
 declaration,decl_sha,_=controller.declarations[phase.target_hour]
 from .live_hook import read,sha
 need(sha(declaration)==decl_sha,'declaration changed')
 payload=dict(schema='gd.agc.accepted_research_display.v1',branch_id=controller.h.branch_id,hour_index=phase.target_hour,receipt=result['receipt'],receipt_digest=digest(result['receipt']),declaration=read(declaration),declaration_sha256=decl_sha,phase_proof=result['phase_proof'],source=result['source'],source_pins={**record['pins'],str(controller.candidates[str(phase.p.resolve())]['evidence']):selection['candidate_sha256']},AGC_semantics=result.get('AGC_semantics',{}),export_kind='selected_after_handoff_research_display',selection=selection,planning_is_not_receipt=True,formal=False,safety_release=False,publication_allowed=False,live_restore_allowed=False)
 path=phase.p.parent/f'H{phase.target_hour}_RESEARCH_AGC_RECEIPT.json'
 # Exclusive creation. A failed/partial write blocks this attempt, never retries.
 with path.open('x') as f:
  json.dump(payload,f,ensure_ascii=False,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
 return path
