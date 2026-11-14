"""Fresh explicit inputs -> R56 SCUC -> same-integer SCED -> independent DA PRICE.
No old result/matrix is used to construct this run. Native is separate, explicit.
"""
from run_protocol import *
import sys,math,dataclasses,shutil,argparse
from copy import deepcopy
from datetime import datetime,timezone
sys.path[:0]=[str(B/'da_math'),str(PACKAGE/'vendor/guangdong_after_reset_r23_compressed_20261112/repo/src'),str(PACKAGE/'vendor/guangdong_r21_day_verified_final_v1_20261112/source/repo/src'),str(PACKAGE/'vendor/guangdong_r56_market_builder_20261112')]
import smm
smm.__path__.append(str(B/'da_math/smm_extend'))
smm.__path__.append(str(B/'price_dependencies_v4'))
from smm.gd_october_job_codec import loads
from smm.highs_solver import serialize_model,validate_options
from smm.gd_r23_equivalent_compression import compress,fix_all_original_integers,lift,audit
from market_builder import build_market
import network_elimination as net
from scoped_kkt import metrics
from physical_geometry import build_geometry
from r53_bridge import build_price_dto,case_digest,SCHEMA
from eligible_vre53 import attach_eligible_vre

_base_own_sources=own_sources
def own_sources():
 pins=_base_own_sources()
 for p in [Path(__file__),B/'pipeline_da_checked.py',B/'run_matrix_profile.py',B.parent/'prepare_matrix_profile.py']:
  pins[str(p.resolve())]=sha(p)
 return pins

def load_case(runroot):return loads((Path(runroot)/'inputs/case.json').read_bytes())
def inputs(run):
 root=Path(run);r=verify_sources(root)
 for item in r['inputs'].values():resolve(root,item)
 case=load_case(root);grid=read(root/'inputs/grid.json');policy=read(root/'inputs/policy.json');agc=read(root/'inputs/agc.json')
 for z in agc.values():
  for k in ('up_mw','down_mw'):z[k]={int(i):v for i,v in z[k].items()}
 need(len(grid)==98 and sorted(x.get('kind','')for x in grid[96:])==['peak','valley'],'one peak and one valley')
 need(math.isfinite(policy['curtailment_penalty']['yuan_per_mwh']),'finite curtailment penalty')
 need(policy['curtailment_penalty']['yuan_per_mwh']==1000 and policy['noquote_energy_bid']['yuan_per_mwh']==-50,'supported full-chain dispatch M2=1000 and noquote=-50 only; never silently substitute')
 need(policy['noquote_energy_bid']['stage_scope']==['SCUC','SCED','DA_PRICE','RT','RT_PRICE'],'whole-chain same bid scope')
 return case,grid,policy,agc

