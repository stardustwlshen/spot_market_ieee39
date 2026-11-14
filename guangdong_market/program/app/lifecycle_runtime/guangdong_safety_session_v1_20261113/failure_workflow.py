"""Conservative, pure research AGC failure workflow. No solver, allocation or dispatch."""
from copy import deepcopy
import hashlib,json,math
ARTICLE59_POLICY={'trigger':'post-safety capacity shortage OR actual operating security need; not inferred from aggregate counterfactual','precondition':'all safety constraints satisfied','priority':['market nonawarded: sorting price ascending','nonparticipants afterwards: comprehensive performance descending'],'record':'post-event information record required','automatic_call_supported':False}
CHECKS=('agc_normal','capacity_balance','network_sections_ac','hydro_vibration','storage_soc','no_increase_curtailment')

def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def need(x,m):
 if not x:raise ValueError(m)
def number(x):return type(x)in(int,float)and math.isfinite(x)and x>=0
def pin(x):return isinstance(x,str)and len(x)==64 and all(c in '0123456789abcdef'for c in x)
def seal(s):
 s.pop('state_sha256',None);s['state_sha256']=digest(s);return s
def verify(s,expected):
 need(s['state_sha256']==expected==digest({k:v for k,v in s.items()if k!='state_sha256'}),'stale or changed workflow state')
 need(s['original_awards_sha256']==digest(s['original_awards']),'original award evidence changed')
 need(s['publication_allowed']is False and s['dispatch_authorized']is False,'workflow cannot grant publication/dispatch')
def transition(s,expected,event,status,details):
 verify(s,expected);n=deepcopy(s);n['revision']+=1;n['status']=status;n['events'].append({'event':event,'previous_state_sha256':expected,'details':deepcopy(details)});n['missing_inputs']=[];return n

def open_case(verified_failure,original_awards,*,branch_id,case_sha256,failure_source_sha256):
 need(verified_failure['research_no_increase_check']=='fail'and verified_failure['safety_release']is False,'confirmed research aggregate failure required')
 need(pin(case_sha256)and pin(failure_source_sha256)and branch_id,'source identities required')
 a=deepcopy(original_awards);need(number(a['target_mw'])and a['target_mw']>0,'positive original requirement')
 need(a['reserve_requirements']=={'positive_mw':400,'accident_mw':400},'original400 unchanged')
 need(a['awards_mw']and all(number(v)for v in a['awards_mw'].values()),'explicit original awards')
 need(pin(a['source_sha256']),'award source required')
 return seal({'schema':'gd.agc.failure_workflow.research.v1','status':'BLOCKED_SYSTEM_SAFETY','revision':0,'branch_id':branch_id,'hour_start':verified_failure['target_hour'],'case_sha256':case_sha256,'original_awards':a,'original_awards_sha256':digest(a),'failure':deepcopy(verified_failure),'failure_source_sha256':failure_source_sha256,'candidate':None,'events':[{'event':'aggregate_failure_received','details':'No unit attribution; original awards retained as historical evidence, not safety-approved dispatch.'}],'missing_inputs':['system redispatch/reclearing proposal with same requirement and constraints','source-bound unit-specific evidence before removing any awarded unit','actual AGC state, safety sections/AC, storage and no-increase checks','if article59 route requested: sourced trigger, eligible nonawarded inventory and safety evidence'],'article59_policy':deepcopy(ARTICLE59_POLICY),'unit_removals':[],'publication_allowed':False,'dispatch_authorized':False,'qualification_created':False,'formal':False,'native_calls':0})

