from pathlib import Path
import sys,json,gzip,copy,hashlib,subprocess,argparse,importlib.util,math
B=Path(__file__).resolve().parent;R=B.parent;SRC=R/'guangdong_rt_short_physical_v1_20261112';V=R/'guangdong_rt_recommit_program_v32_20261112'
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2,allow_nan=False)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--round',type=int,required=True);ap.add_argument('--source-ac',required=True);ap.add_argument('--source-mapping',required=True);ap.add_argument('--fix-first-actual',action='store_true');ap.add_argument('--controls');a=ap.parse_args();assert a.round in (1,2,3)
 out=B/f'round_{a.round}';out.mkdir(exist_ok=False)
 ac=read(a.source_ac);ac=ac.get('AC',ac);assert ac['ac_converged'];mapping=read(a.source_mapping);network=mapping['ac_input'];branches={z['id']:z for z in network['branches']};loss={str(int(z['id'])+1):0. for z in network['buses']}
 for b in ac['branches']:
  l=b['from_p_mw']+b['to_p_mw'];assert l>=-1e-8
  for k in ('from_bus','to_bus'):loss[str(int(branches[b['id']][k])+1)]+=l/2
 total=math.fsum(loss.values());q=json.loads(gzip.decompress((SRC/'actual39_v1/SCED_template.json.gz').read_bytes()));original=copy.deepcopy(q);names=read(SRC/'pure39_v2/names.json');ci={x:i for i,x in enumerate(names['constraints'])};vi={x.replace("'",''):i for i,x in enumerate(names['variables'])};changed=[]
 for node,L in loss.items():
  name=f"c_balance['{node}',0]";i=ci[name];row=q['rows'][i];assert row['lb']==row['ub']
  # Actual emitted row orientations vary by bus; use that bus renewable coefficient.
  orientation=dict(row['terms'])[vi[f'renewable_power[{node},0]']];assert orientation in (-1.,1.)
  for side in ('lb','ub'):row[side]+=orientation*L
  changed.append({'row':name,'renewable_coefficient':orientation,'delta_lb':orientation*L,'delta_ub':orientation*L})
 for name,side in [('reserves[1]','lb'),('reserves[2]','ub')]:q['rows'][ci[name]][side]+=total;changed.append({'row':name,'side':side,'delta':total})
 fixes=[]
 if a.fix_first_actual:
  assert a.controls
  # Only the same-point AC actual thermal P; nonthermal P tied to its source SCED.
  for g in ac['generators']:
   key=f"power[{g['id']},0]";j=vi[key];q['rows'].append({'lb':g['p_mw'],'ub':g['p_mw'],'terms':[[j,1.]]});names['constraints'].append('AC_actual_point_'+g['id']);fixes.append({'variable':key,'MW':g['p_mw']})
  payload=read(Path(a.source_mapping).parent/'PAYLOAD.json') if (Path(a.source_mapping).parent/'PAYLOAD.json').exists()else read(B/'round_1/PAYLOAD.json')
  for r in payload['resources']:
   if r['kind']=='thermal':continue
   key=f"renewable_power[{str(int(r['bus'])+1)},0]"if r['kind']!='battery'else None
   keys=[(key,payload['plan'][r['id']]['p_mean_mw'])]if key else[(f"battery.charge[{r['id']},0]",payload['plan'][r['id']]['charge_mw']),(f"battery.discharge[{r['id']},0]",payload['plan'][r['id']]['discharge_mw'])]
   for key,p in keys:q['rows'].append({'lb':p,'ub':p,'terms':[[vi[key],1.]]});names['constraints'].append('AC_actual_fixed_'+key);fixes.append({'variable':key,'MW':p})
 q['options'].update(dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0);assert q['variables']==original['variables'] and q['objective']==original['objective'];allowed={ci[z['row']]for z in changed}
 assert all(row==original['rows'][i]for i,row in enumerate(q['rows'][:len(original['rows'])])if i not in allowed)
 write(out/'NAMES.json',names);write(out/'LOSS_FEEDBACK.json',{'source_AC':a.source_ac,'source_AC_sha256':sha(a.source_ac),'source_mapping_sha256':sha(a.source_mapping),'node_loss_MW':loss,'total_loss_MW':total,'physical_load_unchanged':True,'allocation':'half branch real loss to each endpoint; numerical research proxy not metered demand','source_time':mapping['start'],'forecast_causality':'counterfactual same-cell solved candidate feedback, not external telemetry or past real operation','changed_original_rows':changed,'fixed_actual_point':fixes,'formal':False})
 spec=importlib.util.spec_from_file_location('actual39_native',SRC/'run_actual39.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
 eq,res=mod.native(out,'SCED',q,V/'lp_worker.py',120.);review=mod.checks(eq,res,names,False);write(out/'SCED_REVIEW.json',review)
 assert all(c['kind']=='C'for c in eq['variables']);write(out/'COMPLETED_LP.json',{'fullKKT':review['KKT']['passed'],'objective':res['objective'],'newLP':1})
 subprocess.run([sys.executable,str(B/'export_point.py'),'--out',str(out),'--response',str(out/'SCED/response.json.gz'),'--review',str(out/'SCED_REVIEW.json'),'--loss',str(total)],check=True)
 mapped=read(out/'MAPPING.json')
 if a.controls:
  controls=read(a.controls);bus={b['id']:b for b in mapped['ac_input']['buses']};gen={g['id']:g for g in mapped['ac_input']['generators']}
  for c in controls:
   if c['kind']=='thermal':gen[c['id']]['vg_pu']=c['new_VG_pu']
   else:bus[str(c['internal_bus'])]['qd_mvar']-=c['Q_Mvar']
  mapped['capability_mode']='new_research_inverter';write(out/'CONTROLLED_MAPPING.json',mapped)
 sys.path.insert(0,str(R/'guangdong_r30_ac_security_20261112'));from ac_security import check_ac
 sys.path.insert(0,str(R/'guangdong_fresh_run_ac_bridge_20261112'));from bridge import postcheck
 write(out/'AC_STARTED.json',{'max_calls':1,'mapping_sha256':sha(out/('CONTROLLED_MAPPING.json'if a.controls else'MAPPING.json'))})
 result=check_ac(mapped['ac_input'],max_iterations=60,tolerance_mva=1e-10);write(out/'AC_RESULT.json',result);capacity=postcheck(mapped,result);summary={'actual_AC_calls':1,'actual_LP_calls':1,'AC_status':result['status'],'capacity_review':capacity,'loss_proxy_MW':total,'loss_actual_MW':sum(b['from_p_mw']+b['to_p_mw']for b in result.get('branches',[])),'max_plan_actual_P_difference_MW':max((abs(g['p_change_mw'])for g in result.get('generators',[])),default=None),'formal_release':False,'security_release':False};write(out/'SUMMARY.json',summary);print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
