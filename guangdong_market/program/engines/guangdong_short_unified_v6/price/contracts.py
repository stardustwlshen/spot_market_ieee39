"""Pure explicit PRICE input contract, not a solver or official certification."""
from pathlib import Path
from datetime import datetime
import hashlib,math,json

def need(ok,m):
 if not ok:raise ValueError(m)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def stamp(v):
 d=datetime.fromisoformat(v);need(d.utcoffset() is not None,'aware timestamp required');return d

def source(z,at):
 need(z['grade'] in ['explicit_research_assumption','official_document','accepted_model_output'],'source grade')
 need(z['locator'] and sha(z['document'])==z['sha256'],'source bytes/locator')
 need(stamp(z['available_at'])<=at,'future source unavailable')

def scalar(z,unit,at,lower=None,upper=None):
 v=z['value'];need(type(v)in(int,float)and math.isfinite(v),'missing/nonfinite scalar');need(z['unit']==unit,'wrong unit');source(z['source'],at)
 need((lower is None or v>=lower)and(upper is None or v<=upper),'value outside contract range');return v

CONSTRAINTS={'negative_reserve','primary_frequency_reserve','group_power','group_energy','sections','hydro_spill','interregional_optimization'}
PENALTIES={'load','accident','network','renewable_curtailment'}

def resolve(c,inventory,as_of):
 need(c['schema']=='gd.PRICE.explicit_inputs.v23','schema');need(c['mode']=='research','formal PRICE disabled until rule ambiguities/parameters/eligibility/publication are verified');at=stamp(as_of)
 identity=c['identity'];need(all(identity.get(k) for k in ['run_id','branch_id','window_id','case_sha256','DA_acceptance_sha256','SCED_acceptance_sha256','AGC_award_sha256']),'complete same-branch source identity')
 for k in ['case','DA','SCED','AGC']:source(c['sources'][k],at)
 need(c['sources']['case']['sha256']==identity['case_sha256'],'case identity')
 for k,key in [('DA','DA_acceptance_sha256'),('SCED','SCED_acceptance_sha256'),('AGC','AGC_award_sha256')]:need(c['sources'][k]['sha256']==identity[key],k+' source identity')
 n=c['time_mapping']['SCED_cells'];need(type(n)is int and 1<=n<=8 and c['time_mapping']['SCED_cells']==n and c['time_mapping']['SCED_step_minutes']==15 and c['time_mapping']['PRICE_periods']==n and c['time_mapping']['PRICE_step_minutes']==15,'provincial same-period8x15/8x15 domains')
 need(c['time_mapping']['anchor'] in ['cutoff_research','delivery_research'] and c['time_mapping']['publish_quarters']==1,'explicit anchor and single published quarter')
 delta=scalar(c['delta'],'dimensionless',at,0,1)
 need(c['domain'] in ['DA_reference_research','current_final_SCED_reference_research'],'explicit research domain')
 for k in ['storage_reference','storage_mode','storage_SOC_start','storage_terminal','storage_cycle_sign','charge_discharge_sign','five_to_fifteen_power','ramp_mapping']:
  need(c['interpretations'][k]['choice'] and c['interpretations'][k]['status'] in ['confirmed_sign_convention','explicit_research_interpretation','official_ambiguity_unresolved'],'explicit interpretation '+k);source(c['interpretations'][k]['source'],at)
 need(c['storage_bounds_formula']=='bounded_absolute_reference_intersection_RESEARCH_NOT_LITERAL','literal printed max/min is ambiguous: no automatic official correction')
 penalties=c['penalties'];need(set(penalties)==PENALTIES,'explicit ML MR M1 M2 required');rates={k:scalar(v,'yuan_per_MWh',at,0)for k,v in penalties.items()}
 need(set(c['VRE_offers'])==set(inventory['VRE']),'complete VRE offer inventory, no -50 default');offers={u:scalar(z,'yuan_per_MWh',at)for u,z in c['VRE_offers'].items()}
 need(set(c['eligibility'])==set(inventory['thermal']),'complete thermal pricing eligibility inventory')
 eligibility={}
 for u,z in c['eligibility'].items():
  source(z['source'],at);need(len(z['quarters'])==n,'eight explicit eligibility quarters')
  for e in z['quarters']:need(e in ['off','fixed_nonpricing','pricing_stable'],'unknown/transition eligibility unsupported: never silently pricing')
  eligibility[u]=z['quarters']
 need(set(c['constraints'])==CONSTRAINTS,'explicit missing-constraint inventory');missing=[]
 for k,z in c['constraints'].items():
  source(z['source'],at);need(z['status']in['required','inapplicable','unimplemented'],'constraint applicability')
  if z['status']=='inapplicable':need(z.get('reason')and z.get('verified_empty_inventory')is True,'inapplicability requires empty-domain evidence')
  elif z['status']=='unimplemented':missing.append(k)
  else:
   need(z.get('data')is not None,'required constraint data missing: '+k)
   if k in ['negative_reserve','primary_frequency_reserve']:
    need(len(z['data']['requirement_MW'])==n and all(type(v)in(int,float)and math.isfinite(v)and v>=0 for v in z['data']['requirement_MW']),'eight explicit reserve requirements')
    need(set(z['data']['qualified_resources']).issubset(set(inventory['thermal'])),'no invented reserve eligibility')
 need(c['constraints']['interregional_optimization']['status']=='inapplicable','user excludes interregional trading; fixed physical injection remains')
 safety=c['safety'];need(safety['kind']in['unchecked','fixed_witness_only','marginal_domain_candidate'],'safety kind')
 if safety['kind']!='unchecked':
  source(safety['source'],at);need(safety['identity']==identity,'AC/SCED/AGC branch mismatch');need(safety['iteration_id'],'missing final safety iteration')
 need(not c.get('request_safe_PRICE_certificate',False),'no certified marginal domain: fixed safe P witness is not safety-certified PRICE')
 need(not missing,'unsupported required constraints: '+','.join(sorted(missing)))
 return dict(delta=delta,VRE_offers=offers,penalties=rates,eligibility=eligibility,domain=c['domain'],constraints=c['constraints'],identity=identity,safety=safety,formal=False,safe_PRICE_certified=False,solver_enabled=False)

