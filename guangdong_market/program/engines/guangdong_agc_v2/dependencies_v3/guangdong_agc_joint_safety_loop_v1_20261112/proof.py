"""Fail-closed numeric proof adapter; never consumes pass flags as evidence."""
from copy import deepcopy
import math,sys,json
from loop import ROOT,need,read,resolve,digest,sha,module

def expected_model(request):
 refs=request['parent_refs'];old=read(resolve(refs['parent_template']));names=read(resolve(refs['parent_names']));q=deepcopy(old);vi={name.replace("'",''):j for j,name in enumerate(names['variables'])};margins={}
 for m in request['margin_rows']:
  name=f"power[{m['unit']},{m['global_cell']}]";j=vi[name];v=q['variables'][j]
  need(v['lb']!=v['ub'],'cannot change fixed issued/executed/physical column');need(abs(v['lb']-(m['raw_pmin_mw']+10))<1e-6 and abs(v['ub']-(m['raw_pmax_mw']-10))<1e-6,'original synthetic10 margins not recognized')
  margins[j]=m;v['lb']=m['dispatch_min_mw'];v['ub']=m['dispatch_max_mw']
 for row in q['rows']:
  if len(row['terms'])!=2:continue
  pp=[(j,c)for j,c in row['terms']if j in margins];rr=[(j,c)for j,c in row['terms']if names['variables'][j].startswith('reserve[')]
  if len(pp)!=1 or len(rr)!=1:continue
  j,c=pp[0];m=margins[j];need(rr[0][1]==1 and c in(-1,1)and row['lb']is None,'unrecognized reserve headroom row')
  oldvalue=m['raw_pmax_mw']-10 if c==1 else -m['raw_pmin_mw']-10;newvalue=m['dispatch_max_mw']if c==1 else -m['dispatch_min_mw'];need(abs(row['ub']-oldvalue)<1e-6,'headroom source mismatch');row['ub']=newvalue
 return q,names

def finite_tree(value):
 if isinstance(value,float):need(math.isfinite(value),'nonfinite AC numeric evidence')
 elif isinstance(value,dict):
  for x in value.values():finite_tree(x)
 elif isinstance(value,list):
  for x in value:finite_tree(x)
def exact_inventory(rows,ids,label):
 need(isinstance(rows,list)and len(rows)==len(ids)and {x['id']for x in rows}==set(ids),'exact AC '+label+' inventory')
def verify_native_receipt(proof,request):
 for p,h in request['producer_pins'].items():need(sha(p)==h,'frozen proof producer changed')
 need(isinstance(proof.get('native_receipt'),dict),'new actual native receipt required; old requests cannot be promoted');refs=proof['native_receipt'];ident=read(resolve(refs['identity']));launch=read(resolve(refs['launch']));done=read(resolve(refs['completed']));qh=sha(resolve(proof['request']));rh=sha(resolve(proof['response']));wh=request['native_worker']['sha256'];lh=request['producer_pins'][str(ROOT/'guangdong_agc_joint_safety_loop_v1_20261112/launch_candidate.py')]
 need(ident['binding']==request['binding']and ident['physical_request_sha256']==digest(request),'native candidate binding')
 need(ident['request_sha256']==launch['request_sha256']==done['request_sha256']==qh and done['response_sha256']==rh,'native raw request/response receipt binding')
 need(ident['worker_sha256']==launch['worker_sha256']==done['worker_sha256']==wh and ident['launcher_sha256']==launch['launcher_sha256']==lh,'native frozen worker/launcher')
 need(type(launch['pid'])is int and launch['pid']>0 and done['pid']==launch['pid']and done['returncode']==0,'known completed native occurrence')