def init(bundle,run,pricing_spec,rt_assets):
 bundle=Path(bundle).resolve();run=Path(run).resolve();need(not run.exists(),'fresh run required; use resume for existing run');d=read(bundle);need(d['schema']=='gd.delivery.scuc_inputs.v1','explicit portable bundle')
 scenario=None;scenario_path=None
 if 'scenario_manifest_file'in d:
  relative=Path(d['scenario_manifest_file']);need(not relative.is_absolute()and '..'not in relative.parts,'scenario manifest must be relative without escape')
  scenario_path=(bundle.parent/relative).resolve();need(scenario_path.is_relative_to(bundle.parent),'scenario path escapes bundle')
  sys.path.insert(0,str(ROOT))
  from scenario_inputs import validate_scenario
  scenario=validate_scenario(bundle.parent);need(isinstance(scenario,dict)and scenario.get('identity')and scenario.get('files'),'validated scenario receipt required')
  need(scenario_path.name=='SCENARIO.json','expected validated SCENARIO.json')
  need(scenario.get('name')and scenario.get('description'),'validated scenario name/description required')
 run.mkdir();(run/'inputs').mkdir()
 if scenario is not None:shutil.copyfile(scenario_path,run/'inputs/scenario.json')
 for name,key in [('case','case_file'),('grid','grid_file'),('policy','policy_file'),('agc','agc_file')]:shutil.copyfile(bundle.parent/d[key],run/'inputs'/f'{name}.json')
 spec=read(pricing_spec)
 # Remove historic result identity from static declared research geometry input.
 for k in ('new_case_sha256','source_fact_pins','source_field_hashes','PTDF_crosscheck_max_abs'):spec.pop(k,None)
 spec.update(source='explicit retained research PRICE geometry/representatives; current run receipt supplies actual state',renewable_price_policy='fixed_verified_SCED_dispatch_nonpricing_research',nonpricing_assumption_source='temporary base expression immediately replaced by eligible VRE PRICE block',renewable_noquote_is_not_automatic_nonpricing=True)
 write(run/'inputs/pricing_spec.json',spec)
 asset=read(rt_assets);need(asset['schema']=='gd.pipeline.rt_assets.v1','explicit RT research assets')
 assets_dir=run/'inputs/rt_assets';assets_dir.mkdir()
 for name,r in asset['files'].items():
  src=Path(rt_assets).resolve().parent/r['path'];need(sha(src)==r['sha256'],'RT input asset pin');dest=assets_dir/r['path'];need(dest.resolve().is_relative_to(assets_dir.resolve()),'RT asset path');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dest)
 shutil.copyfile(rt_assets,assets_dir/'ASSETS.json')
 ins={n:ref(run,run/'inputs'/f'{n}.json')for n in ('case','grid','policy','agc','pricing_spec')};ins['rt_assets']=ref(run,assets_dir/'ASSETS.json')
 if scenario is not None:ins['scenario']=ref(run,run/'inputs/scenario.json')
 sources=own_sources()
 if scenario is not None:sources[str(ROOT/'scenario_inputs.py')]=sha(ROOT/'scenario_inputs.py')
 r=dict(schema='gd.pipeline.run.v1',run_id='gd-'+uuid.uuid4().hex,input_identity=digest(ins),source_identity=digest(sources),source_pins=sources,inputs=ins,status='initialized',formal=False,scope=dict(name=scenario['name'],description=scenario['description'],basis='research_only_not_actual_operational_data',scenario_identity=scenario['identity'])if scenario is not None else'same explicit uniform-event IEEE39 research case only',native_authorized=False)
 write(run/'RUN.json',r);inputs(run);return r

def stage_ready(run,stage,artifacts,extra=None):
 r=verify_sources(run);p=Path(run)/stage
 z=dict(schema='gd.pipeline.prepared.v1',stage=stage,run_id=r['run_id'],input_identity=r['input_identity'],source_identity=r['source_identity'],artifacts={k:ref(run,v)for k,v in artifacts.items()},native_calls=0,**(extra or {}));write(p/'PREPARED.json',z);return z

def checked_prepared(run,stage):
 p=Path(run)/stage;z=read(p/'PREPARED.json');r=verify_sources(run)
 need(z['run_id']==r['run_id']and z['input_identity']==r['input_identity']and z['source_identity']==r['source_identity'],'stage/run identity')
 for f in z['artifacts'].values():resolve(run,f)
 return z

def prepare_scuc(run):
 run=Path(run);p=run/'da_scuc'
 if (p/'PREPARED.json').exists():return checked_prepared(run,'da_scuc')
 need(not p.exists(),'incomplete pure preparation retained; choose explicit fresh run, no overwrite');case,grid,policy,agc=inputs(run);mc=deepcopy(case)
 from smm.gd_horizon import EnergyHorizon,EnergyPeriod
 h=mc['energy_inputs']['horizon'];mc['energy_inputs']['horizon']=EnergyHorizon(**dict(h,periods=tuple(EnergyPeriod(**z)for z in h['periods'])))
 m=build_market(mc,grid,policy,agc);full,vs,cs=serialize_model(m,validate_options(dict(time_limit=3600.,threads=1,mip_rel_gap=.01,mip_abs_gap=0.,output_flag=True)));full['preparation_elapsed_s']=0.;names=dict(variables=[v.name for v in vs],constraints=[c.name for c in cs]);del m,vs,cs
 compact,cn,plan=compress(full,names,case);affine=net.derive(compact,cn,case);q,n,np=net.transform(compact,cn,affine)
 p.mkdir();arts={}
 for name,x in [('full_template',full),('full_names',names),('compact_template',compact),('compact_names',cn),('compression_plan',plan),('network_plan',np),('template',q),('names',n)]:write(p/(name+'.json'),x);arts[name]=p/(name+'.json')
 return stage_ready(run,'da_scuc',arts,dict(columns=len(q['variables']),rows=len(q['rows'])))

