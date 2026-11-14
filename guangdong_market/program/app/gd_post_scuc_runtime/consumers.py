"""In-memory registration of real post-SCUC phases; never restored from JSON.
Old engine/pair verifiers stay intact for all unregistered occurrences.
"""
from pathlib import Path
from copy import deepcopy
from .phase_runtime import digest,need
_PHASES={}
_CONTEXTS={}
_HISTORICAL_CONTEXTS={}
_ORIGINAL={}
# Candidate acceleration only. A fingerprint is NEVER acceptance authority.
_PAIR_INDEX={}
_INDEX_RECORDS={}
def _fingerprint(q,u,eq,r,n):
 # A non-cryptographic candidate key ONLY. Python numeric equality implies
 # equal tuple hashes (1 == 1.0 == True); collisions still require _identity.
 import math
 up=u['primal'];rp=r['primal'];names=n['variables']
 if not all(isinstance(x,(list,tuple))for x in (up,rp,names)):
  raise TypeError('unsupported candidate sequence')
 for values in (up,rp):
  if not all(isinstance(x,(int,float))and(not isinstance(x,float)or math.isfinite(x))for x in values):
   raise TypeError('unsupported candidate primal')
 return len(up),len(rp),len(names),hash(tuple(up)),hash(tuple(rp))
def _index_register(k,record):
 from v6_host import e,sha
 phase=record['phase'];a=phase.p.parent/'actual'
 paths=[a/st/f for st in ('SCUC','SCED')for f in ('request.json.gz','response.json.gz')]
 values=[e.read(p)for p in paths]
 guard={str(p):sha(p)for p in paths}
 need(all(record['pins'].get(p)==h for p,h in guard.items()),'index source differs from validated raw')
 names=deepcopy(phase.candidate['names']);fp=_fingerprint(*values,names)
 _INDEX_RECORDS[k]=(record,guard,names)
 _PAIR_INDEX.setdefault(fp,[]).append(k)
def _index_candidates(q,u,eq,r,n):
 from v6_host import sha
 # An old fingerprint cannot route changed registered raw into the ordinary
 # verifier. Check compressed bytes (no JSON/decompression), including on misses.
 need(set(_INDEX_RECORDS)==set(_PHASES),'phase index registration drift')
 for k,(record,guard,names)in _INDEX_RECORDS.items():
  need(_PHASES[k]is record,'phase index occurrence replaced')
  for p,h in guard.items():need(sha(p)==h,'registered identity raw drift')
  need(record['phase'].candidate['names']==names,'registered identity names drift')
 try:fp=_fingerprint(q,u,eq,r,n)
 except (TypeError,ValueError,KeyError,OverflowError):return list(_PHASES.values()) # preserve legacy behavior for unsupported inputs
 return [_PHASES[k]for k in _PAIR_INDEX.get(fp,())]

def key(p,a):return str(Path(p).resolve()),str(Path(a).resolve())
def lookup(p,a):return _PHASES.get(key(p,a))
def register(phase):
 from .live_hook import LivePhase
 need(type(phase)is LivePhase,'fresh live phase required');phase.verify_source();z=phase.validate_sced()
 k=key(phase.p,phase.p.parent/'actual');need(k not in _PHASES,'phase occurrence already registered')
 from v6_host import sha
 pins={**phase.pins,**phase.h.session.registry['source_pins']}
 for stage in ('SCUC','SCED'):
  for name in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz'):
   f=phase.p.parent/'actual'/stage/name;pins[str(f)]=sha(f)
 record=dict(phase=phase,result=deepcopy(z),result_digest=digest(z),pins=pins,published_prior=deepcopy(phase.h.agc.issued))
 _index_register(k,record);_PHASES[k]=record;return z

def export_case(case):
 # Match fixed builder_user_v2 derived_case serialization exactly. Keep the
 # private captured builder context (and its digest) typed and unchanged.
 from dataclasses import is_dataclass,asdict
 from v6_host import installed
 serial=deepcopy(case);h=serial['energy_inputs']['horizon']
 if is_dataclass(h):serial['energy_inputs']['horizon']=asdict(h)
 engine=installed().engine
 from smm.gd_october_job_codec import dumps
 return engine.loads(dumps(serial))

def case_for(p,a,default):
 x=lookup(p,a)
 if x is None:
  historical=_HISTORICAL_CONTEXTS.get(key(p,a))
  if historical is not None:return export_case(verified_state(historical)['effective_case'])
  c=_CONTEXTS.get(str(Path(p).resolve()))
  if c is None:return default
  from v6_host import sha
  c['controller'].h.session.verify_sources()
  for f,h in c['pins'].items():need(sha(f)==h,'prepared effective context drift')
  need(digest(c['case'])==c['digest'],'effective context changed');need(digest(c['AGC_semantics'])==c['semantics_digest'],'forecast semantics changed');return export_case(c['case'])
 return export_case(verified_state(x)['effective_case'])
