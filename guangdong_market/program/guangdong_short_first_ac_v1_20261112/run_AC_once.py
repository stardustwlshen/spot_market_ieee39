from pathlib import Path
import sys,json,hashlib
B=Path(__file__).resolve().parent;R=B.parent
sys.path.insert(0,str(R/'guangdong_r30_ac_security_20261112'))
from ac_security import check_ac
sys.path.insert(0,str(R/'guangdong_fresh_run_ac_bridge_20261112'))
from bridge import postcheck
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(name,x):
 with(B/name).open('x')as f:json.dump(x,f,indent=2,allow_nan=False)
a=read(B/'ACCEPTANCE_BINDING.json')
for s in a['sources']:assert sha(R/s['path'])==s['sha256']
m=read(B/'MAPPING.json');write('AC_STARTED.json',{'calls_authorized':1,'mapping_sha256':sha(B/'MAPPING.json'),'producer_sha256':sha(__file__),'AC_engine_sha256':sha(R/'guangdong_r30_ac_security_20261112/ac_security.py')})
z=check_ac(m['ac_input'],max_iterations=60,tolerance_mva=1e-10);write('AC_RESULT.json',z)
c=postcheck(m,z);write('CAPACITY_POSTCHECK.json',c)
summary={'AC_status':z['status'],'AC_converged':z['ac_converged'],'violations':z.get('violations',[]),'capacity_postcheck':c,'new_AC_calls':1,'new_optimization_calls':0,'formal_release':False,'security_release':False,'execution_ledger_allowed':False,'source_AC_result_sha256':sha(B/'AC_RESULT.json')}
if z['ac_converged']:
 summary.update(Vmin=min(b['vm_pu']for b in z['buses']),Vmax=max(b['vm_pu']for b in z['buses']),actual_loss_MW=sum(b['from_p_mw']+b['to_p_mw']for b in z['branches']),max_plan_actual_P_difference_MW=max(abs(g['p_change_mw'])for g in z['generators']))
write('SUMMARY.json',summary);print(json.dumps(summary,indent=2))
