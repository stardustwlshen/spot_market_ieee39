"""At most one bounded Q/V diagnostic per new cell; no capacity scans."""
import os
os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['OMP_NUM_THREADS']='1';os.environ['MPLCONFIGDIR']='/tmp/gd_hour_capability_mpl'
from pathlib import Path
import numpy as np
import json,copy,time,signal
from scipy.optimize import least_squares
from scipy.sparse import csr_matrix,eye,hstack
from pandapower.pypower.opf_setup import opf_setup
from pandapower.pypower.opf_consfcn import opf_consfcn
from pandapower.pypower.makeYbus import makeYbus
from pandapower.pypower.ppoption import ppoption
import sys,hashlib,math,gzip
B=Path(__file__).resolve().parent;R=B.parent;INIT=R/'guangdong_short_qv_zeroequiv_v2_20261112';C=R/'guangdong_short_loss_ac_loop_v1_20261112/round_1'
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2,allow_nan=False)

def solve_cell(t,m,previous,values,design):
 out=B/'diagnostic_once';out.mkdir(exist_ok=False);domain=read(B/'LOCAL_P_DOMAIN.json');case=m['ac_input'];base=case['base_mva'];nb=len(case['buses']);nt=len(case['generators']);assert nt==5
 bs={b['id']:b for b in previous['buses']};gs={g['id']:g for g in previous['generators']};idx={b['id']:j for j,b in enumerate(case['buses'])}
 bus=np.array([[j,{'PQ':1,'PV':2,'SLACK':3}[b['type']],b['pd_mw'],b['qd_mvar'],b['gs_mw'],b['bs_mvar'],1,bs[b['id']]['vm_pu'],bs[b['id']]['va_degree'],b['base_kv'],1,b['vmax_pu'],b['vmin_pu']]+[0.]*4 for j,b in enumerate(case['buses'])],float)
 thermal=[];limits=[]
 for g in case['generators']:
  d=m['thermal_capacity'][g['id']];pr=values[f"power[{g['id']},{t-1}]"];nx=values[f"power[{g['id']},{t+1}]"];up=d['ramp_up_mw_h']/12;dn=d['ramp_down_mw_h']/12;lo=max(g['pmin_mw']+10,pr-dn,nx-up);hi=min(g['pmax_mw']-10,pr+up,nx+dn);assert lo<=hi
  limits.append(dict(id=g['id'],lower_P_MW=lo,upper_P_MW=hi,previous_SCED_P_MW=pr,next_SCED_P_MW=nx))
  assert abs(lo-domain['thermal'][g['id']]['P_min_MW'])<1e-8 and abs(hi-domain['thermal'][g['id']]['P_max_MW'])<1e-8
  thermal.append([idx[g['bus']],g['pg_mw'],gs[g['id']]['q_mvar'],g['qmax_mvar'],g['qmin_mvar'],g['vg_pu'],base,1,hi,lo]+[0.]*16)
 active=[];disabled=[]
 for d0 in design['resources']:
  d=copy.deepcopy(d0);key=f"power[{d['id']},{t}]"if d['technology']=='battery'else f"vre_power[{d['external_bus']},{t}]";d['fixed_P_MW']=values[key];d['enabled_Q']=abs(d['fixed_P_MW'])>1e-8;d['Q_min_Mvar']=-d['Qcap_Mvar']if d['enabled_Q']else 0.;d['Q_max_Mvar']=d['Qcap_Mvar']if d['enabled_Q']else 0.
  (active if d['enabled_Q']else disabled).append(d)
 extra=[]
 for d in active:
  local=domain['battery'].get(d['id']);pmin=local['P_min_MW']-d['fixed_P_MW']if local else 0.;pmax=local['P_max_MW']-d['fixed_P_MW']if local else 0.
  extra.append([d['internal_bus'],0.,0.,d['Qcap_Mvar'],-d['Qcap_Mvar'],1.,base,1,pmax,pmin]+[0.]*16)
 gen=np.array(thermal+extra);ng=len(gen);branch=np.array([[idx[l['from_bus']],idx[l['to_bus']],l['r_pu'],l['x_pu'],l['b_pu'],l['rate_mva'],l['rate_mva'],l['rate_mva'],l['tap'],l['shift_degree'],l['status'],-360,360]+[0.]*13 for l in case['branches']],float);ppc=dict(version='2',baseMVA=base,bus=bus,gen=gen,branch=branch,gencost=np.array([[2,0,0,2,0,0]for _ in range(ng)],float));opt=ppoption(OPF_ALG=560,VERBOSE=0,OUT_ALL=0,INIT='results',OPF_FLOW_LIM=0);om=opf_setup(ppc,opt)
 cap=np.array([m['thermal_capacity'][g['id']]['raw_pmax_mw']-10 for g in case['generators']])/base;sel=csr_matrix((np.ones(nt),(np.arange(nt),np.arange(nt))),shape=(nt,ng))
 for service in ['positive','accident']:
  caps=np.array([m['thermal_capacity'][g['id']][service+'_cap_mw']for g in case['generators']])/base;key='research_'+service;om.add_vars(key,nt,np.minimum(caps,np.maximum(0,cap-gen[:nt,1]/base)),np.zeros(nt),caps);om.add_constraints(key+'_headroom',hstack([sel,eye(nt)],format='csr'),np.full(nt,-np.inf),cap,['Pg',key]);om.add_constraints(key+'_total',csr_matrix(np.ones((1,nt))),np.array([4.]),np.array([np.inf]),[key])
 om.add_constraints('thermal_total_cap',csr_matrix(([1.]*nt,([0]*nt,list(range(nt)))),shape=(1,ng)),np.array([-np.inf]),np.array([float(sum(m['thermal_capacity'][g['id']]['raw_pmax_mw']-m['thermal_capacity'][g['id']]['agc_up_reserved_mw'] for g in case['generators'])/base-4.)]),['Pg']);A,l,u=om.linear_constraints();A=A.toarray();x,lo,hi=om.getv();vv,_,_,_=om.get_idx();Vm=vv['i1']['Vm'];Pg=vv['i1']['Pg'];Qg=vv['i1']['Qg'];x[:nb]=[np.deg2rad(bs[b['id']]['va_degree'])for b in case['buses']];x[Vm:Vm+nb]=[bs[b['id']]['vm_pu']for b in case['buses']];x[Qg:Qg+nt]=[gs[g['id']]['q_mvar']/base for g in case['generators']]
 # Initialization only: exact current-point solved AC, including converter Q and slack actual P.
 x[Pg:Pg+nt]=[gs[g['id']]['p_mw']/base for g in case['generators']]
 initialQ={d['id']:0. for d in design['resources']} # same-current-point Q0 policy, not future controls
 for i,d in enumerate(active):x[Qg+nt+i]=initialQ[d['id']]/base
 Y,Yf,Yt=makeYbus(base,bus,branch);il=np.where((branch[:,5]>0)&(branch[:,5]<1e10))[0]
 def ac(z):return opf_consfcn(z,om,Y,Yf[il,:],Yt[il,:],opt,il)
 def inverter(z):
  vals=[];rows=[]
  for i,d in enumerate(active):
   qi=Qg+nt+i;vi=Vm+d['internal_bus'];q=z[qi];pi=Pg+nt+i;p=d['fixed_P_MW']/base+z[pi];sn=d['S_continuous_MVA']/base
   for cur in [False,True]:
    vals.append((p*p+q*q)/(sn*sn)-(z[vi]**2 if cur else 1));r=np.zeros(len(z));r[qi]=2*q/(sn*sn);r[pi]=2*p/(sn*sn)
    if cur:r[vi]=-2*z[vi]
    rows.append(r)
  return np.array(vals),np.array(rows)
 physical_lo=lo.copy();physical_hi=hi.copy();vlow=lo[Vm:Vm+nb].copy();vhigh=hi[Vm:Vm+nb].copy();lo[Vm:Vm+nb]=0.8;hi[Vm:Vm+nb]=1.2;voltage_selector=np.zeros((nb,len(x)));voltage_selector[:,Vm:Vm+nb]=np.eye(nb)
 fl=np.isfinite(l);fu=np.isfinite(u);Al=A[fl];Au=A[fu];ll=l[fl];uu=u[fu];free=hi>lo;fixed=x.copy();fixed[~free]=lo[~free]
 def expand(y):z=fixed.copy();z[free]=y;return z
 def residual(y):
  z=expand(y);h,g,_,_=ac(z);ih,_=inverter(z);return np.r_[g,np.maximum(h,0),np.maximum(ih,0),np.maximum(ll-Al@z,0),np.maximum(Au@z-uu,0),np.maximum(vlow-z[Vm:Vm+nb],0),np.maximum(z[Vm:Vm+nb]-vhigh,0)]
 def jac(y):
  z=expand(y);h,g,dh,dg=ac(z);ih,ij=inverter(z);return np.vstack([dg.toarray().T,dh.toarray().T*(h>0)[:,None],ij*(ih>0)[:,None],-Al*((ll-Al@z)>0)[:,None],Au*((Au@z-uu)>0)[:,None],-voltage_selector*((vlow-z[Vm:Vm+nb])>0)[:,None],voltage_selector*((z[Vm:Vm+nb]-vhigh)>0)[:,None]])[:,free]
 h,g,dh,dg=ac(x);ih,ij=inverter(x);J=np.vstack([dg.toarray().T,dh.toarray().T,ij]);errors=[]
 for j in range(len(x)):
  a=x.copy();b=x.copy();a[j]+=1e-6;b[j]-=1e-6;ha,ga,_,_=ac(a);hb,gb,_,_=ac(b);ia,_=inverter(a);ib,_=inverter(b);num=np.r_[ga-gb,ha-hb,ia-ib]/2e-6;errors.append(float(max(abs(num-J[:,j]))/max(1,max(abs(J[:,j])))))
 assert max(errors)<2e-6;write(out/'JACOBIAN_CHECK.json',dict(columns=len(x),max_relative_error=max(errors),passed=True));write(out/'MODEL_PLAN.json',dict(cell=t,active_resources=active,disabled_resources=disabled,thermal_limits=limits,SCED_response_sha256=sha(C/'SCED/response.json.gz'),capability_contract_sha256=sha(B/'CAPABILITY_CONTRACT.json'),source_mapping_sha256=sha(C/'MAPPING.json'),producer_sha256=sha(__file__),same_Pn_and_Qcap=True,original_400_and_Vlimits_unchanged=True,max_nfev=500,max_seconds=180))
 y=np.clip(x[free],lo[free]+1e-12,hi[free]-1e-12)
 projected=expand(y);h0,g0,_,_=ac(x);hp0,gp0,_,_=ac(projected);i0,_=inverter(x)
 def finite(z):return float(z) if np.isfinite(z) else str(z)
 labels=[]
 for key in ['Va','Vm','Pg','Qg','research_positive','research_accident']:
  for j in range(vv['i1'][key],vv['iN'][key]):labels.append((j,key+':'+str(j-vv['i1'][key])))
 boundary=[dict(index=j,label=label,value=float(x[j]),lower=finite(lo[j]),upper=finite(hi[j]),violation=float(max(0,lo[j]-x[j],x[j]-hi[j])))for j,label in labels]
 initial=dict(current_point_real_AC=True,not_old_dispatch_or_failed_iterate=True,source_AC_sha256=sha(INIT/'LEGAL_INITIAL_AC.json'),source_controls_sha256=sha(INIT/'LEGAL_INITIAL_POLICY.json'),search_voltage_bounds=[0.8,1.2],physical_voltage_bounds_unchanged=True,voltage_violation_terms_enforced_as_zero_residual=True,unprojected_AC_equality_max_pu=float(max(abs(g0))),projected_AC_equality_max_pu=float(max(abs(gp0))),unprojected_inverter_violation=float(max(np.maximum(i0,0))),boundaries=boundary,initial_vector=x.tolist(),projected_vector=projected.tolist(),no_capability_or_availability_change=True)
 assert initial['unprojected_AC_equality_max_pu']<1e-9
 write(out/'INITIAL_STATE_REVIEW.json',initial);print('INITIAL',initial['unprojected_AC_equality_max_pu'],initial['projected_AC_equality_max_pu'],[z for z in boundary if z['violation']>1e-8],flush=True)
 write(out/'STARTED.json',dict(optimizer_calls_allowed=1,model_sha256=sha(out/'MODEL_PLAN.json')))
 def timeout(a,b):raise TimeoutError('180 second point diagnostic budget')
 signal.signal(signal.SIGALRM,timeout);signal.alarm(180);start=time.perf_counter()
 try:res=least_squares(residual,y,jac=jac,bounds=(lo[free],hi[free]),method='trf',max_nfev=500,ftol=1e-13,xtol=1e-13,gtol=1e-13,x_scale='jac');signal.alarm(0)
 except Exception as e:signal.alarm(0);write(out/'FAILURE.json',dict(type=type(e).__name__,message=str(e),elapsed=time.perf_counter()-start,no_retry=True));return dict(cell=t,status='diagnostic_exception',accepted=False)
 z=expand(res.x);h,g,_,_=ac(z);ih,_=inverter(z);review=dict(cell=t,max_AC_equation_pu=float(max(abs(g))),max_line_squared_pu=float(max(np.maximum(h,0))),max_inverter_normalized=float(max(np.maximum(ih,0))),max_bound_pu=float(max(np.max(np.maximum(physical_lo-z,0)),np.max(np.maximum(z-physical_hi,0)))),max_reserve_pu=float(max(np.max(np.maximum(ll-Al@z,0)),np.max(np.maximum(Au@z-uu,0)))),nfev=res.nfev,elapsed_seconds=time.perf_counter()-start,solver_success=bool(res.success));okay=max(v for k,v in review.items()if k.startswith('max_'))<=1e-9;review['zero_P_Q_policy_passed']=all(abs(d['fixed_P_MW']+z[Pg+nt+i]*base)>1e-8 or abs(z[Qg+nt+i]*base)<=1e-6 for i,d in enumerate(active));okay=okay and review['zero_P_Q_policy_passed'];review['strict_feasible_candidate']=bool(okay);write(out/'RESULT.json',dict(x=z.tolist(),variable_indices=vv,physical_lower=[finite(v) for v in physical_lo],physical_upper=[finite(v) for v in physical_hi],search_lower=[finite(v) for v in lo],search_upper=[finite(v) for v in hi],review=review));write(out/'COMPLETED.json',dict(optimizer_calls=1,result_sha256=sha(out/'RESULT.json')))
 if not okay:return dict(cell=t,status='no_feasible_candidate',accepted=False,review=review)
 new=copy.deepcopy(m);bybus={b['id']:b for b in new['ac_input']['buses']};control=[]
 for i,d in enumerate(new['ac_input']['generators']):d['vg_pu']=float(z[Vm+int(d['bus'])]);d['qg_mvar']=float(z[Qg+i]*base);control.append(dict(id=d['id'],kind='thermal',new_VG_pu=d['vg_pu'],diagnostic_actual_P_MW=float(z[Pg+i]*base),SCED_plan_P_MW=d['pg_mw']));d['pg_mw']=float(z[Pg+i]*base)
 for i,d in enumerate(active):
  q=float(z[Qg+nt+i]*base);deltaP=float(z[Pg+nt+i]*base);bybus[str(d['internal_bus'])]['pd_mw']-=deltaP;bybus[str(d['internal_bus'])]['qd_mvar']-=q;control.append(dict(id=d['id'],kind='inverter',internal_bus=d['internal_bus'],P_MW=d['fixed_P_MW']+deltaP,original_P_MW=d['fixed_P_MW'],delta_P_MW=deltaP,Q_Mvar=q,S_MVA=d['S_continuous_MVA'],Qcap_Mvar=d['Qcap_Mvar']))
 new['ac_input']['provenance']='Independent new midnight first-point capability design; legal local P redispatch candidate before full48 fixed-integer SCED reinsertion, bounded same-point Q/VG; no formal release';write(out/'CONTROLLED_MAPPING.json',new);write(out/'CONTROLS.json',control);return dict(cell=t,status='candidate_ready_for_independent_AC',accepted=False,feasible_candidate=True,review=review)