def accepted(run,stage):
 p=Path(run)/stage;z=read(p/'ACCEPTANCE.json');r=verify_sources(run);need(z['status']=='accepted'and z['run_id']==r['run_id']and z['input_identity']==r['input_identity']and z['source_identity']==r['source_identity']and z['stage']==stage,'accepted current run only')
 for v in z['artifacts'].values():resolve(run,v)
 return z

def sced_objective(full,names,case):
 q=deepcopy(full);terms=[];removed=[]
 for j,c in q['objective']['terms']:
  name=names['variables'][j];stem=name.split('[',1)[0];fee=0.
  if stem=='online':fee=c
  elif stem in('start','stop'):
   g,t=name.split('[',1)[1][:-1].replace("'",'').split(',');fee=case['energy_inputs']['thermal'][g]['startup_yuan'if stem=='start'else'shutdown_yuan'][int(t)]
  if c-fee:terms.append([j,c-fee])
  if fee:removed.append(dict(index=j,name=name,coefficient=fee,kind='minimum_output'if stem=='online'else'event'))
 q['objective']['terms']=terms;return q,removed

def prepare_sced(run):
 run=Path(run);a=accepted(run,'da_scuc');p=run/'da_sced'
 if (p/'PREPARED.json').exists():return checked_prepared(run,'da_sced')
 need(not p.exists(),'incomplete stage retained');case,grid,policy,agc=inputs(run);full=read(run/'da_scuc/full_template.json');n=read(run/'da_scuc/full_names.json');x=read(run/'da_scuc/full_primal.json');running,removed=sced_objective(full,n,case);fixed=fix_all_original_integers(running,x);fixed['dual']=True;fixed['options'].update(time_limit=3600.,output_flag=False)
 q,cn,plan=compress(fixed,n,case);q['options'].update(dual_feasibility_tolerance=1e-9,simplex_scale_strategy=0)
 p.mkdir();arts={};scope=dict(removed=[dict(z,value=x[z['index']],amount=z['coefficient']*x[z['index']])for z in removed],curve_energy_retained=True,old_noload_absent=True,new_SCUC_acceptance_sha256=sha(run/'da_scuc/ACCEPTANCE.json'))
 for name,z in [('full_template',fixed),('full_names',n),('template',q),('names',cn),('compression_plan',plan),('COST_SCOPE',scope),('AGC_DECLARATION',agc),('policy',policy)]:write(p/(name+'.json'),z);arts[name]=p/(name+'.json')
 return stage_ready(run,'da_sced',arts,dict(columns=len(q['variables']),rows=len(q['rows'])))

def verify_price_dependencies():
 m=read(B/'price_dependencies_v4/MANIFEST.json')
 for p,h in m['files'].items():need(sha(B/p)==h,'PRICE stage dependency changed')
 return m

