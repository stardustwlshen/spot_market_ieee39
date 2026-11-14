"""Fresh-run simulated executed mean powers to R30 research AC, no AC called here."""
from pathlib import Path
from copy import deepcopy
import json,hashlib,math
B=Path(__file__).resolve().parent;R=B.parent;MAP={'C1':'31','C2':'32','C3':'38','CCGT':'35','GT':'36'}
def need(x,m):
 if not x:raise ValueError(m)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def write(p,x):
 with Path(p).open('x')as f:json.dump(x,f,indent=2,allow_nan=False)
def gross_q(load,bp,bq):
 need(all(type(x)in(int,float)and math.isfinite(x)for x in (load,bp,bq))and load>=0,'known finite gross load/Q proxy')
 need(bp!=0 or load==0,'positive load with zero benchmark P has unknown Q')
 return load*bq/bp if bp else 0.
def build(benchmark,inp,cell,j):
 need(set(cell['thermal'])==set(MAP),'five thermal inventory');need(inp['reserve'][j]['positive_mw']==inp['reserve'][j]['accident_mw']==400,'original400')
 ac=deepcopy(benchmark);ledger=[];bybus={b['id']:b for b in ac['buses']};bg={g['bus']:g for g in benchmark['generators']};need(set(bybus)=={str(i)for i in range(39)},'benchmark zero-based39')
 for b in ac['buses']:
  node=str(int(b['id'])+1);gross=inp['load_mw'][node][j];b['qd_mvar']=gross_q(gross,b['pd_mw'],b['qd_mvar']);b['pd_mw']=gross;b['type']='PQ';ledger.append(dict(id='gross_load:'+node,bus=node,p_injection_mw=-gross,q_injection_mvar=-b['qd_mvar'],kind='gross_load'))
 for node,vals in inp['fixed_injection_mw'].items():
  p=vals[j];bybus[str(int(node)-1)]['pd_mw']-=p;ledger.append(dict(id='fixed_tie:'+node,bus=node,p_injection_mw=p,q_injection_mvar=0.,kind='explicit_fixed_PQ'))
 for node,z in cell['renewable'].items():
  need(z['reserve_credit_mw']==z['AGC_credit_mw']==0 and abs(z['availability_mw']-z['dispatched_mw']-z['curtailed_mw'])<1e-6,'VRE conservation/no services')
  p=z['dispatched_mw'];bybus[str(int(node)-1)]['pd_mw']-=p;ledger.append(dict(id='VRE:'+node,bus=node,p_injection_mw=p,q_injection_mvar=0.,kind='VRE_fixed_PQ'))
 for uid,z in cell['batteries'].items():
  node=inp['resources'][uid]['node'];p=z['discharge_mw']-z['charge_mw'];bybus[str(int(node)-1)]['pd_mw']-=p;ledger.append(dict(id=uid,bus=node,p_injection_mw=p,q_injection_mvar=0.,kind='BESS_fixed_PQ'))
 gens=[];capacity={}
 for uid,node in MAP.items():
  need(inp['resources'][uid]['node']==node,'same thermal bus');z=cell['thermal'][uid];row=inp['resources'][uid]['rows'][j]
  need(z['phase']in('stable','off'),'transition snapshot unsupported');need((z['phase']=='stable')==(row['phase']=='stable'),'phase matches source')
  if z['phase']=='off':need(abs(z['mean_mw'])<1e-8,'offline active injection');continue
  need(row['agc_up_reserved_mw']==row['agc_down_reserved_mw']==z['agc_up_reserved_mw']==z['agc_down_reserved_mw']==10.,'synthetic AGC10/10')
  bus=str(int(node)-1);g=deepcopy(bg[bus]);g.update(id=uid,bus=bus,pg_mw=z['mean_mw'],qg_mvar=0.,pmin_mw=row['min_mw']-10.,pmax_mw=row['max_mw']+10.);gens.append(g);bybus[bus]['type']='SLACK'if uid=='C1'else'PV';capacity[uid]=dict(row,scheduled_p_mw=z['mean_mw'],raw_pmin_mw=g['pmin_mw'],raw_pmax_mw=g['pmax_mw']);ledger.append(dict(id=uid,bus=node,p_injection_mw=z['mean_mw'],q_injection_mvar=None,kind='thermal_AC_Q_proxy'))
 need(any(g['id']=='C1'for g in gens),'explicit C1 slack must be online');need(next(g for g in gens if g['id']=='GT')['pmax_mw']==300.,'original GTmax300')
 ac['generators']=gens;ac['provenance']='fresh-run executed 5min mean representative; R30 benchmark AC; gross load constant Q/P proxy; VRE/BESS Q=0; NOT measured or official';ac['operating_point_kind']='research_snapshot'
 total=math.fsum(z['p_injection_mw']for z in ledger);need(abs(total)<1e-6,'scheduled net injection conservation')
 need(abs(math.fsum(g['pg_mw']for g in gens)-math.fsum(b['pd_mw']for b in ac['buses'])-total)<1e-6,'AC adapter active ledger exact')
 limits=[]
 for line in ac['branches']:
  u=str(int(line['from_bus'])+1);v=str(int(line['to_bus'])+1);keys=[prefix+a+'-'+b for prefix in ('L','T')for a,b in ((u,v),(v,u))if prefix+a+'-'+b in inp['network']];need(len(keys)==1,'unique DC line/transformer mapping');d=inp['network'][keys[0]];limits.append(dict(ac_id=line['id'],from_bus=u,to_bus=v,AC_rate_MVA=line['rate_mva'],DC_lower_MW=d['lower_mw'][j],DC_upper_MW=d['upper_mw'][j]))
 return dict(ac_input=ac,ledger=ledger,thermal_capacity=capacity,branch_limit_comparison=limits,scheduled_net_injection_mw=total,start=cell['start'],end=cell['end'],research_only=True,formal_security_certification=False,slack_policy='C1/bus31 loss adjustment reported separately, never written back to SCED/settlement',reactive_policy='grossPload times benchmark Q/P; preserve negative Q; fixed PQ VRE/BESS Q0',mean_power_is_quasisteady_proxy=True)
