"""Optional new-pair runner using the registered producer's workers and strict stage audit.
No retries: any existing actual directory requires inspection, never resubmission.
"""
from pathlib import Path
from copy import deepcopy
from v6_host import read,write,sha,e

def execute_pair(hooks,prepared,*,execute_native=False):
 e.need(execute_native is True,'explicit native execution required')
 s=hooks.session;s.verify_sources();p=Path(prepared).resolve();record=hooks.prepared[str(p)]
 for f,h in record['artifact_pins'].items():e.need(sha(f)==h,'registered prepared bytes changed')
 e.need(sha(record['carry_path'])==record['carry_sha256'],'registered boundary drift')
 a=p.parent/'actual';e.need(not a.exists(),'existing actual occurrence: inspect; never retry')
 q=read(p/'template.json');n=read(p/'names.json');program=s.program
 a.mkdir();write(a/'HOST_INPUT.json',dict(prepared=str(p),artifact_pins=record['artifact_pins'],ledger_id=record['ledger_id'],mode='fixed_curve_research'))
 program.engine.worker.native(a,'SCUC',q,program.engine.worker.V/'scuc_worker.py',3600.)
 u=program.stage(p.parent,'SCUC',q,n,True)
 controller=getattr(hooks,'post_scuc',None);phase=controller.after_scuc(p)if controller is not None else None
 lp=deepcopy(phase.candidate['model'])if phase is not None else program.fixed_sced(q,u[1]['primal']);n=phase.candidate['names']if phase is not None else n;lp['options'].update(presolve='off',dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0,solver='simplex',run_crossover='off',ipm_optimality_tolerance=1e-12)
 program.engine.worker.native(a,'SCED',lp,program.B/'lp_ipm_worker.py',3600.)
 if phase is None:z=program.stage(p.parent,'SCED',lp,n,False)
 else:
  registered=controller.accepted_phase(phase);z=(registered['SCED_request'],registered['SCED_response'])
 # Full KKT + fixed-integer/primal checks, not a trust flag.
 proof=e.verify_pair(u[0],u[1],z[0],z[1],n)
 result=dict(schema='gd.event.host.accepted_pair.v2',status='accepted_research_pair',run_id=s.registry['run_id'],prepared_sha256=sha(p/'SUMMARY.json'),ledger_id=record['ledger_id'],SCUC_response_sha256=sha(a/'SCUC/response.json.gz'),SCED_response_sha256=sha(a/'SCED/response.json.gz'),SCUC_gap=u[1]['native_mip_gap'],SCUC_audit=program.checks(*u,read(p/'names.json'),True),SCED_audit=registered['SCED_audit']if phase is not None else program.checks(*z,n,False),native_calls=2,formal=False,safety_release=False)
 result.update(SCED_response_kind=proof['SCED_response_kind'],dual_certificate=proof['dual_certificate'],dual_certificate_sha256=proof['dual_certificate_sha256'],native_dual_passed=proof['native_dual_passed'])
 if phase is not None:result['post_SCUC_phase']=phase.candidate['proof']
 write(a/'ACCEPTED_PAIR.json',result);return result