def prepare_price(run):
 verify_price_dependencies()
 run=Path(run);accepted(run,'da_scuc');ac=accepted(run,'da_sced');p=run/'da_price'
 if (p/'PREPARED.json').exists():return checked_prepared(run,'da_price')
 need(not p.exists(),'incomplete stage retained');case,grid,policy,agc=inputs(run);case['contract']['economic_policy']='market98_hidden_physical_research';case['R53_objective_policy']=policy;case['energy_inputs']['renewable_bid_yuan_per_mwh']={k:-50. for k in case['energy_inputs']['renewable_bid_yuan_per_mwh']}
 physical=read(run/'da_sced/physical_solution.json');expanded=read(run/'da_sced/expanded_response.json');names=read(run/'da_sced/full_names.json');values={n.replace("'",''):v for n,v in zip(names['variables'],expanded['primal'])}
 def event(stage):return sha(run/stage/native_dir(stage)/'request.json')+':'+sha(run/stage/native_dir(stage)/'response.json')
 receipt=dict(PRICE_stage_dependency_identity=digest(verify_price_dependencies()),schema=SCHEMA+'.source_receipt',stage='fixed_SCED',status='Optimal',run_id=event('da_sced'),scuc_run_id=event('da_scuc'),case_sha256=case_digest(case),physical_report_sha256=digest(physical),expanded_values_sha256=digest(values),source_kind='native_highs_sced',logical_available_at=case['contract']['publication_at'],generated_at=datetime.now(timezone.utc).isoformat(),SCUC_accepted=True,diagnostic_only=False,publication_allowed=False,new_fixed_sced_primal_KKT_verified=True,SCUC_fixed_discrete_match_verified=True,SCUC_request_sha256=sha(run/'da_scuc/native_v2/request.json'),SCUC_response_sha256=sha(run/'da_scuc/native_v2/response.json'),SCED_request_sha256=sha(run/'da_sced/native_v2/request.json'),SCED_response_sha256=sha(run/'da_sced/native_v2/response.json'),expanded_witness_source_sha256=sha(run/'da_sced/expanded_response.json'),scenario_model_sha256=sha(run/'da_scuc/full_template.json'),actual_AGC_selection_sha256=sha(run/'da_sced/AGC_DECLARATION.json'),same_bid_policy_sha256=sha(run/'inputs/policy.json'),actual_metered_data=False)
 spec=read(run/'inputs/pricing_spec.json');spec['new_case_sha256']=case_digest(case);bridge=build_price_dto(case,physical,values,receipt,spec)
 from smm.gd_r14_da_lmp import build_model,serialize
 model,d,audit0=build_model(bridge['DTO']);vre=attach_eligible_vre(model,d,bid_proxy=-50.,price_curtailment_penalty=1000.,parameter_source=policy['noquote_energy_bid']['source']+'; independent PRICE appendix M2prime1000 baseline')
 vre.update(same_bid_policy_sha256=sha(run/'inputs/policy.json'),source_SCED_dispatch_M2_research=policy['curtailment_penalty']['yuan_per_mwh'])
 q,vs,cs=serialize(model);q.pop('deadline',None);q['options'].update(time_limit=3600.,threads=1,mip_rel_gap=.01,mip_abs_gap=0.,output_flag=False,dual_feasibility_tolerance=1e-9,simplex_scale_strategy=0);q['preparation_elapsed_s']=0.
 wrapper=dict(schema='gd.research.r53.new98_DA_PRICE.v1',base_DTO=d,eligible_VRE=vre,source_receipt=receipt,same_bid_policy=policy,physical_DA_schedule=True,financial_DA_participation='unknown',financial_price=None,formal_bill=None,publication_quantity_source='current run SCED physical, never PRICE variables',formal=False)
 p.mkdir();arts={}
 for name,z in [('base_bridge',bridge),('input',wrapper),('template',q),('names',dict(variables=[v.name for v in vs],constraints=[c.name for c in cs]))]:write(p/(name+'.json'),z);arts[name]=p/(name+'.json')
 return stage_ready(run,'da_price',arts,dict(columns=len(q['variables']),rows=len(q['rows']),source_receipt=receipt))

def derived_write(path,value):
 path=Path(path)
 if path.exists():need(read(path)==value,'existing derived review artifact differs; preserve and use explicit revised audit output')
 else:write(path,value)

def native_dir(stage):return 'native_v2' if stage in ('da_scuc','da_sced')else 'native'

def expected_request(run,stage):
 q=read(Path(run)/stage/'template.json')
 if stage=='da_sced':q['options']['dual_feasibility_tolerance']=1e-10
 return q