def main():
 p=read(C/'PAYLOAD.json');m=read(INIT/'LEGAL_INITIAL_MAPPING.json');m['slack_resource']=p['slack_resource'];previous=read(INIT/'LEGAL_INITIAL_AC.json');assert previous['ac_converged']
 names=read(R/'guangdong_rt_short_physical_v1_20261112/pure39_v2/names.json');raw=json.loads(gzip.decompress((C/'SCED/response.json.gz').read_bytes()));v={k.replace("'",''):x for k,x in zip(names['variables'],raw['primal'])};values={}
 for r in p['resources']:
  if r['kind']=='thermal':
   u=r['id'];values[f'power[{u},-1]']=r['previous_endpoint_mw'];values[f'power[{u},1]']=v[f'power[{u},1]']
  if r['kind']=='battery':values[f"power[{r['id']},0]"]=p['plan'][r['id']]['p_mean_mw']
  elif r['kind']!='thermal':values[f"vre_power[{int(r['bus'])+1},0]"]=p['plan'][r['id']]['p_mean_mw']
 inventory=read(R/'guangdong_agc_ac_controls_v2_20261112/INVERTER_INVENTORY.json')['units'];design={'resources':[],'source':'same original static Pn and existing research pf design; no old optimized controls','no_existing_device_certification':True,'original_scenario_modified':False,'zero_P_Q_enabled':False}
 for u in inventory:
  pf=.9 if u['kind']=='battery'else .95;pn=u['nameplate_P_MW'];sn=pn/pf;cap=pn*math.sqrt(1/pf**2-1)
  design['resources'].append({'id':u['id'],'technology':u['kind'],'internal_bus':u['internal_bus'],'external_bus':u['external_bus'],'P_nameplate_MW':pn,'S_continuous_MVA':sn,'Qcap_Mvar':cap,'research_design_pf':pf,'assumption':'new synthetic direct-connection aggregate, lossless converter/collection; standard-motivated not specified manufacturer capability'})
 write(B/'CAPABILITY_CONTRACT.json',design)
 result=solve_cell(0,m,previous,values,design);write(B/'DIAGNOSTIC_SUMMARY.json',result);print(json.dumps(result),flush=True)
 if result.get('feasible_candidate'):
  write(B/'AWAIT_FULL_SCED.json',{'strict_local_candidate':True,'not_executed':True,'requires_full48_fixed_integer_LP_then_independent_AC':True,'no_safety_ticket':True})
if __name__=='__main__':main()
