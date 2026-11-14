from pathlib import Path
import json,hashlib,math
B=Path(__file__).resolve().parent;R=B.parent
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
d=read(B/'PAYLOAD.json');m=read(B/'MAPPING.json');z=read(B/'AC_RESULT.json');ac=m['ac_input'];gens={g['id']:g for g in ac['generators']};rs={r['id']:r for r in d['resources']};slack=d['slack_resource'];sg=gens[slack]
assert d['loss_proxy_mw']==0 and abs(m['precheck']['net_generation_minus_physical_load_mw'])<1e-6
limit=sg['pmax_mw']-rs[slack]['agc_up_mw'];assert abs(sg['pg_mw']-limit)<1e-8
assert all(b['gs_mw']>=0 for b in ac['buses'])and all(l['r_pu']>=0 for l in ac['branches'])
# Any bus with nonzero net real demand and exclusively positive-R incident branches
# proves strictly positive series loss is unavoidable with no local AC generator.
choices=[]
for b in ac['buses']:
 incident=[l for l in ac['branches']if l['status'] and b['id']in(l['from_bus'],l['to_bus'])]
 if b['pd_mw']>1e-6 and all(g['bus']!=b['id']for g in ac['generators']) and incident and all(l['r_pu']>0 for l in incident):
  choices.append({'bus':b['id'],'net_physical_P_demand_MW':b['pd_mw'],'incident_branches':[{'id':l['id'],'r_pu':l['r_pu']}for l in incident]})
assert choices
invpath=R/'guangdong_agc_ac_controls_v2_20261112/INVERTER_INVENTORY.json';inventory={u['id']:u for u in read(invpath)['units']};verified=[]
for uid,r in rs.items():
 if r['kind']!='thermal':
  u=inventory[uid];assert r['bus']==str(u['internal_bus'])and r['kind']==u['kind'];verified.append({'id':uid,'AC_bus':r['bus'],'original_nameplate_P_MW':u['nameplate_P_MW'],'P_used_MW':d['plan'][uid]['p_mean_mw'],'Q_Mvar':d['controls'][uid]['q_mvar']})
report={'status':'proved_fixed_non_slack_P_QV_only_domain_empty_under_AGC_limit','zero_loss_SCED_plan':True,'slack_id':slack,'slack_plan_MW':sg['pg_mw'],'slack_raw_Pmax_MW':sg['pmax_mw'],'AGC_up_reserved_MW':rs[slack]['agc_up_mw'],'slack_allowed_max_after_AGC_MW':limit,'actual_loss_MW':sum(l['from_p_mw']+l['to_p_mw']for l in z['branches']),'positive_loss_structural_witness':choices[0],'argument':'With all non-slack P, fixed injections and physical load unchanged, Pslack=Pplan+network real loss. Nonnegative R and shunt conductance yield nonnegative loss; the named loaded non-generator bus with only positive-R incident branches requires a nonzero real branch current and strictly positive loss. Pplan is already the AGC-adjusted slack upper bound, so any such loss violates it. Q/V control or added bounded inverter Q cannot remove this active-power obstruction. This is not a proof that redispatch plus reactive control is infeasible.','original_device_map_checked':verified,'AC_residual_MVA':z['max_original_network_residual_mva'],'nonlinear_calls_used':0,'further_AC_calls_used':0,'next_minimum_action':'same-source loss proxy feedback into all relevant SCED energy/PTDF rows, allow economically/physically constrained non-slack redispatch, preserve AGC/dual400/SOC/notice and source binding; only then form one bounded reactive-control candidate and independent AC','not_executed':['new SCED feedback','new capability optimization','PRICE acceptance','execution ledger','security ticket'],'formal_release':False,'security_release':False,'source_pins':{str(p.relative_to(R)):sha(p)for p in [B/'PAYLOAD.json',B/'MAPPING.json',B/'AC_RESULT.json',B/'SUMMARY.json',invpath]}}
(B/'FIXED_P_OBSTRUCTION.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'status':report['status'],'witness':choices[0],'AC_residual_MVA':report['AC_residual_MVA']}))