def verify(proof,request):
 need(proof.get('schema')=='gd.agc.joint.numeric_evidence.v1','numeric evidence schema, not caller Boolean');need(proof['binding']==request['binding']and proof['physical_request_sha256']==digest(request),'candidate iteration/source/hour binding')
 verify_native_receipt(proof,request)
 expected,n=expected_model(request);q=read(resolve(proof['request']));r=read(resolve(proof['response']));names=read(resolve(proof['names']));need(names==n,'same columns and rows')
 for key in ('variables','rows','objective'):need(q[key]==expected[key],'physical model differs beyond explicitly recalculated AGC occupancy '+key)
 need(q['options']==expected['options'],'same numerical profile')
 need(q.get('agc_joint_binding')==request['binding'],'new request must bind candidate; old requests cannot be promoted')
 need(r.get('native_status')=='Optimal'and r.get('dual_valid')is True and r.get('backend')=='highs','new actual Optimal primal/dual required')
 sys.path.insert(0,str(ROOT/'guangdong_pipeline_20261112/da_math'));from scoped_kkt import metrics
 k=metrics(q,r,n,angle_tolerance=1e-8);need(k['passed'],'new full-row strict KKT')
 vi={name.replace("'",''):j for j,name in enumerate(n['variables'])};marg={(z['unit'],z['global_cell']):z for z in request['margin_rows']};local=read(resolve(request['parent_refs']['parent_local_input']));offset=expected['r67_global_offset'];failures={};global_unsafe=[];checked=[]
 benchmark=read(ROOT/'guangdong_r30_ac_security_20261112/IEEE39_RESEARCH_INPUT.json');bb={b['id']:b for b in benchmark['buses']};bg={g['bus']:g for g in benchmark['generators']};mapping={'C1':'31','C2':'32','C3':'38','CCGT':'35','GT':'36'}
 for item in proof.get('ac_cells',[]):
  t=item['global_cell'];need(type(t)is int and t not in checked and all((g,t)in marg for g in mapping),'hour AC cells');acinput=read(resolve(item['input']));out=read(resolve(item['result']));out=out.get('AC',out);finite_tree(acinput);finite_tree(out);need(out['input_sha256']==digest(acinput),'AC raw bound to input')
  receipt=read(resolve(item['receipt']));need(receipt['binding']==request['binding']and receipt['global_cell']==t and receipt['input_sha256']==sha(resolve(item['input']))and receipt['result_sha256']==sha(resolve(item['result'])),'AC actual occurrence binding')
  need(receipt['status']=='completed'and receipt['AC_calls']==1 and receipt['AC_worker_sha256']==request['producer_pins'][str(ROOT/'guangdong_r30_ac_security_20261112/ac_security.py')]and receipt['producer_sha256']==request['producer_pins'][str(ROOT/'guangdong_agc_joint_safety_loop_v1_20261112/review_and_ac.py')],'frozen actual AC worker/producer')
  exact_inventory(acinput['buses'],bb,'input buses');exact_inventory(acinput['generators'],mapping,'input generators');exact_inventory(out['buses'],bb,'result buses');exact_inventory(out['generators'],mapping,'result generators');exact_inventory(out['branches'],{b['id']for b in benchmark['branches']},'result branches')
  for g in out['generators']:
   need(g['bus']==str(int(mapping[g['id']])-1)and g.get('slack')is(g['id']=='C1'),'AC output generator/node/slack identity')
   need(all(type(g[k])in(int,float)and math.isfinite(g[k])for k in ('p_mw','q_mvar','scheduled_p_mw','p_change_mw')),'finite generator measurements')
  for b in out['buses']:need(all(type(b[k])in(int,float)and math.isfinite(b[k])for k in ('vm_pu','va_degree')),'finite bus measurements')
  expected_branches={b['id']:b for b in benchmark['branches']}
  for z in out['branches']:
   need(z['rate_mva']==expected_branches[z['id']]['rate_mva']and z['status']==expected_branches[z['id']]['status'],'original output branch rating/status')
   need(all(type(z[k])in(int,float)and math.isfinite(z[k])for k in ('from_p_mw','to_p_mw','from_q_mvar','to_q_mvar','from_mva','to_mva')),'finite branch measurements')
  need(acinput['branches']==benchmark['branches'],'original AC network/thermal ratings');need(len(acinput['generators'])==5,'thermal population');j=t-offset
  for g in acinput['generators']:
   uid=g['id'];m=marg[uid,t];node=mapping[uid];need(g['bus']==str(int(node)-1),'thermal node');need(abs(g['pg_mw']-r['primal'][vi[f'power[{uid},{t}]']])<1e-6,'AC schedule equals new SCED')
   for field,value in [('pmin_mw',m['raw_pmin_mw']),('pmax_mw',m['raw_pmax_mw']),('qmin_mvar',bg[g['bus']]['qmin_mvar']),('qmax_mvar',bg[g['bus']]['qmax_mvar']),('vg_pu',bg[g['bus']]['vg_pu'])]:need(g[field]==value,'original AC capability/setpoint')
  for b in acinput['buses']:
   node=str(int(b['id'])+1);orig=bb[b['id']];gross=local['load_mw'][node][j];p=gross-local['fixed_injection_mw'][node][j]
   if node in local['renewable']:p-=r['primal'][vi[f'vre_power[{node},{t}]']]
   for uid,u in local['resources'].items():
    if u['kind']=='battery'and u['node']==node:p-=r['primal'][vi[f'power[{uid},{t}]']]
   need(abs(b['pd_mw']-p)<1e-6,'same gross load/fixed/VRE/BESS source');qload=gross*orig['qd_mvar']/orig['pd_mw']if orig['pd_mw']else 0.;need(abs(b['qd_mvar']-qload)<1e-6,'original reactive proxy')
   for field in ('vmin_pu','vmax_pu','base_kv','gs_mw','bs_mvar'):need(b[field]==orig[field],'original AC bus capability')
  need(out.get('backend_version')=='3.5.6'and out.get('ac_converged')is True,'resolved known research AC required')
  # Recompute unit attribution from actual numeric P/Q; ignore external safe/fail flags.
  for g in out['generators']:
   uid=g['id'];m=marg[uid,t];orig=bg[str(int(mapping[uid])-1)]
   if g['p_mw']<m['dispatch_min_mw']-1e-6 or g['p_mw']>m['dispatch_max_mw']+1e-6:
    global_unsafe.append(dict(cell=t,unit=uid,reason='slack_loss_allocation_pending'if uid=='C1'else'P_AGC_violation_causal_attribution_pending',actual=g['p_mw'],bounds=[m['dispatch_min_mw'],m['dispatch_max_mw']],raw_result=deepcopy(item['result'])))
   if g['q_mvar']<orig['qmin_mvar']-1e-6 or g['q_mvar']>orig['qmax_mvar']+1e-6:global_unsafe.append(dict(cell=t,reason='Q limit; AGC-specific unit cause not established'))
  for b in out['buses']:
   orig=bb[b['id']]
   if not orig['vmin_pu']-1e-6<=b['vm_pu']<=orig['vmax_pu']+1e-6:global_unsafe.append(dict(cell=t,reason='voltage; no unit attribution'))
  for z in out['branches']:
   if max(z['from_mva'],z['to_mva'])>z['rate_mva']+1e-6:global_unsafe.append(dict(cell=t,reason='branch thermal; no unit attribution'))
  if out['max_original_network_residual_mva']>1e-6:global_unsafe.append(dict(cell=t,reason='original AC residual'))
  checked.append(t)
 need(checked,'AC evidence missing; no pass by empty list')
 return dict(binding=request['binding'],unit_failures=failures,global_unsafe=global_unsafe,checked_cells=checked,full_hour_AC_covered=len(checked)==12,KKT=k,all_other_checks_pending=['AGC normal health','counterfactual no-increase curtailment','operator/publication authorization'],can_publish=False,payable=False,source_refs=proof)
