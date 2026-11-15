"""Create a new initial research run through the installed producer factory.
Not restore, migration, source-pin rewriting or old-execution continuation.
"""
from pathlib import Path
import sys,json,hashlib
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
def initialize_new_research_base(*,program_root,selected_DA,day_declarations,output,research=False):
 if research is not True:raise ValueError('explicit new research initialization required')
 root=Path(program_root).resolve();da=Path(selected_DA).resolve();days=Path(day_declarations).resolve();out=Path(output).resolve()
 if out.exists():raise ValueError('new output must not exist; not a restore operation')
 artifact=read(da/'ARTIFACTS.json');case=da/artifact['inputs']['case']['path'];response=artifact['stages']['da_sced']['expanded_response']['sha256'];declaration=read(days)
 if sha(case)!=artifact['inputs']['case']['sha256']:raise ValueError('DA case drift')
 for day in declaration['days'].values():
  if day['source']['case_sha256']!=sha(case)or day['source']['accepted_original_DA_response_sha256']!=response:raise ValueError('day declaration does not bind selected accepted DA')
 before={str(p):sha(p)for p in (days,da/'ARTIFACTS.json',case)};paths=list(sys.path);app=root/'app';rt=app/'lifecycle_runtime'
 try:
  sys.path[:0]=[str(app),str(rt),str(rt/'guangdong_event_host_candidate_v1_20261113')]
  from v6_host import installed
  program=installed();expected=root/'engines/guangdong_short_unified_v6/rt/guangdong_rt_short_user_cli_v5_20261112/program.py'
  if Path(program.__file__).resolve()!=expected:raise ValueError('installed program from another root; use a fresh worker process')
  result=program.seed(da,out,days);_,context=program.context(out);carry=read(out/'INITIAL_CARRY.json')
  if context['code_pins']!=program.pins()or not all(Path(p).is_relative_to(root)for p in context['code_pins']):raise AssertionError('new base source registration mismatch')
  if context['start_window']!=0 or carry['executed_end_exclusive']!=0 or carry['issued_end_exclusive']!=3:raise AssertionError('factory did not create initial research boundary')
  if any(sha(p)!=h for p,h in before.items()):raise AssertionError('input changed')
  receipt=dict(schema='gd.new_research_base.initialization.v1',factory=str(expected),factory_sha256=sha(expected),run_id=context['run_id'],selected_DA=str(da),selected_DA_run_id=context['selected_DA_run_id'],source_pins=before,new_code_pins_current_root=True,actual_executed_end=0,hypothetical_issued_end=3,kind='new independent research initial task, not old run recovery',native_calls=0,old_execution_progress_imported=False,formal=False)
  (out/'NEW_INITIALIZATION_RECEIPT.json').write_text(json.dumps(receipt,indent=2));return dict(result,receipt=receipt,base_run=str(out),instance=bind_initialization_instance(program_root=root,base_run=out))
 finally:sys.path[:]=paths

def bind_initialization_instance(*,program_root,base_run):
 """Add instance identity without editing deterministic factory scenario identity."""
 from uuid import uuid4
 root=Path(program_root).resolve();base=Path(base_run).resolve();context=read(base/'SHORT_CONTEXT.json');carry=read(base/'INITIAL_CARRY.json');receipt=base/'INITIALIZATION_INSTANCE.json'
 if receipt.exists():raise ValueError('instance already bound; read the existing receipt')
 if context['start_window']!=0 or carry['executed_end_exclusive']!=0 or carry['issued_end_exclusive']!=3:raise ValueError('not a fresh initial research boundary')
 for p,h in context['code_pins'].items():
  if not Path(p).is_relative_to(root)or sha(p)!=h:raise ValueError('current installed source mismatch')
 for p,h in context['seed_pins'].items():
  if sha(p)!=h:raise ValueError('new base input drift')
 z=dict(schema='gd.research.initialization_instance.v1',initialization_instance_id='research-init-'+uuid4().hex,base_run=str(base),program_root=str(root),scenario_run_id=context['run_id'],selected_DA=context['selected_DA'],context_sha256=sha(base/'SHORT_CONTEXT.json'),carry_sha256=sha(base/'INITIAL_CARRY.json'),actual_executed_end=0,hypothetical_issued_end=3,old_execution_progress_imported=False,native_calls=0,formal=False)
 with receipt.open('x')as f:json.dump(z,f,indent=2)
 return z
