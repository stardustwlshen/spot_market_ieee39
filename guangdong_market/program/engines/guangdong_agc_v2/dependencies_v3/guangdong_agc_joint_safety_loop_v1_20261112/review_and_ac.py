"""Review main-thread new native files; optional one explicitly requested AC check."""
from loop import *
from proof import expected_model,verify,verify_native_receipt
import argparse

def write(p,z):
 with p.open('x')as f:json.dump(z,f,indent=2,allow_nan=False)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--native',required=True);ap.add_argument('--candidate',required=True);ap.add_argument('--output',required=True);ap.add_argument('--execute-ac',action='store_true');a=ap.parse_args();C=Path(a.candidate);N=Path(a.native);O=Path(a.output);need(not O.exists(),'fresh review output required');ready=read(C/'READY.json')
 for r in ready['artifacts'].values():resolve(r)
 physical=read(C/'PHYSICAL_REQUEST.json');native_receipt={k:file_ref(N/v)for k,v in [('identity','IDENTITY.json'),('launch','LAUNCH.json'),('completed','COMPLETED.json')]};verify_native_receipt(dict(native_receipt=native_receipt,request=file_ref(N/'request.json'),response=file_ref(N/'response.json')),physical);expected,n=expected_model(physical);q=read(N/'request.json');r=read(N/'response.json');need(q.get('agc_joint_binding')==physical['binding'],'new candidate request binding')
 for k in ('variables','rows','objective','options'):need(q[k]==expected[k],'new model scope '+k)
 need(r.get('backend')=='highs'and r.get('solver_version')=='1.15.1'and r.get('native_status')=='Optimal'and r.get('dual_valid')is True,'actual Optimal LP required')
 sys.path.insert(0,str(ROOT/'guangdong_pipeline_20261112/da_math'));from scoped_kkt import metrics
 kk=metrics(q,r,n,angle_tolerance=1e-8);need(kk['passed'],'strict new SCED KKT');O.mkdir();write(O/'SCED_KKT.json',kk)
 vi={name.replace("'",''):j for j,name in enumerate(n['variables'])};local=read(resolve(physical['parent_refs']['parent_local_input']));t=ready['AC_selected_cell'];offset=expected['r67_global_offset'];j=t-offset;ref=local['da_cells'][j];cell=dict(start=ref['start'],end=ref['end'],thermal={},batteries={},renewable={})
 for uid,u in local['resources'].items():
  p=r['primal'][vi[f'power[{uid},{t}]']]
  if u['kind']=='battery':cell['batteries'][uid]=dict(charge_mw=max(0.,-p),discharge_mw=max(0.,p))
  else:
   m=next(z for z in physical['margin_rows']if z['unit']==uid and z['global_cell']==t);need(m['agc_up_mw']==m['agc_down_mw']==10.,'v1 retained AC adapter supports original10 margins only');cell['thermal'][uid]=dict(phase=u['rows'][j]['phase'],mean_mw=p,agc_up_reserved_mw=10.,agc_down_reserved_mw=10.)
 for node,u in local['renewable'].items():cell['renewable'][node]=dict(availability_mw=u['availability_mw'][j],dispatched_mw=r['primal'][vi[f'vre_power[{node},{t}]']],curtailed_mw=r['primal'][vi[f'vre_curtailment[{node},{t}]']],reserve_credit_mw=0.,AGC_credit_mw=0.)
 bridge=module('joint_ac_bridge',ROOT/'guangdong_fresh_run_ac_bridge_20261112/bridge.py');benchmark=read(ROOT/'guangdong_r30_ac_security_20261112/IEEE39_RESEARCH_INPUT.json');prepared=bridge.build(benchmark,local,cell,j);prepared.update(candidate_binding=physical['binding'],global_cell_index=t,not_executed=True)
 write(O/'AC_MAPPING.json',prepared);write(O/'AC_INPUT.json',prepared['ac_input'])
 if not a.execute_ac:print('strict SCED reviewed; pure AC input ready; 0 AC calls');return
 acmod=module('joint_original_ac',ROOT/'guangdong_r30_ac_security_20261112/ac_security.py');ac=acmod.check_ac(prepared['ac_input']);cap=bridge.postcheck(prepared,ac);write(O/'AC_RESULT.json',dict(AC=ac,capacity_review=cap,AC_calls=1,not_executed=True))
 write(O/'AC_RECEIPT.json',dict(status='completed',binding=physical['binding'],global_cell=t,input_sha256=sha(O/'AC_INPUT.json'),result_sha256=sha(O/'AC_RESULT.json'),AC_calls=1,AC_worker_sha256=sha(ROOT/'guangdong_r30_ac_security_20261112/ac_security.py'),producer_sha256=sha(__file__)))
 evidence=dict(native_receipt=native_receipt,schema='gd.agc.joint.numeric_evidence.v1',binding=physical['binding'],physical_request_sha256=digest(physical),request=file_ref(N/'request.json'),response=file_ref(N/'response.json'),names=file_ref(C/'names.json'),ac_cells=[dict(global_cell=t,input=file_ref(O/'AC_INPUT.json'),result=file_ref(O/'AC_RESULT.json'),receipt=file_ref(O/'AC_RECEIPT.json'))]);write(O/'NUMERIC_EVIDENCE.json',evidence)
 c=read(C/'CONTEXT.json');verify_context(c);need(binding(c,0,None)==physical['binding'],'candidate context matches actual request');l=Loop(c);l.clear(read(C/'thermal_input.json'));z=l.apply_verified_failure(evidence);write(O/'SAFETY_TRANSITION.json',z)
 if l.status=='reclear_required':l.clear(read(C/'thermal_input.json'))
 write(O/'LOOP_RESULT.json',l.snapshot());print(l.status)
if __name__=='__main__':main()
