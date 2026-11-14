"""Explicit eligible noquote VRE PRICE block; no fabricated thermal/ramp/service type."""
import math
import pyomo.environ as p
from r53_bridge import require,digest

def attach_eligible_vre(model,dto,*,bid_proxy,price_curtailment_penalty,parameter_source):
 require(type(bid_proxy)in(int,float)and math.isfinite(bid_proxy),'explicit finite bid proxy')
 require(type(price_curtailment_penalty)in(int,float)and math.isfinite(price_curtailment_penalty)and price_curtailment_penalty>=0 and parameter_source,'explicit independent PRICE M2prime')
 x=dto['physical_lift'];nodes=[b for b in dto['nodes']if any(a>0 for a in x['renewable_availability_mw'][b])];Q=range(98);J=range(576);delta=dto['delta']
 require(len(nodes)==5,'original five VRE nodes');A=x['renewable_availability_mw'];ref=x['renewable_dispatched_mw'];cur=x['renewable_curtailed_mw'];mapping=x['market_to_physical']
 model.physical_vre_power=p.Var(nodes,J,domain=p.NonNegativeReals);model.physical_vre_curtailment=p.Var(nodes,J,domain=p.NonNegativeReals);model.vre_power=p.Var(nodes,Q,domain=p.NonNegativeReals);model.vre_curtailment=p.Var(nodes,Q,domain=p.NonNegativeReals);model.vre_rows=p.ConstraintList();cost=0.;records={}
 for b in nodes:
  rows=[]
  for j in J:
   require(abs(ref[b][j]+cur[b][j]-A[b][j])<=1e-6,'new source VRE conservation')
   for v in(model.physical_vre_power[b,j],model.physical_vre_curtailment[b,j]):v.setub(A[b][j])
   model.vre_rows.add(model.physical_vre_power[b,j]+model.physical_vre_curtailment[b,j]==A[b][j])
   # ExpressionData references in balance/flows are preserved; no fixed-P double count.
   old=model.physical_injection[b,j].expr;model.physical_injection[b,j].set_value(old+model.physical_vre_power[b,j]-ref[b][j])
  for q,ids in enumerate(mapping):
   av=math.fsum(A[b][j]for j in ids)/3;pv=math.fsum(ref[b][j]for j in ids)/3;cv=math.fsum(cur[b][j]for j in ids)/3
   lo=max(0.,(1-delta)*pv);hi=min(av,(1+delta)*pv);require(lo<=hi+1e-8,'VRE reference band')
   model.vre_power[b,q].setlb(lo);model.vre_power[b,q].setub(hi);model.vre_curtailment[b,q].setub(av)
   model.vre_rows.add(model.vre_power[b,q]+model.vre_curtailment[b,q]==av)
   model.vre_rows.add(3*model.vre_power[b,q]==sum(model.physical_vre_power[b,j]for j in ids))
   old=model.injection[b,q].expr;model.injection[b,q].set_value(old+model.vre_power[b,q]-pv)
   weight=dto['grid'][q]['objective_weight_h'];cost+=weight*(bid_proxy*model.vre_power[b,q]+price_curtailment_penalty*model.vre_curtailment[b,q])
   rows.append(dict(availability_mw=av,SCED_reference_dispatched_mw=pv,SCED_reference_curtailed_mw=cv,price_power_lower_mw=lo,price_power_upper_mw=hi,bid_proxy_yuan_mwh=bid_proxy,M2_price_prime_yuan_mwh=price_curtailment_penalty,objective_weight_h=weight,physical_indices=ids))
  records[b]=rows
 model.objective.set_value(model.objective.expr+cost)
 return dict(schema='gd.research.r53.eligible_VRE_PRICE.block.v1',nodes=nodes,rows=records,physical_availability_mw={b:A[b]for b in nodes},physical_SCED_dispatched_mw={b:ref[b]for b in nodes},physical_SCED_curtailed_mw={b:cur[b]for b in nodes},price_eligible=True,physical_DA_schedule=True,financial_DA_participation='unknown',financial_DA_entitlement=False,synthetic_PRICE_optimization_assumption=True,participation_basis='Physical PRICE research assumption only; no actual financial opt-in evidence',historical_2025_provincial_floor_yuan_mwh=-50.,historical_floor_source='https://drc.gd.gov.cn/gkmlpt/content/4/4775/post_4775501.html',public_baseline_not_dynamic_operator_confirmation=True,noquote_rule_source='https://nfj.nea.gov.cn/xwzx/tzgg/202601/P020260104519707491129.pdf',noquote_not_ineligible=True,delta_is_research=delta,bid_proxy_yuan_mwh=bid_proxy,public_bid_floor_baseline_requires_source_version=True,M2_price_prime_yuan_mwh=price_curtailment_penalty,parameter_source=parameter_source,dispatch_M2_is_separate_research_parameter=1000.,reserve_credit_mw=0.,AGC_credit_mw=0.,no_thermal_or_ramp_identity_invented=True,quantity_publication_must_remain_SCED_reference_not_PRICE_solution=True,official=False)
