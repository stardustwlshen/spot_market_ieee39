"""Read-only DA98 mapping/calendar and research publication candidate. Zero solves."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import argparse,hashlib,importlib.util,json,math,sys
TZ=timezone(timedelta(hours=8))
DEFAULTS={'renewable_forecast':(10,0),'boundary_preparation':(11,30),'prior_information':(11,45),'bids':(12,30),'publication':(20,30)}
RULES={'renewable_forecast':'2.4.2.3 print34/PDF37','boundary_preparation':'2.4.1 print32/PDF35','prior_information':'2.4.1 print32/PDF35','bids':'2.4.1–2.4.2 print32–33/PDF35–36;2.4.4 print36/PDF39','publication':'2.7.2.1 print86/PDF89'}
GAPS=['representative_time_weight_state_linkage_not_officially_resolved','storage_terminal_and_printed_band_interpretation_research','hard_5min_lift_not_official_98_equivalence','delta_and_resource_eligibility_require_effective_business_sources','actual_declaration_default_permission_records_not_evidenced','official_price_limits_financial_participation_and_settlement_not_certified']
def need(ok,msg):
 if not ok:raise ValueError(msg)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canonical(z):return json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def read(p):return json.loads(Path(p).read_text())
def stamp(s):
 x=datetime.fromisoformat(s);need(x.utcoffset()==timedelta(hours=8),'explicit +08:00 market timestamp required');return x

def calendar_contract(day_start,*,events=None,exceptions=None):
 """Events are actual recorded timestamps, never inferred from model clock."""
 need(events is None or type(events)is dict,'events must be an explicit mapping');need(exceptions is None or type(exceptions)is dict,'exceptions must be an explicit mapping');day=stamp(day_start);need(not any((day.hour,day.minute,day.second,day.microsecond)),'delivery-day midnight required');events=events or {};exceptions=exceptions or {};need(set(events)<=set(DEFAULTS),'unknown event');need(set(exceptions)<={'bids','publication'},'only text-supported bid cutoff/publication exceptions allowed');out={}
 for key,(h,m)in DEFAULTS.items():
  default=(day-timedelta(days=1)).replace(hour=h,minute=m);deadline=default;notice=exceptions.get(key)
  if key in exceptions:
   required={'deadline','source','source_sha256','issued_at','authority_role','reason'};need(set(notice)==required,'complete explicit exception evidence required');need(notice['source']and len(notice['source_sha256'])==64 and all(c in '0123456789abcdef'for c in notice['source_sha256']),'source and SHA256 required')
   deadline=stamp(notice['deadline']);issued=stamp(notice['issued_at']);need(issued<=deadline<day,'exception chronology invalid');need(notice['authority_role']==('trading_center'if key=='bids'else'dispatch_operator'),'wrong exception authority role')
   need(notice['reason'] in (('market_abnormality_affecting_declaration',)if key=='bids'else('supply_demand_balance_difficulty','section_control_difficulty')),'unsupported exception reason');need(key!='publication'or deadline>=default,'publication postponement cannot advance default')
  actual=stamp(events[key])if key in events else None
  if actual is not None:need(actual<day,'actual DA event after delivery begins');need(not notice or stamp(notice['issued_at'])<=actual,'exception not known at actual event')
  out[key]={'default_deadline':default.isoformat(),'effective_deadline':deadline.isoformat(),'rule':RULES[key],'exception':notice,'exception_source_verification':'caller_declared_reference_not_external_authority_certification'if notice else None,'actual_event_at':actual.isoformat()if actual else None,'status':'missing_actual_event'if actual is None else'on_time'if actual<=deadline else'late_without_effective_exception'}
 need(stamp(out['prior_information']['effective_deadline'])<=stamp(out['bids']['effective_deadline'])<=stamp(out['publication']['effective_deadline']),'effective market deadlines out of order')
 for left,right in [('boundary_preparation','prior_information'),('prior_information','bids'),('bids','publication')]:
  if left in events and right in events:need(stamp(events[left])<=stamp(events[right]),'actual event sequence reversed')
 return {'timezone':'Asia/Shanghai','day_start':day.isoformat(),'events':out,'missing_actual_events':[k for k,v in out.items()if v['actual_event_at']is None],'formal_ready':False,'scope':'Default official calendar, no invented declaration or publication event'}

def load_contract_module(path):
 """Load only the existing standard-library DA98 contract, not model/solver."""
 path=Path(path).resolve();spec=importlib.util.spec_from_file_location('_existing_da98_contract',path);mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod);return mod

def validate_mapping(grid,dto,contract_module,*,formal=False):
 need(not formal,'formal DA98 unsupported: '+', '.join(GAPS));contract_module.validate_lift_contract_binding(dto);need(len(grid)==98,'98 market points required')
 for i,(a,b)in enumerate(zip(grid,dto['grid'])):
  need(a['physical_indices']==dto['physical_lift']['market_to_physical'][i],f'grid/PRICE mapping differs at {i}')
  for k in ('kind','start','end','actual_time','power_semantics','objective_weight_h','physical_duration_h','ramp_elapsed_h','predecessor','settlement'):need(a[k]==b[k],f'grid/PRICE {k} differs at {i}')
 return {'validated':True,'existing_contract_sha256':dto['contract_sha256'],'market_points':98,'delivery_points':96,'representatives':dto['grid'][96:],'physical_cells':len(dto['physical_lift']['cells']),'official_equivalence':False,'unsupported':GAPS.copy()}

def checked_ref(run,record):
 path=Path(record['path']);need(not path.is_absolute()and '..'not in path.parts,'run artifact path escape');target=(run/path).resolve();need(target.is_relative_to(run),'run artifact symlink escape');need(sha(target)==record['sha256'],'accepted artifact changed: '+str(path));return target

def export_candidate(run,contract_module,*,calendar_events=None,calendar_exceptions=None,formal=False):
 """Use already accepted same-run artifacts; no acceptance or model rerun."""
 run=Path(run).resolve();need(not formal,'formal publication unsupported; research candidate only');arts=read(run/'ARTIFACTS.json');need(arts['status']=='accepted'and arts['formal']is False,'accepted research DA artifacts required');accepted={};pins={'ARTIFACTS.json':sha(run/'ARTIFACTS.json')}
 for stage in ('da_scuc','da_sced','da_price'):
  rec=read(checked_ref(run,arts['stages'][stage]['acceptance']));need(rec['stage']==stage and rec['status']=='accepted'and rec['formal']is False,'stage acceptance required')
  for k in ('run_id','input_identity','source_identity'):need(rec[k]==arts[k],'stage source/run mismatch')
  for item in rec['artifacts'].values():checked_ref(run,item)
  accepted[stage]=rec;pins[stage+'/ACCEPTANCE.json']=sha(run/stage/'ACCEPTANCE.json')
 for item in arts['inputs'].values():checked_ref(run,item)
 bridge=read(checked_ref(run,accepted['da_price']['artifacts']['base_bridge']));dto=bridge['DTO'];need(hashlib.sha256(canonical(dto)).hexdigest()==bridge['DTO_sha256'],'bridge DTO hash mismatch');wrapper=read(checked_ref(run,accepted['da_price']['artifacts']['input']));need(wrapper['base_DTO']==dto,'PRICE wrapper/bridge differs');need(bridge['SCUC_accepted']and not bridge['diagnostic_only'],'accepted SCUC source required');mapping=validate_mapping(read(run/'inputs/grid.json'),dto,contract_module)
 need(accepted['da_price']['audit']['KKT']['passed']is True,'accepted independent PRICE KKT required');prices=read(checked_ref(run,accepted['da_price']['artifacts']['raw_prices']))['raw_unclipped_LMP'];need(set(prices)==set(dto['nodes'])and all(len(z)==98 and all(math.isfinite(v)for v in z)for z in prices.values()),'all98 independent node prices required')
 receipt=bridge['source_receipt']
 for stage,prefix in [('da_scuc','SCUC'),('da_sced','SCED')]:
  for kind in('request','response'):need(receipt[prefix+'_'+kind+'_sha256']==accepted[stage]['artifacts'][kind]['sha256'],'SCED/SCUC receipt mismatch')
 need(dto['sced_snapshot']['source_kind']=='native_highs_sced','native SCED quantity source required');physical=read(checked_ref(run,accepted['da_sced']['artifacts']['physical_solution']))['physical_cells'];need(len(physical)==576,'576 original SCED cells required');quarters=[]
 for i,g in enumerate(dto['grid'][:96]):
  ids=dto['physical_lift']['market_to_physical'][i];quantities={}
  for uid,u in dto['resources'].items():
   vals=[physical[j]['thermal'][uid]['mean_mw']if u['kind']=='generator'else physical[j]['battery'][uid]['discharge_mw']-physical[j]['battery'][uid]['charge_mw']for j in ids];power=math.fsum(vals)/3;need(abs(power-u['rows'][i]['sced_mw'])<=1e-6,'SCED reference power mismatch');quantities[uid]=math.fsum(vals)/12
  vre={node:math.fsum(physical[j]['renewable'][node]['dispatched_mw']for j in ids)/12 for node in physical[ids[0]]['renewable']}
  quarters.append({'index':i,'start':g['start'],'end':g['end'],'SCED_net_energy_MWh':quantities,'SCED_thermal_phase_5min':{r:[physical[j]['thermal'][r]['phase']for j in ids]for r,u in dto['resources'].items()if u['kind']=='generator'},'SCED_VRE_bus_aggregate_MWh':vre,'raw_independent_node_price_yuan_MWh':{n:prices[n][i]for n in dto['nodes']}})
 hours=[]
 for h in range(24):
  qs=quarters[4*h:4*h+4];hours.append({'hour':h,'start':qs[0]['start'],'end':qs[-1]['end'],'SCED_net_energy_MWh':{r:math.fsum(q['SCED_net_energy_MWh'][r]for q in qs)for r in dto['resources']},'raw_independent_node_price_arithmetic_mean_yuan_MWh':{n:math.fsum(q['raw_independent_node_price_yuan_MWh'][n]for q in qs)/4 for n in dto['nodes']}})
 calendar=calendar_contract(dto['grid'][0]['start'],events=calendar_events,exceptions=calendar_exceptions)
 for rel,h in pins.items():need(sha(run/rel)==h,'accepted run mutated during candidate export')
 return {'schema':'gd.da98.readonly_candidate.v1','rule_source_index_sha256':sha(Path(__file__).with_name('SOURCES.json')),'run_id':arts['run_id'],'source_run':str(run),'source_pins':pins,'generated_at_utc':datetime.now(timezone.utc).isoformat(),'logical_simulated_publication_at':dto['planned_publication_at'],'calendar':calendar,'mapping':mapping,'quarters':quarters,'hours':hours,'quantity_source':'same-run accepted SCED physical, not PRICE variables','price_source':'same-run accepted independent DA PRICE, raw/unclipped','representatives_publishable':False,'commitment_source':'same-run da_sced/physical_solution.json thermal phases; not new decisions','user_side_awarded_energy':None,'financial_DA_participation':'unknown','publication_allowed':False,'official_market_price':None,'formal_bill':None,'execution_authority':False,'unsupported':GAPS.copy(),'clock_warning':'Logical simulated publication is not evidence this actual computation completed by the official deadline','native_calls':0,'integrated_into_current_UI':False}

def main():
 p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--contract-module',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--calendar-evidence',type=Path);a=p.parse_args();need(not a.output.exists(),'candidate must use fresh output, never overwrite accepted artifacts');need(not a.output.resolve().is_relative_to(a.run.resolve()),'write candidate outside accepted run');e=read(a.calendar_evidence)if a.calendar_evidence else{};r=export_candidate(a.run,load_contract_module(a.contract_module),calendar_events=e.get('events'),calendar_exceptions=e.get('exceptions'));a.output.write_bytes(canonical(r));print(json.dumps({'run':r['run_id'],'quarters':96,'hours':24,'native_calls':0,'official':False,'missing_calendar_events':r['calendar']['missing_actual_events']}))
if __name__=='__main__':main()