def proof(x):
 z=verified_state(x);phase=x['phase'];c=z['certificate']
 return dict(SCUC=phase.phase.audit(phase.phase.q,phase.phase.x,None),SCED_KKT=z['SCED_audit']['KKT'],**c,post_SCUC_phase=deepcopy(phase.candidate['proof']))
def verified_state(x):
 from gd_resume_runtime.historical_hour import HistoricalSelected,verified_history
 if type(x)is HistoricalSelected:return verified_history(x)
 from v6_host import sha
 x['phase'].h.session.verify_sources()
 for hour,receipt in x['published_prior'].items():need(x['phase'].h.agc.issued.get(hour)==receipt,'previously published receipt mutated')
 for p,h in x['pins'].items():need(sha(p)==h,'registered phase source drift')
 need(digest(x['result'])==x['result_digest'],'registered phase payload drift')
 f=x['phase'].p.parent/'actual/ACCEPTED_PAIR.json'
 if f.exists():
  from v6_host import read
  need(read(f).get('post_SCUC_phase')==x['result']['phase_proof'],'stored phase certificate changed')
 return x['result']
def _identity(record,q,u,eq,r,n):
 from v6_host import e
 x=record['phase']
 return (q==e.read(x.p.parent/'actual/SCUC/request.json.gz')and u==e.read(x.p.parent/'actual/SCUC/response.json.gz')and eq==e.read(x.p.parent/'actual/SCED/request.json.gz')and r==e.read(x.p.parent/'actual/SCED/response.json.gz')and n==x.candidate['names'])
def install(program,event):
 if id(program)in _ORIGINAL:return
 original_pair=program.engine.checked_pair;original_verify=event.verify_pair
 _ORIGINAL[id(program)]=(original_pair,original_verify)
 def checked(p,a):
  x=lookup(p,a)
  if x is None:return original_pair(p,a)
  z=verified_state(x);return z['names'],z['SCED_response']
 def verified(q,u,eq,r,n):
  # Exact original file identity plus re-derived phase is mandatory; flags/hashes
  # in supplied JSON alone cannot create a registered occurrence.
  for x in _index_candidates(q,u,eq,r,n):
   if _identity(x,q,u,eq,r,n):return proof(x)
  return original_verify(q,u,eq,r,n)
 program.engine.checked_pair=checked;event.verify_pair=verified

def source_state(p,a):
 x=lookup(p,a)
 if x is None:return None
 return deepcopy(verified_state(x))


def register_context(controller,prepared,case):
 from .controller import Controller
 from v6_host import sha
 need(type(controller)is Controller and controller.h.pending==Path(prepared),'registered current controller context required')
 p=Path(prepared).resolve();record=controller.h.hooks.prepared[str(p)]
 need(digest(case)==digest(controller.last_case),'actual captured full-builder context required')
 _CONTEXTS[str(p)]=dict(controller=controller,case=deepcopy(case),digest=digest(case),pins={**record['artifact_pins'],**controller.h.session.registry['source_pins']},AGC_semantics=deepcopy(controller.forecast_context),semantics_digest=digest(controller.forecast_context))

def effective_awards(p,a,default):
 case=case_for(p,a,None)
 return default if case is None else deepcopy(case['agc_awards'])


def semantics_for(p,a):
 record=lookup(p,a)
 if record is not None:return deepcopy(verified_state(record).get('AGC_semantics',{}))
 historical=_HISTORICAL_CONTEXTS.get(key(p,a))
 if historical is not None:return deepcopy(verified_state(historical).get('AGC_semantics',{}))
 context=_CONTEXTS.get(str(Path(p).resolve()))
 if context is None:return {}
 case_for(p,a,None);return deepcopy(context['AGC_semantics'])


def register_historical_context(prepared,actual,record):
 """Read-only accepted historical consumer case; never register a LivePhase."""
 from gd_resume_runtime.historical_hour import HistoricalSelected,verified_history
 need(type(record)is HistoricalSelected,'owned historical selected fact required')
 z=verified_history(record);k=key(prepared,actual)
 need(k not in _PHASES and k not in _HISTORICAL_CONTEXTS,'historical consumer occurrence already registered')
 h=record.controller.h;p,a=h.accepted_source(4*record.hour-2)
 need(k==key(p,a),'historical consumer belongs to another occurrence')
 need(digest(z['effective_case'])==z['phase_proof']['case'],'historical consumer effective case changed')
 _HISTORICAL_CONTEXTS[k]=record
