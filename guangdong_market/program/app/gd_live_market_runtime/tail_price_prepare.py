"""Typed new event branch PRICE, reuse fixed v6 math without fake old contexts."""
from pathlib import Path
import sys,json,subprocess,hashlib,math
B=Path(__file__).resolve().parent;R=B.parent.parent;ENGINE=R/'engines/guangdong_short_unified_v6'
sys.path.insert(0,str(ENGINE/'price'))
from loader import read,write,verify,sha,need,loads
sys.path.insert(0,str(B))
POLICY='thermal_DA_DA_previous_quarter_anchor_research'

def prepare(source,expected_sha,out,selected_profile=None):
 from tail_parameters import sample,resolved
 from source_contracts import selected_da
 from domain_event import prepare as domain
 from state_adapter import derive,reference_and_ramp,PROFILE
 from wiring import apply_slacks
 from mapping import build
 from thermal_reference import apply_to_serialized_model
 from ramp_anchor import preceding_anchor,apply_first_ramp,POLICY as RP
 out=Path(out).resolve();need(not out.exists(),'fresh PRICE output; never overwrite');out.mkdir(parents=True)
 sf=Path(source).resolve();need(sha(sf)==expected_sha,'internal live snapshot transport changed');z=read(sf);verify(z['source_pins']);w=z['window'];base=Path(z['base_run']);da=Path(z['selected_DA']);selected_da(da);r=z['request'];af=out/'effective_agc.json';write(af,z['effective_agc']);r['effective_agc_path']=str(af);r['sources']['AGC']={**r['sources']['AGC'],'path':str(af),'sha256':sha(af),'available_at':r['cutoff']};r['typed_source_path']=str(sf.resolve());x=z['values'];a=z['accepted'];casefile=base/'seed/case.json';case=loads(casefile.read_bytes());params=out/'PARAMETERS.json'
 state=derive(case,x,r['delivery_tick']);need(selected_profile in (None,PROFILE),'unsupported selected PRICE profile');need(not state['affected_units']or selected_profile==PROFILE,'event/off PRICE state requires independent explicit research profile selection');policy=PROFILE if state['affected_units']else POLICY
 sample(da,base,w,params,'selected_DA_tail_lookahead_'+str(w),'current_sced_research',verified_request=r)
 ps=read(params);ps['eligibility']=state['eligibility'];ps['selected_event_profile']=selected_profile;ps['derived_thermal_state']=state;params.write_text(json.dumps(ps));explicit,contract,pars,boundary=resolved(params,case,da,base,w,r)
 need(explicit['eligibility']==state['eligibility'],'derived eligibility contract mismatch')
 pair=da/read(da/'ARTIFACTS.json')['stages']['da_sced']['acceptance']['path']if w==-1 else sf
 domain(pair,casefile,Path(r['effective_agc_path']),da,out/'domain',research_current_sced=True,boundary=boundary,verified_inputs=(a,r,x),explicit=explicit,thermal_profile=state,selected_profile=selected_profile)
 model=read(out/'domain/template.json');names=read(out/'domain/names.json');ctx=read(out/'domain/CONTEXT.json');model,names,trace=apply_slacks(model,names,build(model,names),explicit,pars,r['cutoff'])
 art=read(da/'ARTIFACTS.json');phfile=da/art['stages']['da_sced']['physical_solution']['path'];ph=read(phfile)['physical_cells'];ag=read(r['effective_agc_path']);first=r['delivery_tick'];ps=read(params);specs={};ramps={};avg=lambda a:math.fsum(a)/3
 for g,d in case['energy_inputs']['thermal'].items():
  for j in range(8):
   ts=range(first+3*j,first+3*j+3)
   specs[f'P[{g},{j}]']=dict(status=ps['eligibility'][g][j],current_sced=avg([x[f'power[{g},{t}]']for t in ts]),da_sced=avg([ph[t]['thermal'][g]['mean_mw']for t in ts]),current_min=avg([d['min_mw'][t]+ag[g]['down_mw'][str(t)]for t in ts]),current_max=avg([d['max_mw'][t]-ag[g]['up_mw'][str(t)]for t in ts]),da_min=avg([d['min_mw'][t]+ph[t]['thermal'][g]['agc_down_reserved_mw']for t in ts]),da_max=avg([d['max_mw'][t]-ph[t]['thermal'][g]['agc_up_reserved_mw']for t in ts]),delta=ps['delta'])
  ts=range(first,first+3);ramps[f'P[{g},0]']=dict(DA_anchor_MW=preceding_anchor([cell['thermal'][g]['mean_mw']for cell in ph],first,d['initial_power_mw']),up_budget_MW=min(d['ramp_up_mw_per_h'][t]for t in ts)*.25,down_budget_MW=min(d['ramp_down_mw_per_h'][t]for t in ts)*.25)
 model,thermal,ramp=reference_and_ramp(model,names,specs,ramps,state,apply_to_serialized_model,apply_first_ramp,ramp_policy=RP)
 ctx.update(short_run=z['branch'],base_run=z['base_run'],selected_DA=str(da),run_id=z['branch_id'],price_producer='gd.selected_DA_tail.full_profile.PRICE.research.v1',source_kind='independent_selected_DA_tail_research_RT_lookahead_NOT_live50chain',not_executed_price=True,selected_event_profile=selected_profile,derived_thermal_state=state,typed_source=str(sf),typed_source_sha256=sha(sf),seed_price_from_selected_DA=(w==-1),boundary_mode=policy,physical_release=False,safety_release=False,formal_bill=None,thermal_reference_extension=dict(policy=policy,thermal_specs=specs,trace=thermal,first_ramp_trace=ramp,official_first_anchor_certified=False,storage_current=True,delta_and_storage_remain_research=True),ramp_anchor_policy='DA_previous_complete_quarter_RESEARCH')
 pins=dict(z['source_pins']);pins.update({str(ENGINE/n):h for n,h in read(ENGINE/'FROZEN_MANIFEST.json')['files'].items()});pins.update({str(p):sha(p)for p in [sf,af,params,phfile,*B.glob('*.py'),ENGINE/'FROZEN_MANIFEST.json']});ctx['source_pins'].update(pins)
 dest=out/'prepared';dest.mkdir()
 for n,v in [('template.json',model),('names.json',names),('CONTEXT.json',ctx),('CONTRACT.json',contract),('SOURCE_REVIEW.json',z),('TRACE.json',trace)]:write(dest/n,v)
 verify(pins);write(dest/'READY.json',dict(columns=len(model['variables']),rows=len(model['rows']),artifact_pins={f.name:sha(f)for f in dest.iterdir()},source_pins=pins,native_calls=0,formal=False))
 result=dict(status='prepared',prepared=str(dest),window=w,day_quarter=first//3,policy=policy,branch_id=z['branch_id'],columns=len(model['variables']),rows=len(model['rows']),native_calls=0,proposed_PRICE_calls=1,seconds=60,source_SCED_sha256=a['SCED_response_sha256'],formal=False)
 write(out/'RESULT.json',result);return result
if __name__=='__main__':print(json.dumps(prepare(sys.argv[1],sys.argv[2],sys.argv[3],None if len(sys.argv)<5 or sys.argv[4]=='NONE'else sys.argv[4])))
