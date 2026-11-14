"""Research-only storage Fm candidate mapping; no clearing, eligibility grant or settlement."""
import argparse,json
from decimal import Decimal,localcontext
from datetime import datetime,timedelta
from pathlib import Path
class Missing(ValueError):pass
class Unsupported(ValueError):pass
def need(v,msg):
 if not v:raise Missing(msg)
def dec(v):
 need(isinstance(v,(str,int))and not isinstance(v,bool),'decimal strings/integer inputs required, no binary float')
 d=Decimal(v);need(d.is_finite()and d>=0,'finite nonnegative required');return d
def stamp(v):
 d=datetime.fromisoformat(v);need(d.tzinfo is not None,'aware ISO required');return d
def evidence(e,t,now):
 need(isinstance(e,dict),'missing evidence')
 for key in ('id','source','source_grade'):need(isinstance(e.get(key),str)and e[key].strip(),'missing evidence '+key)
 need(e['source_grade']in('research','retained_reposted_scan','caller_supplied_unverified'),'source grade unsupported')
 need(stamp(e['hour_start'])==t and stamp(e['as_of'])<=now,'wrong-hour or future evidence')
def fm(x,p):
 if x>=p['Ux']:raise Unsupported('cumulative_x_at_or_above_Ux; Fm diagnostic 0 at Ux, no sorting price')
 return p['Uy']+(p['zy']-p['Uy'])*x/p['zx'] if x<=p['zx'] else p['zy']*(p['Ux']-x)/(p['Ux']-p['zx'])
def run(d):
 with localcontext()as ctx:
  ctx.prec=60
  return calculate(d)
