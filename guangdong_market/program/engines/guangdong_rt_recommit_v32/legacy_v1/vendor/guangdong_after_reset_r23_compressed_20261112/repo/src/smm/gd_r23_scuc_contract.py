from datetime import datetime,timedelta
import re
SCHEMA='gd.research.r23.free_scuc_contract.v1'
ECONOMIC_POLICY='continuous48h_research'
REQUIRED_EVIDENCE=('declaration_versions','thermal_curves','qualification','terminal_targets','representative_mapping','economic_mapping')
FIVE=timedelta(minutes=5)
def require(ok,msg):
    if not ok:raise ValueError(msg)
def _time(v,label):
    require(type(v)is str,label+': ISO timestamp required')
    try:r=datetime.fromisoformat(v)
    except ValueError as exc:raise ValueError(label+': invalid timestamp')from exc
    require(r.utcoffset()==timedelta(hours=8),label+': explicit Shanghai +08:00 required');return r
def _text(v,label):require(type(v)is str and bool(v.strip()),label+': nonempty text required')
def _version(r,cutoff,label):
    require(type(r)is dict,label+': record required')
    for k in ('version','source'):_text(r.get(k),label+'.'+k)
    require(type(r.get('source_sha256'))is str and re.fullmatch('[0-9a-f]{64}',r['source_sha256'])is not None,label+': SHA256 required')
    require(r.get('basis')in ('research_assumption','external_declaration'),label+': explicit declaration basis required')
    require(_time(r.get('available_at'),label+'.available_at')<=cutoff,label+': unavailable at cutoff')
    return r['basis']=='external_declaration'
def validate_contract(c,physical_count):
    """Metadata validation, not external authenticity or official equivalence."""
    require(type(c)is dict and c.get('schema')==SCHEMA,'R23 contract schema required')
    require(c.get('mode')in ('research','formal_strict'),'explicit contract mode required')
    require(type(physical_count)is int and physical_count>0,'physical_count must be positive integer')
    day=_time(c.get('day_start'),'day_start');require((day.hour,day.minute,day.second,day.microsecond)==(0,0,0,0),'day_start must be midnight')
    diagnostic='diagnostic_count'in c
    if diagnostic:
        require(type(c['diagnostic_count'])is int and c['diagnostic_count']==physical_count,'diagnostic_count must equal physical_count')
        require(c['mode']=='research'and physical_count<=576,'diagnostic scope is bounded research only');_text(c.get('diagnostic_reason'),'diagnostic_reason')
    else:require(physical_count==576,'continuous48h research requires exactly 576 physical cells')
    require(c.get('economic_policy')==ECONOMIC_POLICY,'explicit continuous48h research objective required')
    dc=day-timedelta(days=1)+timedelta(hours=12,minutes=30);dp=day-timedelta(days=1)+timedelta(hours=20,minutes=30)
    cutoff=_time(c.get('cutoff'),'cutoff');pub=_time(c.get('publication_at'),'publication_at');require(cutoff<pub<=day,'cutoff < publication <= day_start required')
    notice=c.get('schedule_notice');changed=(cutoff,pub)!=(dc,dp)
    if changed or notice is not None:
        require(type(notice)is dict,'schedule override requires versioned available announcement');_version(notice,cutoff,'schedule_notice')
        require(notice.get('cutoff')==c['cutoff']and notice.get('publication_at')==c['publication_at'],'announcement must bind exact overridden schedule')
    declarations=c.get('declarations');require(type(declarations)is list and bool(declarations),'explicit declaration version inventory required')
    subjects=set();formal=True
    for d in declarations:
        require(type(d)is dict,'declaration record required');_text(d.get('subject'),'declaration.subject')
        require(d['subject']not in subjects,'ambiguous duplicate declaration subject/version selection');subjects.add(d['subject']);formal=_version(d,cutoff,'declaration:'+d['subject'])and formal
    evidence=c.get('evidence',{});assumptions=c.get('research_assumptions',{})
    require(type(evidence)is dict and type(assumptions)is dict,'evidence and research_assumptions must be mappings');require(set(evidence)<=set(REQUIRED_EVIDENCE),'unknown formal evidence category')
    missing=[]
    for category in REQUIRED_EVIDENCE:
        real=_version(evidence[category],cutoff,'evidence:'+category)if category in evidence else False
        if category=='declaration_versions':real=real and formal
        if not real:
            missing.append(category)
            if c['mode']=='research':_text(assumptions.get(category),'explicit research assumption for '+category)
    if c['mode']=='formal_strict':
        require(not missing,'formal_strict missing authenticated formal inputs: '+','.join(missing))
        raise ValueError('formal_strict unsupported: continuous48h research objective is not official DA98 equivalence')
    grid=[dict(index=i,start=(day+i*FIVE).isoformat(),end=(day+(i+1)*FIVE).isoformat(),duration_h=1/12,operating_date=(day+i*FIVE).date().isoformat())for i in range(physical_count)]
    reps=c.get('representatives');require(type(reps)is list,'explicit representative inventory required');market=[]
    if diagnostic:require(not reps,'diagnostic cannot pretend DA98 representatives')
    else:
        require(len(reps)==2,'DA98 requires two D+1 representatives')
        for q in range(96):market.append(dict(index=q,start=(day+timedelta(minutes=15*q)).isoformat(),physical_indices=[3*q,3*q+1,3*q+2],settle=True,role='delivery',projection='arithmetic_mean_of_three_5min_powers'))
        prior=day+timedelta(days=1)-FIVE
        for n,r in enumerate(reps):
            require(type(r)is dict,'representative record required');start=_time(r.get('start'),'representative.start');i=r.get('physical_index')
            require(type(i)is int and 288<=i<=physical_count-3 and i%3==0,'representative requires a complete D+1 quarter at actual physical index')
            require(start==day+i*FIVE,'representative time/index mismatch; no sparse adjacency');require(start>prior and start<day+timedelta(days=2),'representatives require strict chronological D+1 order');prior=start
            market.append(dict(index=96+n,start=start.isoformat(),physical_indices=[i,i+1,i+2],settle=False,role='representative',projection='arithmetic_mean_of_three_5min_powers'))
    return dict(schema=SCHEMA+'.validated',mode=c['mode'],diagnostic=diagnostic,day_start=day.isoformat(),cutoff=cutoff.isoformat(),publication_at=pub.isoformat(),horizon_end=(day+physical_count*FIVE).isoformat(),physical_count=physical_count,grid=grid,market_grid=market,market_count=len(market),formal_missing=missing,economic_policy=ECONOMIC_POLICY,objective_duration_h=[1/12]*physical_count,event_fee_policy='every_chosen_event_once_no_duration_multiplier',DA98_is_projection_only=not diagnostic,official_DA98_objective_equivalence=False,official_authenticity_verified=False,formal_ready=False,declaration_subjects=sorted(subjects),schedule_override=changed)