def postcheck(prepared,result):
 if not result.get('ac_converged'):return dict(status='unresolved_AC',research_safe=False,formal_security_certification=False)
 gs={g['id']:g for g in result['generators']};rows=[]
 for uid,c in prepared['thermal_capacity'].items():
  g=gs[uid];p=g['p_mw'];head=c['raw_pmax_mw']-p-c['agc_up_reserved_mw'];down=p-c['raw_pmin_mw']-c['agc_down_reserved_mw'];rows.append(dict(unit=uid,p_AC_mw=p,scheduled_p_mw=c['scheduled_p_mw'],slack_loss_adjustment_mw=p-c['scheduled_p_mw']if uid=='C1'else 0.,up_headroom_after_AGC_mw=head,down_headroom_after_AGC_mw=down,positive_available_mw=max(0.,min(head,c['positive_cap_mw'])),accident_available_mw=max(0.,min(head,c['accident_cap_mw']))))
 positive=math.fsum(x['positive_available_mw']for x in rows);accident=math.fsum(x['accident_available_mw']for x in rows);agc=all(min(x['up_headroom_after_AGC_mw'],x['down_headroom_after_AGC_mw'])>=-1e-6 for x in rows)
 return dict(status='research_safe'if result['ac_snapshot_safe']and agc and min(positive,accident)>=400-1e-6 else'research_unsafe',research_safe=bool(result['ac_snapshot_safe']and agc and min(positive,accident)>=400-1e-6),thermal=rows,positive_available_mw=positive,accident_available_mw=accident,positive_requirement_mw=400,accident_requirement_mw=400,synthetic_AGC50_headroom_preserved=agc,fixed_PQ_service_credit=0,slack_adjustment_not_settlement=True,formal_security_certification=False)
