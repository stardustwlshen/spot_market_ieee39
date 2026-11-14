"""Identity-preserving precision wiring for an explicitly selected new process.
Call after post consumers and dual adapter installation, before math memo wrapping.
No solver call is made by installation. Frozen original files remain untouched.
"""
from pathlib import Path
from .policy import Policy,Proxy,WORKER,sha

def install(program):
 if hasattr(program,'_explicit_ipm_crossover_policy'):
  return program._explicit_ipm_crossover_policy
 # Capture callables, not a recursive reference to later overwritten attributes.
 base=Proxy(program);base.stage=program.stage
 base.engine=Proxy(program.engine);base.engine.worker=Proxy(program.engine.worker)
 base.engine.worker.native=program.engine.worker.native
 policy=Policy(base)
 policy.pins[str(Path(__file__).resolve())]=sha(__file__)
 old_checked=program.engine.checked_pair
 def checked(prepared,actual):
  policy.verify();actual=Path(actual);jobpath=actual/'SCED/JOB.json'
  if jobpath.exists():
   job=program.read(jobpath)
   if Path(job['worker']).resolve()==WORKER.resolve():
    # Worker identity, one-shot counters, full request/readback and strict KKT.
    # Full prepared/SCUC/phase mathematical binding remains old_checked's job.
    q=program.load(job['request']);n=program.read(Path(prepared)/'names.json')
    # Registered phase names may add fixed planning columns; get trusted names.
    try:
     from gd_post_scuc_runtime import consumers
     record=consumers.lookup(prepared,actual)
     if record is not None:n=consumers.verified_state(record)['names']
    except ImportError:
     pass
    policy.stage(actual.parent,'SCED',q,n,False)
  return old_checked(prepared,actual)
 program.stage=policy.stage
 program.engine.worker.native=policy.execute
 program.engine.checked_pair=checked
 program._explicit_ipm_crossover_policy=policy
 return policy
