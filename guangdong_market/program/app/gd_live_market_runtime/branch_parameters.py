from gd_provincial15_model.renewable_bids import window as bid_window, verify_window
from gd_provincial15_model.curtailment_policy import value as curtailment_value
from loader import *
from gd_provincial15_model.adapter import convert_full_case
from contracts import resolve
from wiring import validate_wiring
# Regional default, not authenticated active Guangdong parameter; explicit input overrides remain supported.
NETWORK_DEFAULT_CNY_PER_MWH = 1000.
NETWORK_DEFAULT_SOURCE = dict(document='南方区域电力现货市场实施细则（2026年V1.0版）',url='http://www.gzpec.cn/law/jygz/202611/P020261103633894465723.pdf',printed_page=180,pdf_page=183,item=18,pdf_sha256='978d7d7179e3629f8a86fb2d770470e1700b67c4bb867123b07a2d8f480c3caf',scope='regional_default_not_authenticated_Guangdong_override')
CHOICES=dict(storage_reference='selected_domain_reference',storage_mode='selected_reference_cell_modes',storage_SOC_start='selected_reference_prefix_SOC',storage_terminal='declared_terminal_if_in_horizon',storage_cycle_sign='positive_charge_internal_throughput',charge_discharge_sign='net_discharge_positive',five_to_fifteen_power='arithmetic_three_mean_MW',ramp_mapping='quarter_min_ramp_from_current_previous_power')
def sample(da,run,w,output,name,domain,*,verified_request=None):
 # Standalone callers retain the complete independent source reconstruction.
 need(verified_request is not None,'live verified request required')
 run=Path(run).resolve();case=convert_full_case(loads((run/'seed/case.json').read_bytes()))
 from gd_forecast_inputs import apply_forecast
 apply_forecast(case,run/'seed',verified_request['delivery_tick'])
 return _write_parameters(case,da,run,w,output,name,domain,verified_request=verified_request)

def _write_parameters(case,da,run,w,output,name,domain,*,verified_request):
 """Internal read-only parameter projection, not an input verification endpoint.

 The normal sample() above reconstructs its own case. math_prepare calls this
 only after its existing accepted-source/forecast checks, and retains the
 independent domain reconstruction and final complete source verification.
 No caller-supplied case option is added to the standalone interface.
 """
 need(verified_request is not None,'live verified request required')
 da,run,r=Path(da).resolve(),Path(run).resolve(),verified_request
 need(name and type(w)is int and -1<=w<=95,'named sample/window; negative seed is historical only')
 e=case['energy_inputs'];first=r['delivery_tick'];n=min(8,96-first)
 need(type(first)is int and first>=0 and first+n<=96,'same-period PRICE input interval')
 out=Path(output).resolve();need(not out.exists(),'fresh parameters only');out.parent.mkdir(parents=True,exist_ok=True)
 c=dict(schema='gd.short.PRICE.parameters.v1',name=name,mode='research',case_sha256=sha(run/'seed/case.json'),domain=domain,delta=.1,penalties_yuan_per_MWh=dict(load=1000.,accident=200.,network=NETWORK_DEFAULT_CNY_PER_MWH,renewable_curtailment=curtailment_value(case)),VRE_offers_yuan_per_MWh={b:v[0] for b,v in bid_window(e,first,n).items()},eligibility={g:['pricing_stable']*n for g in e['thermal']},interpretations=CHOICES,storage_bounds_formula='bounded_absolute_reference_intersection_RESEARCH_NOT_LITERAL',constraints={},safety=dict(kind='unchecked'),logical_available_at=(datetime.fromisoformat(r['origin'])-timedelta(minutes=15)).isoformat() if w==-1 else r['cutoff'],available_at_is_synthetic=True,valid_until=(datetime.fromisoformat(r['origin'])+timedelta(days=2)).isoformat(),balance_allocation=dict(node='39',basis='one system slack pair per quarter at node39; explicit research not official',officially_certified=False),formal=False)
 for key,casekey in [('negative_reserve','negative_mw'),('primary_frequency_reserve','primary_mw')]:
  d=dict(requirement_MW=[case['reserve'][casekey][first+q] for q in range(n)],qualified_resources=list(e['thermal']))
  if key=='negative_reserve':d['load_side_upward_capability_MW']=[0]*n
  else:d['fraction']={g:[case['reserve']['primary_fraction'][g]]*n for g in e['thermal']}
  c['constraints'][key]=dict(status='required',data=d,basis='inherited selected case research values, not official Guangdong parameter')
 for key in ['group_power','group_energy','sections','hydro_spill','interregional_optimization']:
  c['constraints'][key]=dict(status='inapplicable',verified_empty_inventory=True,reason='explicit empty declared group/section/hydro research scope; interregional optimization user excluded, fixed physical ties retained')
 c['default_parameter_sources']={'load':dict(value=1000.,unit='yuan_per_MWh',source={**NETWORK_DEFAULT_SOURCE,'item':21},override='regional rule default, not authenticated active Guangdong parameter'),'network':dict(value=NETWORK_DEFAULT_CNY_PER_MWH,unit='yuan_per_MWh',source=NETWORK_DEFAULT_SOURCE,override='editable penalties_yuan_per_MWh.network; any explicit value is used unchanged')}
 c['VRE_offers_by_tick_yuan_per_MWh']=bid_window(e,first,n)
 c['forecast_vintage']=case.get('_forecast_proof')
 c['curtailment_parameter_basis']='User-selected research Mc=0 by default; retain provincial Mc SLc formula, explicit editable case override; not official numeric value'
 c['warning']='Named research sample only. All values explicit/editable; eligibility must pass actual accepted-state checks. Network default uses regional table item18=1000, not verified active Guangdong value; load default uses regional table item21=1000, delta is an explicit unverified research choice; other parameters/qualifications remain unverified, AC outside this model scope, no formal settlement.';write(out,c);return dict(status='prepared',parameters=str(out),native_calls=0,warning=c['warning'])