def reference_bounds(ref,lo,hi,delta,status):
 need(all(type(v)in(int,float)and math.isfinite(v)for v in [ref,lo,hi,delta])and lo<=hi,'finite bounds')
 if status=='off':need(lo<=0<=hi,'off state inconsistent');return (0.,0.)
 if status=='fixed_nonpricing':need(lo<=ref<=hi,'fixed reference beyond physical bounds');return (ref,ref)
 need(status=='pricing_stable','no hidden eligibility');a=max(lo,ref-delta*abs(ref));b=min(hi,ref+delta*abs(ref));need(a<=b,'empty research reference domain');return a,b

def map_24_to_8(p5):
 need(len(p5)==24 and all(type(x)in(int,float)and math.isfinite(x)for x in p5),'24 finite mean-MW cells')
 q=[math.fsum(p5[3*j:3*j+3])/3 for j in range(8)]
 need(abs(math.fsum(p5)/12-math.fsum(q)/4)<=1e-8,'energy mismatch');return q

def group_energy_rows(group_units,quarter_columns,qmin,qmax):
 # Equivalent full-two-hour energy ONLY; not intermediate five-minute constraints.
 need(len(quarter_columns)==8 and math.isfinite(qmin)and math.isfinite(qmax)and qmin<=qmax,'explicit 2h group MWh limits')
 return {'terms':[(quarter_columns[q][u],.25)for q in range(8)for u in group_units],'lb_MWh':qmin,'ub_MWh':qmax,'five_minute_subperiod_feasibility_proved':False}
