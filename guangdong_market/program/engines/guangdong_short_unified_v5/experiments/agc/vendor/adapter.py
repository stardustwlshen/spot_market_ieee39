"""R73 typed current-parameter interface; local research projection only."""
import argparse,json,math
from datetime import datetime,timedelta
from pathlib import Path
from projection import clear_hour,RuleInputError,num

def stamp(x):
    try: d=datetime.fromisoformat(x)
    except (ValueError,TypeError): raise RuleInputError('ISO timestamp required')
    if d.tzinfo is None: raise RuleInputError('timezone required')
    return d

def parameter(p,unit,now,start,end):
    if not isinstance(p,dict): raise RuleInputError('missing sourced parameter')
    for k in ('version','source','source_grade'):
        if not isinstance(p.get(k),str) or not p[k].strip(): raise RuleInputError('missing '+k)
    if p.get('source_grade') not in ('research_assumption','retained_reposted_scan','caller_supplied_publication_unverified'):
        raise RuleInputError('unsupported source grade; interface does not authenticate official publications')
    if p.get('unit')!=unit: raise RuleInputError('parameter unit mismatch')
    if stamp(p.get('available_at'))>now: raise RuleInputError('future parameter')
    if not stamp(p.get('effective_from'))<=start<end<=stamp(p.get('effective_to')):
        raise RuleInputError('parameter does not cover whole clearing hour')
    return num(p.get('value'))

def run(data):
    if data.get('schema')!='gd.agc.r73.v1': raise RuleInputError('unsupported schema')
    if data.get('mode')!='research': raise RuleInputError('formal unsupported: local projection is not certified regional clearing')
    if data.get('scope')!='guangdong_local_research_projection' or type(data.get('cross_zone_exchange_assumption')) not in (int,float) or data['cross_zone_exchange_assumption']!=0:
        raise RuleInputError('explicit no-cross-zone research boundary required')
    now=stamp(data.get('as_of')); start=stamp(data.get('hour_start'));end=start+timedelta(hours=1)
    ps=data.get('parameters',{}); values={}
    for name,unit in [('bid_floor','yuan/MW_mileage'),('distribution_zone_demand_mw','MW'),('provincial_procurement_target_mw','MW'),('local_reserved_floor_mw','MW'),('full_control_area_kmax','dimensionless')]:
        values[name]=parameter(ps.get(name),unit,now,start,end)
    units=data.get('units');
    if not isinstance(units,list): raise RuleInputError('units list required')
    active=[]; excluded=[]; ids=set()
    for u in units:
        rid=u.get('resource_id')
        if not isinstance(rid,str) or not rid or rid in ids: raise RuleInputError('missing/duplicate resource id')
        ids.add(rid)
        if u.get('participant_type') not in ('generator','third_party'): raise RuleInputError('explicit participant_type required')
        if type(u.get('submitted')) is not bool: raise RuleInputError('explicit submission status required')
        if not u['submitted']:
            if u['participant_type']!='third_party': raise RuleInputError('generator default declarations not implemented; supply sourced declared inputs')
            excluded.append({'resource_id':rid,'reason':'third_party_not_submitted_no_participation_no_defaults'});continue
        if u.get('kind')!='thermal': raise RuleInputError('unsupported technology: storage Fm/qualification and hydro vibration/plan not implemented')
        stamp(u.get('available_at'))
        active.append(u)
    if 'previous_raw_marginal' in data: raise RuleInputError('historical-price provenance adapter not implemented; no unsourced prior price')
    safety=data.get('safety_excluded',[])
    if not isinstance(safety,list): raise RuleInputError('safety_excluded must be list')
    r=clear_hour(active,demand_mw=values['provincial_procurement_target_mw'],local_floor_mw=values['local_reserved_floor_mw'],full_control_area_kmax=values['full_control_area_kmax'],distribution_zone_demand_mw=values['distribution_zone_demand_mw'],bid_floor=values['bid_floor'],as_of=data['as_of'],hour_start=data['hour_start'],safety_excluded=safety)
    r.update(schema='gd.agc.r73.result.v1',parameters=ps,nonparticipants=excluded,unmet_requirement_mw=r['unmet_mw'],scope=data['scope'],cross_zone_exchange_assumption=0,formal_supported=False,source_authentication_performed=False,security_certified=False,native_calls=0)
    return r

def main():
    p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('--output');a=p.parse_args()
    try: result=run(json.loads(Path(a.input).read_text()))
    except (RuleInputError,KeyError,TypeError,ValueError) as e:
        print(json.dumps({'status':'invalid_or_unsupported_input','error':str(e),'native_calls':0}));return 2
    text=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if a.output:
        with open(a.output,'x') as f:f.write(text)
    else: print(text,end='')
    return 0
if __name__=='__main__':raise SystemExit(main())