def resolved(parameters,case,da,run,w,r):
 p=Path(parameters).resolve();z=read(p);n=min(8,96-r['delivery_tick']);need(z['schema']=='gd.short.PRICE.parameters.v1' and z['mode']=='research','explicit research schema');need(z['case_sha256']==sha(run/'seed/case.json'),'parameter selected-case mismatch');need(z['available_at_is_synthetic']is True,'explicit counterfactual availability');need(z['interpretations']==CHOICES,'unsupported formula interpretation');need(z['balance_allocation']['node']=='39' and z['balance_allocation']['officially_certified']is False,'supported explicit research balance allocation');pair=run/f'W{w}/actual/ACCEPTED_PAIR.json';ar=read(da/'ARTIFACTS.json');daacc=da/ar['stages']['da_sced']['acceptance']['path'];pair=daacc if w==-1 else Path(r['typed_source_path'])
 def source(f,label,available=None):return dict(document=str(f),sha256=sha(f),locator=label,grade='explicit_research_assumption',available_at=available or z['logical_available_at'])
 def val(v,unit):return dict(value=v,unit=unit,source=source(p,'named research parameter '+z['name']))
 sources={k:source(f,k,r['cutoff'])for k,f in [('case',run/'seed/case.json'),('DA',daacc),('SCED',pair),('AGC',Path(r['effective_agc_path']))]}
 c=dict(schema='gd.PRICE.explicit_inputs.v23',mode='research',identity=dict(run_id=r['run_id'],branch_id=str(run),window_id=str(w),case_sha256=sources['case']['sha256'],DA_acceptance_sha256=sources['DA']['sha256'],SCED_acceptance_sha256=sources['SCED']['sha256'],AGC_award_sha256=sources['AGC']['sha256']),sources=sources,delta=val(z['delta'],'dimensionless'),domain={'current_sced_research':'current_final_SCED_reference_research','DA_reference_R61_compatible_research':'DA_reference_research'}[z['domain']],interpretations={k:dict(choice=v,status='explicit_research_interpretation',source=source(p,k))for k,v in z['interpretations'].items()},storage_bounds_formula=z['storage_bounds_formula'],penalties={k:val(v,'yuan_per_MWh')for k,v in z['penalties_yuan_per_MWh'].items()},VRE_offers={k:val(v,'yuan_per_MWh')for k,v in z['VRE_offers_yuan_per_MWh'].items()},eligibility={g:dict(quarters=v,source=source(p,'explicit pricing eligibility subject to accepted states'))for g,v in z['eligibility'].items()},constraints={k:{**v,'source':source(p,k)}for k,v in z['constraints'].items()},safety=z['safety'],time_mapping=dict(SCED_cells=n,SCED_step_minutes=15,PRICE_periods=n,PRICE_step_minutes=15,anchor='delivery_research',publish_quarters=1))
 need(z.get('forecast_vintage')==case.get('_forecast_proof'),'PRICE parameter vintage differs from case')
 e=case['energy_inputs'];out=resolve(c,dict(thermal=list(e['thermal']),VRE=list(e['buses'])),r['cutoff']);validate_wiring(out)
 out['VRE_offers_by_tick']=verify_window(z['VRE_offers_by_tick_yuan_per_MWh'],e,r['delivery_tick'],n)
 need(z['VRE_offers_yuan_per_MWh']=={b:v[0]for b,v in out['VRE_offers_by_tick'].items()},'legacy first-cell renewable offer mismatch')
 pars=dict(schema='gd.PRICE.penalties.v1',mode='research',factors={k:dict(value=z['penalties_yuan_per_MWh'][k],unit='yuan_per_MWh',meaning=meaning,source=source(p,meaning),effective_from=z['logical_available_at'],effective_to=z['valid_until'])for k,meaning in [('load','ML_prime'),('accident','MR_prime'),('network','M1_prime')]},balance_allocation=dict(basis=z['balance_allocation']['basis'],source=str(p),officially_certified=False))
 return out,c,pars,z['domain']