def propose(s,p,*,expected_state_sha256):
 n=transition(s,expected_state_sha256,'proposal_received','BLOCKED_MISSING_INPUTS',p);missing=[]
 kind=p.get('kind');need(kind in ('same_awards_system_redispatch','reclearing_proposal','article59_temporary_call_proposal'),'unknown route')
 orig=s['original_awards'];aw=p.get('awards_mw',{})
 if p.get('branch_id')!=s['branch_id']or p.get('hour_start')!=s['hour_start']:missing.append('proposal must bind same branch and clearing hour')
 if p.get('target_mw')!=orig['target_mw']:missing.append('original procurement requirement must not be reduced')
 if p.get('reserve_requirements')!=orig['reserve_requirements']:missing.append('400MW positive and accident requirements must remain unchanged')
 if p.get('case_sha256')!=s['case_sha256']:missing.append('original case/SOC/cycle/curve/bid identity required; no silent parameter changes')
 if not aw or not all(number(x)for x in aw.values())or sum(aw.values())+1e-8<orig['target_mw']:missing.append('full original procurement target not covered; AGC0 is not a replacement')
 new=set(aw)-set(orig['awards_mw'])
 if new:missing.append('new resource qualification/registry not supported by this bounded workflow: '+','.join(sorted(new)))
 if kind=='same_awards_system_redispatch':
  if aw!=orig['awards_mw']:missing.append('same-awards route cannot change allocation')
  if not pin(p.get('material_plan_change_sha256')):missing.append('sourced material control/schedule proposal required; do not repeat the identical accepted model')
 reduced=[g for g,v in orig['awards_mw'].items()if aw.get(g,0)<v-1e-8]
 findings=p.get('unit_specific_findings',{})
 for g in reduced:
  f=findings.get(g,{})
  if f.get('attribution')!='unit_specific'or not pin(f.get('source_sha256'))or not f.get('cause_id'):missing.append('unit-specific sourced cause required before reducing '+g)
 if kind!='same_awards_system_redispatch'and not pin(p.get('clearing_proposal_sha256')):missing.append('bound clearing proposal/source required; no automatic reallocation')
 if kind=='article59_temporary_call_proposal':
  t=p.get('article59_trigger',{})
  if t.get('kind')not in ('post_safety_capacity_shortfall','actual_operating_security_need')or not pin(t.get('source_sha256')):missing.append('article59 trigger not established by aggregate counterfactual alone')
  # This bounded model has no certified nonawarded/new provider inventory or dispatcher instruction channel.
  missing.append('article59 eligible nonawarded/nonparticipant inventory and dispatcher decision unavailable; no automatic call')
 n['candidate']={'proposal':deepcopy(p),'candidate_id':digest(p),'unit_reductions_proposed_not_executed':reduced};n['missing_inputs']=missing;n['status']='BLOCKED_MISSING_INPUTS'if missing else'RESEARCH_RECHECK_REQUIRED'
 n['next_actions']=['resolve listed inputs; preserve original awards']if missing else['prepare fresh same-case SCED and six safety checks; never mutate original accepted run']
 return seal(n)

def recheck(s,evidence,*,expected_state_sha256):
 need(s['status']=='RESEARCH_RECHECK_REQUIRED','candidate not ready for recheck')
 n=transition(s,expected_state_sha256,'research_recheck_received','BLOCKED_RECHECK',evidence);need(evidence.get('candidate_id')==s['candidate']['candidate_id'],'wrong candidate')
 need(evidence.get('branch_id')==s['branch_id']and evidence.get('hour_start')==s['hour_start']and evidence.get('case_sha256')==s['case_sha256'],'wrong branch/hour/case')
 need(pin(evidence.get('sced_acceptance_sha256')),'new SCED acceptance reference required')
 checks=evidence.get('checks',{});need(set(checks)==set(CHECKS),'six checks explicitly required')
 for k,x in checks.items():
  need(x.get('status')in ('pass','fail','unknown','not_applicable')and x.get('reason'),'typed check required')
  need(pin(x.get('source_sha256')),'bound safety evidence required')
  if x['status']=='not_applicable':need(k=='hydro_vibration'and evidence.get('no_hydro_resources')is True,'cannot bypass system storage/AC/AGC/no-increase')
 failed=[k for k,x in checks.items()if x['status']=='fail'];unknown=[k for k,x in checks.items()if x['status']=='unknown'];n['missing_inputs']=['resolve '+k for k in failed+unknown]
 n['status']='BLOCKED_RECHECK'if failed or unknown else'RESEARCH_CHECKS_RECORDED_NOT_PUBLISHED'
 n['next_actions']=['new proposal/recheck; never promote unknown to pass']if failed or unknown else['trusted host must verify linked raw evidence; no formal publication/dispatch channel in this module']
 n['source_authentication_performed_here']=False;n['physical_solver_checks_performed_here']=False
 return seal(n)

def view(s,*,expected_state_sha256):
 verify(s,expected_state_sha256)
 return {'status':s['status'],'hour_start':s['hour_start'],'original_awards_mw':s['original_awards']['awards_mw'],'award_basis':s['original_awards'].get('basis','explicit research input'),'pending_candidate':s['candidate'],'missing_inputs':s['missing_inputs'],'next_actions':s.get('next_actions',['propose a same-awards system redispatch for research recheck, or collect supported reclearing inputs']),'article59_policy':deepcopy(ARTICLE59_POLICY),'unit_removals':[],'publication_allowed':False,'dispatch_authorized':False,'count_as_safety_pass':False,'formal':False,'native_calls':0}

if __name__=='__main__':
 import argparse
 from pathlib import Path
 p=argparse.ArgumentParser();p.add_argument('action',choices=['open_case','propose','recheck','view']);p.add_argument('input');p.add_argument('output');a=p.parse_args();args=json.loads(Path(a.input).read_text());result=globals()[a.action](**args)
 with Path(a.output).open('x')as f:json.dump(result,f,indent=2,allow_nan=False)
