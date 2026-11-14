"""Typed §58 post-clearing research safety interface; no native or dispatch."""
import argparse,json,math
from pathlib import Path
from datetime import datetime
CHECKS=('agc_normal','capacity_balance','network_sections','hydro_vibration','storage_soc','no_increase_curtailment')
class InputError(ValueError):pass
def need(ok,msg):
 if not ok:raise InputError(msg)
def number(v):
 need(type(v)in(int,float)and math.isfinite(v)and v>=0,'finite nonnegative numeric required');return v
def timestamp(v):
 try:d=datetime.fromisoformat(v)
 except (ValueError,TypeError):raise InputError("aware ISO timestamp required")
 need(d.tzinfo is not None,"aware ISO timestamp required");return d
def evidence(e,hour,gate):
 need(isinstance(e,dict),'evidence object required')
 for k in ('id','source','source_grade','as_of'):
  need(isinstance(e.get(k),str)and bool(e[k].strip()),'evidence '+k+' required')
 need(e['source_grade']in('research','retained_reposted_scan','caller_supplied_unverified'),'unsupported source grade')
 need(timestamp(e.get('hour_start'))==hour,'evidence clearing hour mismatch')
 need(timestamp(e.get('as_of'))<=gate,'future evidence')
 return e

def run(d):
 need(d.get('schema')=='gd.agc.safety.r77.v1','schema required')
 need(d.get('mode')=='research','formal certification unsupported')
 hour=timestamp(d.get('hour_start'));gate=timestamp(d.get('gate_as_of'))
 target=number(d.get('procurement_target_mw'));units=d.get('awarded_units');need(isinstance(units,list),'awarded_units required')
 ids=set();failures={};retained=[];unknown=[];awards={};records={}
 for u in units:
  rid=u.get('resource_id');need(isinstance(rid,str)and rid and rid not in ids,'unique resource id required');ids.add(rid);records[rid]=u
  need(u.get('kind')in('thermal','hydro','storage'),'explicit resource kind required; gate confers no qualification')
  awards[rid]=number(u.get('awarded_mw'));number(u.get('sorting_price'));number(u.get('P'));number(u.get('k1'));evidence(u.get('award_evidence'),hour,gate)
  c=u.get('checks');need(isinstance(c,dict)and set(c)==set(CHECKS),'all six checks explicitly required')
  for name,x in c.items():
   need(isinstance(x,dict)and x.get('status')in('pass','fail','unknown','not_applicable'),'typed safety status required')
   need(isinstance(x.get('reason'),str)and bool(x['reason'].strip()),'reason required');evidence(x.get('evidence'),hour,gate)
   if x['status']=='not_applicable':
    need((name=='hydro_vibration'and u['kind']!='hydro')or(name=='storage_soc'and u['kind']!='storage'),'not_applicable only for technology-specific irrelevant check')
   if x['status']=='unknown':unknown.append(dict(resource_id=rid,check=name,reason=x['reason']))
   if x['status']=='fail':
    need(x.get('attribution')=='unit_specific','aggregate comparison cannot attribute unit failure')
    cause=x.get('cause_id');need(isinstance(cause,str)and bool(cause.strip()),'sourced common cause_id required')
    evidence(x.get('cause_evidence'),hour,gate)
    failures.setdefault(cause,[]).append(dict(resource_id=rid,check=name,reason=x['reason'],evidence=x['evidence'],cause_evidence=x['cause_evidence']))
 # All clearly failed units are removed, not merely enough to reach target. Ordering is within each cause.
 groups=[];removed=set()
 for cause,rows in sorted(failures.items()):
  ce={json.dumps(x['cause_evidence'],sort_keys=True)for x in rows};need(len(ce)==1,'same cause requires identical cause evidence')
  by={}
  for x in rows:by.setdefault(x['resource_id'],[]).append(x)
  order=sorted(by,key=lambda r:(-records[r]['sorting_price'],records[r]['P'],records[r]['k1'],r))
  groups.append(dict(cause_id=cause,removal_order=order,reasons_by_unit=by));removed.update(order)
 retained=[rid for rid in awards if rid not in removed];remaining=sum(awards[r]for r in retained)
 aggregate=d.get('aggregate_no_increase',{'status':'unknown','reason':'not supplied'})
 need(isinstance(aggregate,dict)and aggregate.get('status')in('unknown','research_no_increase_not_certified'),'aggregate comparison cannot certify or attribute units')
 need(isinstance(aggregate.get('reason'),str)and bool(aggregate['reason'].strip()),'aggregate reason required')
 if 'evidence'in aggregate:evidence(aggregate['evidence'],hour,gate)
 return dict(schema='gd.agc.safety.r77.result.v1',scope='research post-clearing evidence gate; not operator safety certification',hour_start=d['hour_start'],gate_as_of=d['gate_as_of'],removed=sorted(removed),removal_groups=groups,retained=retained,remaining_awarded_mw=remaining,unmet_requirement_mw=max(0,target-remaining),unknown_checks=unknown,requires_reclearing=bool(removed)or bool(unknown)or remaining<target,requires_operator_resolution=bool(unknown)or aggregate['status']in('unknown','research_no_increase_not_certified'),aggregate_no_increase=aggregate,ordering='within same sourced cause: sorting_price DESC, P ASC, k1 ASC; exact tie resource_id ASC research only',cross_cause_order_prescribed=False,formal_safety_certified=False,formal_publication_allowed=False,dispatch_authorized=False,qualification_created=False,native_calls=0)

def main():
 p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('--output');a=p.parse_args()
 try:r=run(json.loads(Path(a.input).read_bytes()))
 except (InputError,KeyError,TypeError,ValueError)as e:print(json.dumps({'status':'invalid_or_unsupported','error':str(e),'native_calls':0}));return 2
 s=json.dumps(r,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
 if a.output:
  with open(a.output,'x')as f:f.write(s)
 else:print(s,end='')
 return 0
if __name__=='__main__':raise SystemExit(main())
