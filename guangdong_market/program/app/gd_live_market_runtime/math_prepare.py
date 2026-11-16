"""Typed new event branch PRICE, reuse fixed v6 math without fake old contexts."""
from pathlib import Path
import sys,json,subprocess,hashlib,math
B=Path(__file__).resolve().parent;R=B.parent.parent;ENGINE=R/'engines/guangdong_short_unified_v6'
sys.path.insert(0,str(ENGINE/'price'))
from loader import read,write,verify,sha,need,loads
sys.path.insert(0,str(B))
sys.path.insert(0,str(B.parent))
from gd_provincial15_model.adapter import convert_full_case
POLICY='same_period_RT_SCED_reference_user_selected'

def verify_selected_da(da):
 manifest=Path(da)/'MANIFEST.json'
 if manifest.exists():
  from gd_provincial_da_contract import verify
  return verify(manifest,require_price=True)
 from source_contracts import selected_da
 return selected_da(da)

def prepare(source,expected_sha,out,selected_profile=None):
 from information_clock import validate as validate_information_clock
 from branch_parameters import _write_parameters,resolved
 from pricing_delta import from_source as delta_from_source
 from source_contracts import selected_da
 from domain_event import prepare as domain
 from state_adapter import derive,reference_and_ramp,PROFILE
 from wiring import apply_slacks
 from mapping import build
 from thermal_reference import apply_to_serialized_model,RT_POLICY
 from functools import partial
 from ramp_anchor import preceding_anchor,apply_first_ramp,POLICY as RP
 out=Path(out).resolve();need(not out.exists(),'fresh PRICE output; never overwrite');out.mkdir(parents=True)
 sf=Path(source).resolve();need(sha(sf)==expected_sha,'internal live snapshot transport changed');z=read(sf);verify(z['source_pins']);delta_selection=delta_from_source(z);information_clock=validate_information_clock(z,z.get('pricing_information_clock_declaration'));w=z['window'];base=Path(z['base_run']);da=Path(z['selected_DA']);verify_selected_da(da);r=z['request'];af=out/'effective_agc.json';write(af,z['effective_agc']);r['effective_agc_path']=str(af);r['sources']['AGC']={**r['sources']['AGC'],'path':str(af),'sha256':sha(af),'available_at':r['cutoff']};r['typed_source_path']=str(sf.resolve());x=z['values'];a=z['accepted'];casefile=base/'seed/case.json'
 from gd_case_snapshot import CaseSnapshot
 case_snapshot=CaseSnapshot(casefile,r['case_sha256'],z['source_pins']);case=case_snapshot.clone(casefile);params=out/'PARAMETERS.json'
 from forecast_binding import for_price
 case,forecast_proof=for_price(case,base/'seed',r,pins=z['source_pins'],prepared=z['reports'][-1]['prepared'],_case_snapshot=case_snapshot)
 from zero_vre_reference import canonicalize
 x,zero_vre_proof=canonicalize(z,case)
 n=min(8,96-r['delivery_tick']);state=derive(case,x,r['delivery_tick'],quarters=n);need(selected_profile in (None,PROFILE),'unsupported selected PRICE profile');need(not state['affected_units']or selected_profile==PROFILE,'event/off PRICE state requires independent explicit research profile selection');policy=PROFILE if state['affected_units']else POLICY
 # Reuse only the case already validated above against accepted SCED inputs.
 # Independent domain reconstruction and final verify(pins) remain unchanged.
 _write_parameters(case,da,base,w,params,'typed_event_branch_H0_'+str(w),'current_sced_research',verified_request=r)
 ps=read(params);ps['delta']=delta_selection['value'];ps['pricing_delta_selection']=delta_selection;ps['eligibility']=state['eligibility'];ps['selected_event_profile']=selected_profile;ps['derived_thermal_state']=state;params.write_text(json.dumps(ps));explicit,contract,pars,boundary=resolved(params,case,da,base,w,r)
 need(explicit['eligibility']==state['eligibility'],'derived eligibility contract mismatch')
 pair=da/read(da/'ARTIFACTS.json')['stages']['da_sced']['acceptance']['path']if w==-1 else sf
 r['accepted_prepared_dir']=z['reports'][-1]['prepared']
 domain(pair,casefile,Path(r['effective_agc_path']),da,out/'domain',research_current_sced=True,boundary=boundary,verified_inputs=(a,r,x),explicit=explicit,thermal_profile=state,selected_profile=selected_profile,accepted_source_pins=z['source_pins'],_case_snapshot=case_snapshot)
 model=read(out/'domain/template.json');names=read(out/'domain/names.json');ctx=read(out/'domain/CONTEXT.json');model,names,trace=apply_slacks(model,names,build(model,names),explicit,pars,r['cutoff'])
 art=read(da/'ARTIFACTS.json');phfile=da/art['stages']['da_sced']['physical_solution']['path'];ph=read(phfile)['physical_cells']
 # Accepted historical DA physical archive uses five-minute cells. Its hourly
 # energy is preserved when adapting to the provincial fifteen-minute grid.
 from provincial_da_view import aggregate_physical
 ph=ph if art.get('grid_minutes',5)==15 else aggregate_physical(ph)
 if z.get('reference_revision') is not None:
  ref=z['reference_revision'];from reference_clock import check as reference_clock_check
  ctx['post_reference_pricing_availability']=reference_clock_check(z,ref)
  need(w==94 and ref['interval']==[288,335]and ref['D0_unchanged']is True,'explicit q95-only reference interval');need(str(phfile)==ref['original_DA_physical_path']and sha(phfile)==ref['original_DA_physical_sha256'],'reference original DA changed');need(sha(ref['path'])==ref['sha256'],'reference overlay drift');verify(ref['source_pins']);revised=read(ref['path'])['physical_cells'];need(revised[:288]==ph[:288]and revised[336:]==ph[336:],'overlay outside D1 interval changed');ph=revised
 ag=read(r['effective_agc_path']);first=r['delivery_tick'];ps=read(params);specs={};ramps={};avg=lambda a:math.fsum(a)/len(a)
 for g,d in case['energy_inputs']['thermal'].items():
  for j in range(n):
   ts=range(first+j,first+j+1)
   specs[f'P[{g},{j}]']=dict(status=ps['eligibility'][g][j],current_sced=avg([x[f'power[{g},{t}]']for t in ts]),da_sced=avg([ph[t]['thermal'][g]['mean_mw']for t in ts]),current_min=avg([d['min_mw'][t]+ag[g]['down_mw'][str(t)]for t in ts]),current_max=avg([d['max_mw'][t]-ag[g]['up_mw'][str(t)]for t in ts]),da_min=avg([d['min_mw'][t]+ph[t]['thermal'][g]['agc_down_reserved_mw']for t in ts]),da_max=avg([d['max_mw'][t]-ph[t]['thermal'][g]['agc_up_reserved_mw']for t in ts]),delta=ps['delta'])
  ts=range(first,first+1);ramps[f'P[{g},0]']=dict(DA_anchor_MW=preceding_anchor([cell['thermal'][g]['mean_mw']for cell in ph],first,d['initial_power_mw']),up_budget_MW=min(d['ramp_up_mw_per_h'][t]for t in ts)*.25,down_budget_MW=min(d['ramp_down_mw_per_h'][t]for t in ts)*.25)
 from realtime_reference import preserve_current_ramps,assert_reference_alignment
 assert_reference_alignment(z,specs,state)
 model,thermal,ramp=reference_and_ramp(model,names,specs,ramps,state,partial(apply_to_serialized_model,policy=RT_POLICY),preserve_current_ramps,ramp_policy='current_SCED_incoming_state')
 ctx.update(short_run=z['branch'],base_run=z['base_run'],selected_DA=str(da),run_id=z['branch_id'],price_producer='gd.live_host.PRICE.v4_event'if state['affected_units']else'gd.live_host.PRICE.v3',selected_event_profile=selected_profile,derived_thermal_state=state,typed_source=str(sf),typed_source_sha256=sha(sf),seed_price_from_selected_DA=(w==-1),boundary_mode=policy,physical_release=False,safety_release=False,formal_bill=None,thermal_reference_extension=dict(policy=policy,thermal_specs=specs,trace=thermal,first_ramp_trace=ramp,official_first_anchor_certified=False,storage_current=True,delta_and_storage_remain_research=True),ramp_anchor_policy='current_SCED_incoming_state')
 ctx['forecast_binding']=forecast_proof
 ctx['source_pins'].update(forecast_proof['source_pins'])
 ctx['pricing_information_clock']=information_clock
 ctx['pricing_delta_selection']=delta_selection
 ctx['reference_selection']='user_selected_RT_SCED_not_literal_DA_wording_in_rule_3_4_4_5'
 ctx['research_boundary']='thermal same-period RT-SCED band using selected research delta='+format(delta_selection['value'],'.17g')+'; storage/VRE reference and mode mapping remain research; no dispatch recomputation or official parameter certification'
 ctx['zero_vre_reference_normalization']=zero_vre_proof;ctx['AGC_forecast_semantics']=z.get('AGC_forecast_semantics');ctx['D1_reference_revision']=z.get('reference_revision');pins=dict(z['source_pins']);pins.update(case_snapshot.source_pins(casefile));pins[str(Path(__file__).resolve())]=sha(__file__);import zero_vre_reference;pins[str(Path(zero_vre_reference.__file__).resolve())]=sha(zero_vre_reference.__file__);pins.update({str(ENGINE/n):h for n,h in read(ENGINE/'FROZEN_MANIFEST.json')['files'].items()});pins.update({str(p):sha(p)for p in [sf,af,params,phfile,*B.glob('*.py'),ENGINE/'FROZEN_MANIFEST.json']});ctx['source_pins'].update(pins)
 dest=out/'prepared';dest.mkdir()
 for n,v in [('template.json',model),('names.json',names),('CONTEXT.json',ctx),('CONTRACT.json',contract),('SOURCE_REVIEW.json',z),('TRACE.json',trace)]:write(dest/n,v)
 case_snapshot.verify(casefile);verify(pins);write(dest/'READY.json',dict(columns=len(model['variables']),rows=len(model['rows']),artifact_pins={f.name:sha(f)for f in dest.iterdir()},source_pins=pins,native_calls=0,formal=False))
 result=dict(pricing_delta=delta_selection['value'],pricing_delta_official_numeric_verified=False,status='prepared',prepared=str(dest),window=w,day_quarter=first,policy=policy,branch_id=z['branch_id'],columns=len(model['variables']),rows=len(model['rows']),native_calls=0,proposed_PRICE_calls=1,seconds=model['options']['time_limit'],time_limit_scope='each_native_solve',model_class='LP',mip_gap=None,mip_gap_display='N/A (LP)',source_SCED_sha256=a['SCED_response_sha256'],formal=False)
 write(out/'RESULT.json',result);return result
if __name__=='__main__':print(json.dumps(prepare(sys.argv[1],sys.argv[2],sys.argv[3],None if len(sys.argv)<5 or sys.argv[4]=='NONE'else sys.argv[4])))
