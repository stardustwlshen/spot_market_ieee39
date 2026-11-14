"""One completed SCUC, no SCED occurrence: rebuild once, reuse exact native bytes."""
from pathlib import Path
from copy import deepcopy
from .engine import need,read,sha,write,verify

def finish(x,old,*,source_pins,before_native):
 from v6_host import e
 h=x.host;s=h.session;old=Path(old).resolve();verify(source_pins)
 have_sced=(old/'actual/SCED/COMPLETED.json').is_file()
 need((old/'actual/SCUC/COMPLETED.json').is_file() and (have_sced or not(old/'actual/SCED').exists()),'仅支持完成SCUC且SCED从未启动或已明确完成')
 h.prepare();p=h.pending;record=h.hooks.prepared[str(p)];a=p.parent/'actual';need(not a.exists(),'新代次原生目录必须为空')
 q=read(p/'template.json');n=read(p/'names.json');oldn=read(old/'prepared/names.json')
 need(n==oldn,'历史SCUC名称不同');a.mkdir();(a/'SCUC').symlink_to(old/'actual/SCUC',target_is_directory=True)
 program=s.program;u=program.stage(p.parent,'SCUC',q,n,True) # exact request + integer/primal audit, no native
 write(a/'HISTORICAL_SCUC.json',dict(schema='gd.partial.completed_scuc.v1',new_native_calls=0,historical_native_calls=1,execution_attempt_id=x.execution_attempt_id,source=str(old/'actual/SCUC'),source_pins={str((old/'actual/SCUC'/f).resolve()):sha(old/'actual/SCUC'/f)for f in ('JOB.json','COMPLETED.json','request.json.gz','response.json.gz')},same_complete_model=True,safety_release=False))
 controller=x.post_controller;phase=controller.after_scuc(p)if controller else None
 lp=deepcopy(phase.candidate['model'])if phase else program.fixed_sced(q,u[1]['primal']);names=phase.candidate['names']if phase else n
 lp['options'].update(presolve='off',dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0,solver='simplex',run_crossover='off',ipm_optimality_tolerance=1e-12)
 old_phase=old/'post_scuc_phase/full_build_2'
 if phase is not None and old_phase.is_dir():
  from gd_post_scuc_runtime.phase_runtime import fixed
  for f in ('template.json','names.json'):need(source_pins.get(str(old_phase/f))==sha(old_phase/f),'历史未完阶段模型未绑定')
  historical_lp=fixed(read(old_phase/'template.json'),u[1]['primal'])
  for k in ('variables','rows','objective'):need(historical_lp[k]==lp[k],'未完阶段SCED完整数学改变：'+k)
  need(read(old_phase/'names.json')==names,'未完阶段SCED列名改变')
  write(a/'PARTIAL_PHASE_EQUAL.json',dict(full_SCED_mathematics_equal=True,historical_phase=str(old_phase),new_native_calls=0))
 if have_sced:(a/'SCED').symlink_to(old/'actual/SCED',target_is_directory=True)
 else:
  before_native(1,'W'+str(h.window)+'/SCED')
  program.engine.worker.native(a,'SCED',lp,program.B/'lp_ipm_worker.py',3600.)
 if phase:registered=controller.accepted_phase(phase);z=(registered['SCED_request'],registered['SCED_response'])
 else:z=program.stage(p.parent,'SCED',lp,names,False)
 proof=e.verify_pair(u[0],u[1],z[0],z[1],names)
 result=dict(schema='gd.event.host.accepted_pair.v2',status='accepted_research_pair',run_id=s.registry['run_id'],prepared_sha256=sha(p/'SUMMARY.json'),ledger_id=record['ledger_id'],SCUC_response_sha256=sha(a/'SCUC/response.json.gz'),SCED_response_sha256=sha(a/'SCED/response.json.gz'),SCUC_gap=u[1]['native_mip_gap'],SCUC_audit=program.checks(*u,n,True),SCED_audit=registered['SCED_audit']if phase else program.checks(*z,names,False),native_calls=int(not have_sced),historical_native_calls=1+int(have_sced),historical_native_source=str(old/'actual/SCUC'),execution_attempt_id=x.execution_attempt_id,formal=False,safety_release=False)
 for k in ('SCED_response_kind','dual_certificate','dual_certificate_sha256','native_dual_passed'):result[k]=proof[k]
 if phase:result['post_SCUC_phase']=phase.candidate['proof']
 write(a/'ACCEPTED_PAIR.json',result);h.before_handoff_gate._generated_after_verified_pair(p,a,result)
 h._finish(p,a,result)
 return int(not have_sced)
