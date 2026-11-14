"""Explicit source-bound D+1 research assumption, preflight now; consume only when due."""
from pathlib import Path
from datetime import datetime,timedelta
import sys,json,re
from source import read,sha,e
B=Path(__file__).resolve().parent;sys.path.insert(0,str(B.parent/'lifecycle_runtime/guangdong_day_clock_candidate_v1_20261113'))
from nextday_declaration import declare,validate,SCENARIO

def create(session,name,available_at,*,confirmed=False):
 e.need(confirmed is True,'explicit named same-rules D+1 research declaration required');e.need(re.fullmatch(r'[A-Za-z0-9_-]{1,50}',name or ''),'declaration name letters/digits/_/-');session.verify_sources();out=session.run.parent/'nextday_agc_declarations'/name;e.need(not out.exists(),'immutable declaration, choose a new name');out.mkdir(parents=True)
 p=out/'D1_AGC.json';d=declare(p,scenario=SCENARIO,available_at=available_at);origin=read(session.run/'seed/request.json')['origin'];target=datetime.fromisoformat(origin)+timedelta(days=1)
 e.need({x['effective_from']for x in d['parameters'].values()}=={target.isoformat()},'declared D+1 date does not match selected source')
 da=Path(session.context['selected_DA']);z=dict(schema='gd.source_bound.nextday_AGC.selection.v1',scenario=SCENARIO,run=str(session.run),selected_DA=str(da),origin=origin,case_sha256=sha(session.run/'seed/case.json'),DA_artifacts_sha256=sha(da/'ARTIFACTS.json'),declaration=str(p),declaration_sha256=sha(p),available_at=available_at,explicit_hypothetical_availability=True,original_50_and_400_400_retained=True,source='same declared synthetic five-thermal qualifications and bids; NOT verified operating eligibility',formal=False,safety_release=False)
 f=out/'SELECTION.json'
 with f.open('x')as stream:json.dump(z,stream,indent=2)
 return dict(selection_path=str(f),selection=z,native_calls=0)

def preflight(session,selection,*,explicit=False,reference_revision=False,post_scuc_day=False):
 e.need(explicit is True and bool(selection),'96 requires an explicit valid source-bound D+1 declaration before any solve');session.verify_sources();f=Path(selection).resolve();z=read(f)
 e.need(z['schema']=='gd.source_bound.nextday_AGC.selection.v1'and z['scenario']==SCENARIO and z['explicit_hypothetical_availability']is True,'named research selection only')
 da=Path(session.context['selected_DA']);origin=read(session.run/'seed/request.json')['origin'];target=datetime.fromisoformat(origin)+timedelta(days=1);deadline=target-timedelta(minutes=30)
 e.need(z['run']==str(session.run)and z['selected_DA']==str(da)and z['origin']==origin,'declaration wrong run/DA/date')
 e.need(z['case_sha256']==sha(session.run/'seed/case.json')and z['DA_artifacts_sha256']==sha(da/'ARTIFACTS.json'),'declaration wrong case/source')
 p=Path(z['declaration']).resolve();e.need(sha(p)==z['declaration_sha256'],'declaration changed');d=validate(p,cutoff=z['available_at'])
 e.need(d['research_extension']['available_at']==z['available_at']and datetime.fromisoformat(z['available_at'])<=deadline,'D+1 unavailable by H24 deadline')
 e.need(all(datetime.fromisoformat(x['effective_from'])==target and datetime.fromisoformat(x['effective_to'])>=target+timedelta(hours=1)for x in d['parameters'].values()),'wrong-date or expired nextday parameters')
 case=session.program.engine.loads((session.run/'seed/case.json').read_bytes())
 e.need({u['resource_id']for u in d['units']}==set(case['energy_inputs']['thermal']),'nextday qualification inventory differs from selected case')
 e.need(all(set(case['reserve'][k].values())=={400}for k in ('positive_mw','contingency_mw')),'400/400 scope changed')
 from clock_adapter import declaration_check
 carry=read(session.run/'INITIAL_CARRY.json');warm=read(session.run/'seed/WARMSTART_SOURCE.json')
 dayproof=declaration_check(carry,datetime.fromisoformat(origin),sha(session.run/'seed/case.json'),warm['response_sha256'],deadline.isoformat())
 from crossday_price_guard import check as price_check
 try:price_compatibility=price_check(session,case,d)
 except ValueError as exc:
  if not(reference_revision or post_scuc_day)or '跨日价格参考不兼容'not in str(exc):raise
  price_compatibility=dict(status='known_original_conflict_explicit_reference_revision_attempt_only',reason=str(exc),reference_native_budget=1,not_guaranteed_feasible=True)
 pins={dayproof['path']:dayproof['sha256'],str(p):sha(p),str(f):sha(f),str(da/'ARTIFACTS.json'):sha(da/'ARTIFACTS.json'),str(session.run/'seed/case.json'):sha(session.run/'seed/case.json')}
 return dict(price_compatibility=price_compatibility,selection_path=str(f),declaration_path=str(p),scenario=SCENARIO,source_pins=pins,available_at=z['available_at'],actual_consumption='H24 post-SCUC+SCED accepted W94 available23:45; no early produce24'if post_scuc_day else'H24 only after W93 available23:30, before W94',new_research_assumption=z['source'],default_native_calls=0)