def actual_worker(stage,run=None):
 if stage=='da_scuc':
  need(run is not None,'run-scoped SCUC worker requires run')
  from run_matrix_profile import worker
  return worker(run)
 return B/'transport_recovery_v2/checked_mip_worker.py'if stage=='da_scuc'else B/'precision_recovery_v3/lp_worker_1e10.py'if stage=='da_sced'else B/'da_math/lp_worker.py'

def verify_transport():
 m=read(B/'transport_recovery_v2/TRANSPORT_MANIFEST.json')
 for path,h in m['files'].items():need(sha(B/path)==h,'transport/reviewer revision changed')
 return m

def review(run,stage):
 if stage=='da_price':verify_price_dependencies()
 verify_transport()
 run=Path(run);revision=read(run/'PRECISION_REVISION.json')
 for name,h in revision['source_pins'].items():need(sha(B/name)==h,'precision revision source changed')
 p=run/stage
 if (p/'ACCEPTANCE.json').exists():return accepted(run,stage)
 prep=checked_prepared(run,stage);q=read(p/native_dir(stage)/'request.json');r=read(p/native_dir(stage)/'response.json');n=read(p/'names.json');ident=read(p/native_dir(stage)/'IDENTITY.json');done=read(p/native_dir(stage)/'COMPLETED.json')
 need(done['returncode']==0 and done['response_sha256']==sha(p/native_dir(stage)/'response.json'),'completed current native')
 need({k:v for k,v in q.items()if k!='deadline'}==expected_request(run,stage),'actual prepared mathematical/options identity')
 need(ident['stage']==stage and ident['run_id']==prep['run_id']and ident['source_identity']==prep['source_identity'],'native current stage/source')
 worker=actual_worker(stage,run);need(ident['worker_sha256']==sha(worker),'actual worker source identity')
 need(not r.get('error') and r.get('backend')=='highs','worker/import error: '+str(r.get('error')))
 if stage=='da_scuc':
  rev=read(run/'TRANSPORT_REVISION.json');need(rev['activation_sha256']==sha(run/'matrix_profile/ACTIVATION.json'),'activation receipt changed')
 if stage=='da_scuc':need(r.get('optimize_calls')==1 and r.get('matrix_storage_verification',{}).get('all_stored_coefficients_indices_bounds_cost_types_rows_exact_checked')is True,'new checked matrix/import and one optimization')
 settings=r['native_settings'];need(settings['time_limit']==3600. and r['time_settings']['native_time_limit_s']==3600.,'actual native solver 3600 seconds required');need(settings['threads']==1 and settings['mip_rel_gap']==.01 and settings['mip_abs_gap']==0,'actual solver readback')
 if stage!='da_scuc':need(settings['dual_feasibility_tolerance']==(1e-10 if stage=='da_sced'else 1e-9) and settings['simplex_scale_strategy']==0,'actual LP precision options')
 need(ident['request_sha256']==sha(p/native_dir(stage)/'request.json')and ident['prepared_sha256']==sha(p/'PREPARED.json'),'native prepared identity')
 need(r['backend']=='highs'and r['solver_version']=='1.15.1'and r['native_status']=='Optimal'and r['has_primal'],'actual optimal HiGHS required')
 a=audit(q,r['primal'],r['objective']);case,grid,policy,agc=inputs(run);extra={};a['solver_policy']=dict(threads=1,native_time_limit_s=3600.,mip_rel_gap=.01 if stage=='da_scuc'else None,gap_applicability='MIP_only'if stage=='da_scuc'else 'not_applicable_LP')
 if stage=='da_scuc':
  need(r['native_mip_gap']<=.01+1e-12,'new gap');compact=net.lift(read(p/'network_plan.json'),r['primal']);audit(read(p/'compact_template.json'),compact,r['objective']);x=lift(read(p/'full_template.json'),read(p/'compression_plan.json'),compact);a['full_primal']=audit(read(p/'full_template.json'),x,r['objective']);derived_write(p/'full_primal.json',x);extra['full_primal']=p/'full_primal.json'
 elif stage=='da_sced':
  need(r['dual_valid'],'dual required');a['KKT']=metrics(q,r,n,angle_tolerance=1e-8);need(a['KKT']['passed'],'strict reduced SCED KKT')
  full=read(p/'full_template.json');fn=read(p/'full_names.json');plan=read(p/'compression_plan.json');x=lift(full,plan,r['primal']);y=[0.]*len(full['rows'])
  for j,v in zip(plan['kept_rows_original_indices'],r['dual']):y[j]=v
  expanded=dict(r,primal=x,dual=y);a['full_KKT']=metrics(full,expanded,fn,angle_tolerance=1e-8);need(a['full_KKT']['passed'],'strict full SCED KKT')
  previous=read(run/'da_scuc/full_primal.json');scuc=read(run/'da_scuc/full_template.json')
  need(all(abs(z-round(v))<=1e-8 for z,v,col in zip(x,previous,scuc['variables'])if col['kind']!='C'),'new fixed SCUC integers')
  a['full_physical']=audit(scuc,x);source=dict(run_id=verify_sources(run)['run_id'],SCUC_request_sha256=sha(run/'da_scuc/native_v2/request.json'),SCED_request_sha256=sha(p/native_dir(stage)/'request.json'),SCED_response_sha256=sha(p/native_dir(stage)/'response.json'),new_model_full_template_sha256=sha(run/'da_scuc/full_template.json'))
  physical=build_geometry(case,fn,expanded,source,curtailment_penalty=policy['curtailment_penalty']['yuan_per_mwh'])
  for k in ('event_cost_yuan','thermal_cost_yuan','storage_bid_cost_yuan','curtailment_penalty_cost_yuan','total_cost_yuan'):physical.pop(k,None)
  physical.update(schema='gd.research.r53.new98_SCED.physical.v1',SCED_objective_yuan=r['objective'],same_bid_policy_sha256=sha(run/'inputs/policy.json'),legacy_geometry_cost_calculation_not_used=True)
  for name,z in [('expanded_response',expanded),('physical_solution',physical)]:derived_write(p/(name+'.json'),z);extra[name]=p/(name+'.json')
 elif stage=='da_price':
  from price_audit import review_math
  z=review_math(q,r,n,read(p/'input.json'));prices=z.pop('prices');a.update(z);derived_write(p/'raw_prices.json',dict(raw_unclipped_LMP=prices,market_price=None,settlement_price=None,formal=False,quantity_source='current SCED'))
  extra['raw_prices']=p/'raw_prices.json'
 else:raise ValueError('unknown DA stage')
 arts={**prep['artifacts'],**{k:ref(run,v)for k,v in extra.items()},'request':ref(run,p/native_dir(stage)/'request.json'),'response':ref(run,p/native_dir(stage)/'response.json')}
 arts['transport_revision']=ref(run,run/'TRANSPORT_REVISION.json')
 arts['precision_revision']=ref(run,run/'PRECISION_REVISION.json')
 if stage=='da_price':arts['price_dependency_manifest']=ref(run,run/'PRICE_DEPENDENCIES.json')
 acc=dict(transport_review_code_sha256=sha(__file__),schema='gd.pipeline.acceptance.v1',status='accepted',stage=stage,run_id=prep['run_id'],input_identity=prep['input_identity'],source_identity=prep['source_identity'],artifacts=arts,audit=a,formal=False)
 derived_write(p/'ACCEPTANCE.json',acc);return acc

def artifacts(run):
 run=Path(run);r=verify_sources(run);stages={}
 for stage in ('da_scuc','da_sced','da_price'):
  a=accepted(run,stage);stages[stage]={**a['artifacts'],'acceptance':ref(run,run/stage/'ACCEPTANCE.json')}
 z=dict(schema='gd.pipeline.da_artifacts.v1',status='accepted',run_id=r['run_id'],input_identity=r['input_identity'],source_identity=r['source_identity'],inputs=r['inputs'],stages=stages,formal=False,old_native_results_used=False)
 if (run/'ARTIFACTS.json').exists():need(read(run/'ARTIFACTS.json')==z,'completed artifacts changed')
 else:write(run/'ARTIFACTS.json',z)
 return z
