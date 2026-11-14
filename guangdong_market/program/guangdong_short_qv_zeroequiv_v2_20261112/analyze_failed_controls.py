from pathlib import Path
import sys,json,numpy as np
from pandapower.pypower.makeYbus import makeYbus
B=Path(__file__).resolve().parent
read=lambda p:json.loads(Path(p).read_text())
m=read(B/'LEGAL_INITIAL_MAPPING.json');a=m['ac_input'];r=read(B/'diagnostic_once/RESULT.json');z=np.array(r['x']);v=r['variable_indices'];n=len(a['buses']);base=a['base_mva'];idx={b['id']:i for i,b in enumerate(a['buses'])}
bus=np.array([[j,{'PQ':1,'PV':2,'SLACK':3}[b['type']],b['pd_mw'],b['qd_mvar'],b['gs_mw'],b['bs_mvar'],1,b['vm_pu'],b['va_degree'],b['base_kv'],1,b['vmax_pu'],b['vmin_pu']]for j,b in enumerate(a['buses'])],float)
branch=np.array([[idx[l['from_bus']],idx[l['to_bus']],l['r_pu'],l['x_pu'],l['b_pu'],l['rate_mva'],l['rate_mva'],l['rate_mva'],l['tap'],l['shift_degree'],l['status'],-360,360]for l in a['branches']],float)
branch=np.pad(branch,((0,0),(0,13)));Y,_,_=makeYbus(base,bus,branch);V=z[v['i1']['Vm']:v['iN']['Vm']]*np.exp(1j*z[v['i1']['Va']:v['iN']['Va']]);S=-(bus[:,2]+1j*bus[:,3])/base
Pg=z[v['i1']['Pg']:v['iN']['Pg']]*base;Qg=z[v['i1']['Qg']:v['iN']['Qg']]*base
for j,g in enumerate(a['generators']):S[idx[g['bus']]]+=(Pg[j]+1j*Qg[j])/base
plan=read(B/'diagnostic_once/MODEL_PLAN.json');control=[]
for j,d in enumerate(plan['active_resources']):
 Q=Qg[len(a['generators'])+j];S[d['internal_bus']]+=1j*Q/base;control.append({'id':d['id'],'P_fixed_MW':d['fixed_P_MW'],'failed_iterate_Q_Mvar':Q,'Qcap_Mvar':d['Qcap_Mvar'],'S_MVA':d['S_continuous_MVA'],'Vpu':float(abs(V[d['internal_bus']]))})
mis=(V*np.conj(Y@V)-S)*base;rows=sorted([{'AC_bus':b['id'],'external_bus':int(b['id'])+1,'P_residual_MW':float(mis[j].real),'Q_residual_Mvar':float(mis[j].imag),'Vpu':float(abs(V[j]))}for j,b in enumerate(a['buses'])],key=lambda x:max(abs(x['P_residual_MW']),abs(x['Q_residual_Mvar'])),reverse=True)
thermal=[{'id':g['id'],'failed_iterate_P_MW':Pg[j],'failed_iterate_Q_Mvar':Qg[j],'Qmin':g['qmin_mvar'],'Qmax':g['qmax_mvar']}for j,g in enumerate(a['generators'])]
out={'pure_reconstruction_no_solver_calls':True,'not_physical_infeasibility_proof':True,'unaccepted_iterate_never_applied':True,'largest_residuals':rows[:10],'thermal':thermal,'active_inverters':control,'disabled_inverters':plan['disabled_resources'],'stopping_reason':'one authorized zero-equivalent voltage-residual diagnostic terminated at142 evaluations; final original voltage constraints failed. No repeat or capacity sweep.'}
(B/'FAILED_CONTROL_LOCATION.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