def calculate(d):
 need(d.get('schema')=='gd.agc.storage.r78.v1'and d.get('mode')=='research','research schema required; formal unsupported')
 t=stamp(d['hour_start']);now=stamp(d['as_of']);z=d['zone'];need(isinstance(z,str)and z,'zone required')
 pop=d['population'];need(pop.get('complete')is True,'complete distribution-zone storage population required');evidence(pop.get('evidence'),t,now)
 need(pop.get('zone')==z,'population zone mismatch')
 demand=dec(pop['demand_mw']);need(demand>0,'positive zone demand required')
 snap=d['parameters'];evidence(snap.get('evidence'),t,now);need(isinstance(snap.get('version'),str)and snap['version'],'parameter version required')
 need(stamp(snap['effective_from'])<=t and t+timedelta(hours=1)<=stamp(snap['effective_to']),'parameters must cover hour')
 keys=('G','H','QLL','Ux','Uy','zx','zy','Qlb','bid_floor','bid_upper','lambdaI','lambdaII','lambdaIII','KI_upper','Lss_hours','Ts_hours')
 p={k:dec(snap['values'][k])for k in keys};need(0<p['QLL']<1 and 0<p['zx']<p['Ux'] and 0<p['zy']<p['Uy'],'invalid Fm domain')
 need(p['G']+p['H']==100 and p['lambdaI']+p['lambdaII']+p['lambdaIII']==1,'weight sums')
 need(0<p['bid_floor']<=p['bid_upper'] and p['KI_upper']>0 and p['Lss_hours']>0 and p['Ts_hours']>0,'positive current parameters')
 KU=p['lambdaI']*p['KI_upper']+p['lambdaII']+p['lambdaIII'];need(KU>0,'KU upper bound positive')
 universe=d['performance_universe'];need(universe.get('scope')=='entire_southern_unified_control_area'and universe.get('complete')is True,'external full-area kmax required');evidence(universe.get('evidence'),t,now)
 kmax=dec(universe['kmax']);need(kmax>0,'positive full-area kmax');need(kmax<=KU,'full-area kmax exceeds common KU; heterogeneous upper bounds unsupported')
 ids=set();rows=[];excluded=[];unresolved=[]
 for u in pop['offers']:
  rid=u['resource_id'];need(isinstance(rid,str)and rid and rid not in ids,'unique resource id');ids.add(rid);need(u.get('participant_type')=='independent_storage_third_party','only section49 independent storage third-party supported');need(u.get('zone')==z,'offer zone mismatch');evidence(u.get('evidence'),t,now)
  for flag in ('submitted','normal_service','operator_excluded'):need(type(u.get(flag))is bool,'explicit '+flag+' required')
  if not u['submitted']or not u['normal_service']or u['operator_excluded']:
   need(isinstance(u.get('exclusion_reason'),str)and u['exclusion_reason'],'exclusion reason required');evidence(u.get('exclusion_evidence'),t,now);excluded.append(dict(resource_id=rid,reason=u['exclusion_reason']));continue
  need(u.get('participation_mode')=='simultaneous_energy_agc','this version only explicit simultaneous energy/AGC participation; AGC-only unsupported')
  qualification=u['qualification']
  for key in ('market_registered','dispatch_direct_control','dispatch_agreement','purchase_sale_contract','agc_capable','initial_model_performance_test','autor_test_published','simultaneous_energy_market_qualified','simultaneous_technical_test_completed'):
   need(qualification.get(key)is True,'missing explicit qualification/test '+key)
  evidence(qualification.get('evidence'),t,now)
  need(dec(qualification['sustained_response_hours'])>=p['Lss_hours']and dec(qualification['autor_test_hours'])>=p['Ts_hours'],'qualification duration evidence insufficient')
  R=dec(u['rated_power_mw']);C=dec(u['installed_capacity_mw']);q=dec(u['declared_capacity_mw']);b=dec(u['bid_yuan_per_mw_mileage']);k=dec(u['k'])
  need(R>0 and C>0 and k>0 and k<=KU and k<=kmax,'invalid positive power/performance bound')
  need(q==q.to_integral_value()and b*10==(b*10).to_integral_value(),'capacity1MW and price0.1 increments')
  need(p['bid_floor']<=b<=p['bid_upper'],'bid outside current supplied limits')
  qmin=min(max(Decimal('.2')*R,p['Qlb']),Decimal('.15')*demand);qmax=min(R,Decimal('.2')*demand)
  need(qmin<=qmax and qmin<=q<=qmax,'capacity outside section44 range')
  r=q/C
  if not p['QLL']<=r<=1:unresolved.append(dict(resource_id=rid,status='capacity_valid_but_Fm_domain_unresolved',ratio=r,qmin=qmin,qmax=qmax));continue
  B=p['G']*(k/KU)/(b/p['bid_floor']);A=p['H']*(1-r)/(1-p['QLL']);S=B+A
  rows.append(dict(resource_id=rid,q=q,b=b,k=k,ratio=r,qmin=qmin,qmax=qmax,base_score=B,reward=A,score=S,P=k/kmax))
 base=dict(schema='gd.agc.storage.r78.result.v1',hour_start=d['hour_start'],zone=z,K_U=KU,external_full_area_kmax=kmax,excluded=excluded,unresolved=unresolved,precision_policy='Decimal precision60; no explicit intermediate quantization; rounded decimal division is implementation strategy, not official production rule',formal_eligibility_granted=False,formal_publication_allowed=False,settlement_Fm_application=False,native_calls=0)
 if unresolved:return dict(base,status='unsupported_domain_no_partial_population_mapping',candidates=[])
 rows.sort(key=lambda a:(-a['score'],a['b'],-a['k'],a['q']));groups=[];cum=Decimal(0);i=0
 while i<len(rows):
  key=lambda a:(a['score'],a['b'],a['k'],a['q'])
  j=i+1
  while j<len(rows)and key(rows[j])==key(rows[i]):j+=1
  group=rows[i:j];cum+=sum((x['q']for x in group),Decimal(0));x=cum/demand
  if x>=p['Ux']:
   F=Decimal(0)if x==p['Ux']else None;unresolved.append(dict(status='cumulative_x_at_or_above_Ux',members=[r['resource_id']for r in group],x=x,Fm_diagnostic=F))
  else:F=fm(x,p)
  for r in group:r.update(tie_group=len(groups),cumulative_q=cum,x=x,Fm=F,sorting_price=None if x>=p['Ux']else r['b']/(r['P']*F))
  groups.append([r['resource_id']for r in group]);i=j
 return dict(base,status='unsupported_cumulative_domain'if unresolved else'research_candidates_only',candidates=rows,tie_groups=groups,market_clearing_performed=False,award_capacity_rule='whole declared q if awarded downstream; no fractional award here')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('input');ap.add_argument('--output');a=ap.parse_args()
 try:r=run(json.loads(Path(a.input).read_bytes()))
 except (ValueError,KeyError,TypeError)as e:r=dict(status='missing_or_unsupported',error=str(e),native_calls=0)
 s=json.dumps(r,default=str,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
 if a.output:
  with open(a.output,'x')as f:f.write(s)
 else:print(s,end='')
 return 0 if r['status']=='research_candidates_only'else 2
if __name__=='__main__':raise SystemExit(main())
