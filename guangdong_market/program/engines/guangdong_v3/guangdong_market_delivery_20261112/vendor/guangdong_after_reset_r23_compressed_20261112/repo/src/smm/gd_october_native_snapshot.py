"""Original window62 research snapshot, never an execution/rolling credential.

Only immutable input bytes are serializable. Freeze registers an experiment
before the real ED call; this process-local capability is not market authority.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from weakref import WeakKeyDictionary
import pyomo.environ as pyo
from .gd_october_pricing_inputs import canonical, require, obj, num, vector, boolean, FAMILIES
from .gd_october_pricing_rules import PARAMETERS
from .gd_october_native_solve import NativeSolveSettings, _checked_result
from .gd_october_native_inputs import build_october_native_model, assert_october_native_input_binding
from .gd_october_roll import RollingWindow, Physics, advance_state_values, inventory, extract_october_model_steps, canonical_bytes, _same_numbers
from . import gd_october_rt_contract as c
from . import gd_solve, highs_solver

ORIGINAL_CODEC_SHA256='ec248f1088abaf68877520407b411de196d8c42d51156f40e9b9fbf33d8235ff'
PRICE_PARAMETERS=PARAMETERS+('tie_priority_penalty_yuan_per_mwh',)
_FROZEN=WeakKeyDictionary()
_ISSUED=WeakKeyDictionary()
CHOICE_SCHEMA='gd.october.native_pricing_choice.v3'
CHOICES=dict(ptdf_numerics='topology_certified_zero_and_bridge_cutset_exact_values',reference_stage='same_RT_selected_research_not_official_DA_resolution',
    power='energy_equivalent_mean',inventory='corresponding_endpoints',states='homogeneous_three_steps_exact_source_phase',
    four_sl='constant_three_steps_no_averaging',parameters='constant_three_steps_no_averaging',
    exogenous='existing_LMP_source_equals_SCED_energy_mean',thermal_bids='source_Pmin_base_integral_plus_original_incremental_segments',
    balance='source_hard_and_pricing_hard',storage_capacity='verified_V3_current_signed_baseline',
    renewable_capacity='verified_IEEE39_V2_retained_base',services='storage_zero_incremental_all_products_zero_AGC',
    carry='original_independent_research_declaration_not_observation',clocks='logical_research_and_actual_runtime_separate',
    pumping='fixed_selected_reference_research',negative_power='signed_magnitude_research_printed_conflict_unresolved',
    terminal='explicit_source_rolling_target_not_fitted_to_ED',publication='research_only_no_cash_no_execution',
    ed_reference='live_original_sced_snapshot_research_pricing_only',
    thermal_phase='homogeneous_three_step_phase_same_event_reject_mixed',
    transition_cost='source_step_rate_energy_integral_constant_not_offer',
    transition_boundary='left_closed_right_open_next_complete_stable_quarter_research',
    negative_demand_basis='energy_mean_from_own_original_24x5_source_not_replaced_by_load',
    ramp='adjacent_quarter_mean_power_plus_original_initial_MW_per_h_times_0.25_research')
FLAGS=dict(observation_verified=False,known_command_projection_verified=False,execution_confirmed=False,
    rolling_authority=False,whole_day=False,official_price=None,published=False,settlement_cash_created=False,
    pricing_dispatch_executable=False,operational_source_verified=False,source_authenticity_verified=False)

@dataclass(frozen=True,eq=False)
class ResearchPricingExperiment:
    policy_document: bytes
    eligibility_document: bytes
    original_codec_sha256: str
    def __deepcopy__(self,memo): return self

@dataclass(frozen=True)
class SnapshotPublication:
    selected_dispatch_id: str
    source: str
    published_at: str
    available_at: str
    clock_basis: str='declared_research_clock'

@dataclass(frozen=True)
class NativeSCEDSnapshotInputs:
    original_codec: bytes
    known_dispatch_reference: object
    settings: NativeSolveSettings
    publication: SnapshotPublication
    experiment: ResearchPricingExperiment
    @property
    def bridge_inputs(self):
        from .gd_october_job_codec import loads
        return loads(self.original_codec)

@dataclass(frozen=True,eq=False)
class NativeSCEDSnapshotSolution:
    artifact: bytes
    selected_dispatch_sha256: str
    @property
    def provenance(self): return json.loads(self.artifact)['solver_call_provenance']
    def __deepcopy__(self,memo): return self


def _codes():
    from .gd_october_pricing_model import code_hashes
    return code_hashes()


def validate_research_pricing_experiment(experiment, bridge):
    require(type(experiment) is ResearchPricingExperiment,'explicit research experiment input required')
    require(type(experiment.policy_document)is bytes and type(experiment.eligibility_document)is bytes,'original experiment bytes required')
    p=c._document(experiment.policy_document);e=c._document(experiment.eligibility_document)
    obj(p,{'schema_version','experiment_id','source','classification','source_contract_identity','published_at','available_at','approved_at','approval_source','asof','choices','parameters','inventory','absence_reasons','pricing_eligibility','eligibility_source','network_slack_limit_mw','accident_slack_limit_mw','diagnostics'},'precommitted pricing experiment')
    require(p['schema_version']==CHOICE_SCHEMA and p['classification']=='research','new research choice v3 required')
    for key in ('source','experiment_id','approval_source'):c._text(p[key],key)
    require(p['source_contract_identity']==bridge['expected_identity'],'experiment original identity mismatch')
    require(p['choices']==CHOICES,'unsupported or changed experiment choices')
    obj(p['parameters'],PRICE_PARAMETERS,'seven explicit parameters')
    for key,v in p['parameters'].items():
        obj(v,{'value','source'},'parameter');num(v['value'],key,0);c._text(v['source'],key+' source')
    require(0<=p['parameters']['delta']['value']<=1,'delta outside [0,1]')
    core=bridge['physical_inputs']['model_inputs'];s=core['storage_spec'];units={u:d for plant in s['pumped'].values() for u,d in plant['units'].items()}
    families=FAMILIES+('pumped_plants',);obj(p['inventory'],families,'inventory');obj(p['absence_reasons'],families,'absence reasons')
    inventory_ids=[]
    for f,ids in p['inventory'].items():
        c._resources(ids,f);require(ids==sorted(ids),'sorted inventory required');inventory_ids+=ids
        require((ids and p['absence_reasons'][f]is None) or (not ids and isinstance(p['absence_reasons'][f],str) and p['absence_reasons'][f].strip()),'explicit absence required')
    require(len(set(inventory_ids))==len(inventory_ids),'globally unique inventory required')
    for f,ids in [('thermal',core['thermal']),('battery',s['batteries']),('pumped',units),('pumped_plants',s['pumped'])]:require(set(p['inventory'][f])==set(ids),'inventory mismatch '+f)
    for f in ('hydro','groups','load_side_reserve'):require(not p['inventory'][f],'unsupported '+f)
    contract=c._document(bridge['contract_document'])
    for family,target in [('wind_mw','wind'),('solar_mw','solar'),('boundary_tie_mw','tie')]:
        expected=next(r['resource_ids']for r in contract['required_series']if r['family']==family and r['stage']=='sced')
        require(set(p['inventory'][target])==set(expected),'original exogenous inventory mismatch '+target)
    for f in ('battery','pumped'):
        require(not any(any(row)for row in p['pricing_eligibility'][f].values()),'idle/off source storage cannot be research price eligible')
    obj(p['network_slack_limit_mw'],{'lines','sections'},'slack cap inventory')
    for f in ('lines','sections'):
        obj(p['network_slack_limit_mw'][f],core[f],f+' caps')
        for v in p['network_slack_limit_mw'][f].values():vector(v,f+' cap',0)
    vector(p['accident_slack_limit_mw'],'accident cap',0)
    require(not any(p['accident_slack_limit_mw']),'original hard accident requirement cannot be relaxed')
    obj(p['pricing_eligibility'],{'thermal','battery','pumped'},'eligibility')
    for f,v in p['pricing_eligibility'].items():
        obj(v,p['inventory'][f],f+' eligibility')
        for row in v.values():
            require(type(row)is list and len(row)==8,'eight explicit eligibility values')
            for b in row:boolean(b,'eligibility')
    obj(e,{'schema_version','classification','source_contract_identity','thermal','fixed_restrictions','source','reason','published_at','available_at','permission_basis'},'eligibility declaration')
    require(e['schema_version']=='gd.october.research_pricing_eligibility.v1' and e['classification']=='research','research qualification required')
    require(e['source_contract_identity']==p['source_contract_identity'] and e['thermal']==p['pricing_eligibility']['thermal'],'qualification source mismatch')
    require(e['permission_basis']=='authored_research_hypothesis_not_operator_qualification','qualification is not operator certification')
    for key in ('source','reason'):c._text(e[key],key)
    obj(e['fixed_restrictions'],core['thermal'],'fixed restrictions')
    for g,row in e['fixed_restrictions'].items():
        require(type(row)is list and len(row)==8,'explicit fixed restrictions required')
        for t,v in enumerate(row):boolean(v,'fixed restriction');require(not (v and e['thermal'][g][t]),'fixed restriction cannot qualify for price')
    ref=dict(source_id='r10f-pricing-eligibility',source_sha256=sha256(experiment.eligibility_document).hexdigest())
    require(p['eligibility_source']==ref,'eligibility original bytes not bound')
    contract=c._document(bridge['contract_document']);cut=c._time(contract['information_cutoff']['lmp'],'cutoff')
    pub,avail,appr=(c._time(p[k],k)for k in ('published_at','available_at','approved_at'))
    require(pub<=avail<=cut<=c._time(p['asof'],'asof') and pub<=appr<=cut,'experiment logical clocks invalid')
    require(c._time(e['published_at'],'eligibility pub')<=c._time(e['available_at'],'eligibility availability')<=pub,'eligibility unavailable when experiment declared')
    obj(p['diagnostics'],{'energy_only_nodes','epsilon_mw','relative_tolerance','absolute_tolerance','linked_demand'},'precommitted diagnostics')
    diag=p['diagnostics'];require(type(diag['energy_only_nodes'])is list and len(diag['energy_only_nodes'])>=2 and len(set(diag['energy_only_nodes']))==len(diag['energy_only_nodes']),'two distinct diagnostic buses required')
    require(set(diag['energy_only_nodes'])<=set(core['buses']),'unknown diagnostic node')
    require(type(diag['epsilon_mw'])is list and len(diag['epsilon_mw'])>=2,'epsilon ladder required')
    for v in diag['epsilon_mw']:require(num(v,'epsilon',0)>0,'positive epsilon')
    for key in ('relative_tolerance','absolute_tolerance'):require(num(diag[key],key,0)>0,'positive tolerance')
    require(diag['linked_demand']=='independent_all_three_RHS_ledger_and_separate_nonzero_mu_minus_fixture','explicit linked diagnostic required')
    return p,dict(e,source_ref=ref)


def freeze_research_pricing_experiment(*,policy_document,eligibility_document,original_codec):
    from .gd_october_job_codec import loads
    require(type(original_codec)is bytes and sha256(original_codec).hexdigest()==ORIGINAL_CODEC_SHA256,'exact saved original window62 codec required; no regeneration')
    e=ResearchPricingExperiment(policy_document,eligibility_document,ORIGINAL_CODEC_SHA256)
    validate_research_pricing_experiment(e,loads(original_codec))
    _FROZEN[e]=(policy_document,eligibility_document,datetime.now(timezone.utc).isoformat(),_codes())
    return e


def _experiment_binding(inputs):
    e=inputs.experiment
    require(type(e)is ResearchPricingExperiment and e in _FROZEN,'experiment must be frozen in this process before ED; archived data is not a receipt')
    p,qual=validate_research_pricing_experiment(e,inputs.bridge_inputs)
    raw,elig,registered,codes=_FROZEN[e]
    require(e.policy_document==raw and e.eligibility_document==elig and codes==_codes(),'frozen experiment bytes or implementation changed; new experiment and new ED required')
    require(e.original_codec_sha256==ORIGINAL_CODEC_SHA256==sha256(inputs.original_codec).hexdigest(),'original codec identity changed')
    return p,qual,dict(policy_sha256=sha256(raw).hexdigest(),eligibility_sha256=sha256(elig).hexdigest(),
        original_codec_sha256=e.original_codec_sha256,actual_registered_at=registered,source_code_hashes=codes)


def snapshot_initial_values(bridge):
    """Research-only state arithmetic from exact original sources, no observation."""
    from .gd_october_job_codec import loads
    core=bridge['physical_inputs']['model_inputs'];s=core['storage_spec'];base=loads(bridge['source_artifacts']['r10-capacity-base-scenario'])
    at=bridge['expected_delivery_start'];values=dict(thermal=deepcopy(base['blueprint']['boundaries'][at]),batteries={},pumped_units={},pumped_plants={})
    bats={};plants={};units={}
    def constant(d,key):
        vals=d[key];require(len(set(vals.values()))==1,'replay requires constant '+key);return vals[1]
    for g,d in core['thermal'].items():
        require(values['thermal'][g]==dict(online=d['initial_online'],power_mw=d['initial_power_mw'],state_age_h=d['initial_state_age_h']),'raw initial thermal mismatch')
    for uid,d in s['batteries'].items():
        initial=d['actual_initial'];values['batteries'][uid]={k:initial[k]for k in ('mode','net_power_mw','mode_elapsed_h')}
        values['batteries'][uid].update(energy_mwh=d['initial_energy_mwh'],cumulative_throughput_mwh=d['cumulative_throughput_used_mwh'],throughput_mwh_by_day={day:b['used_throughput_mwh']for day,b in d['cycle_budgets'].items()})
        bats[uid]=dict(eta_charge=d['roundtrip_efficiency']**.5,eta_discharge=d['roundtrip_efficiency']**.5,rated_mwh=d['energy_capacity_mwh'],min_mwh=constant(d,'energy_min_mwh'),max_mwh=constant(d,'energy_max_mwh'),max_cycles_per_day=d['cycle_limit'])
    for pid,d in s['pumped'].items():
        values['pumped_plants'][pid]=dict(upper_m3=d['upper_initial_m3'],lower_m3=d['lower_initial_m3'])
        plants[pid]={k:constant(d,k)for k in ('upper_min_m3','upper_max_m3','lower_min_m3','lower_max_m3')}
        for uid,u in d['units'].items():
            values['pumped_units'][uid]={k:u['actual_initial'][k]for k in ('mode','net_power_mw','mode_elapsed_h')}
            units[uid]=dict(plant_id=pid,pump_m3_per_mwh=constant(u,'pump_m3_per_mwh'),generate_m3_per_mwh=constant(u,'generation_m3_per_mwh'))
    end=bridge['physical_inputs']['period_intervals'][-1]['delivery_end'];target=next(x for x in base['terminal_boundaries']if c._time(x['at'],'terminal')==c._time(end,'end'))
    terminal=dict(battery_energy_mwh=target['battery_energy_mwh'],upper_volume_m3={p:v['upper']for p,v in target['pumped_m3'].items()},lower_volume_m3={p:v['lower']for p,v in target['pumped_m3'].items()})
    for uid,d in s['batteries'].items():require(d['final_energy_mwh']==terminal['battery_energy_mwh'][uid] and c._time(d['final_energy_at'],'battery final')==c._time(end,'end'),'source battery terminal mismatch')
    physics=Physics(bats,plants,units,'original source research arithmetic, not physical observation','delivery_interval_start_Asia_Shanghai','symmetric_sqrt_roundtrip',core['thermal_trajectories']['book'])
    physics.validate(values)
    return values,terminal,physics


def preflight_october_native_snapshot(inputs):
    require(type(inputs)is NativeSCEDSnapshotInputs,'exact new snapshot input type required')
    require(type(inputs.settings)is NativeSolveSettings and type(inputs.publication)is SnapshotPublication,'typed settings/publication required')
    policy,eligibility,binding=_experiment_binding(inputs);bridge=inputs.bridge_inputs
    require(bridge['mode']=='research' and bridge['physical_inputs']['initial_state']['kind']=='synthetic_research_state','original independent research initial declaration required')
    settings=inputs.settings.validate();pub=inputs.publication;contract=c._document(bridge['contract_document'])
    require(pub.clock_basis=='declared_research_clock','original snapshot uses explicitly synthetic logical clock')
    for v in (pub.selected_dispatch_id,pub.source):c._text(v,'snapshot publication')
    require(c._time(contract['information_cutoff']['sced'],'sced')<=c._time(pub.published_at,'publication')<=c._time(pub.available_at,'availability')<=c._time(policy['published_at'],'mapping publication'),'ED logical publication order invalid')
    window=RollingWindow(bridge['expected_identity']['run_id'],bridge['expected_identity']['window_id'],c._time(bridge['expected_delivery_start'],'start'),c._time(contract['information_cutoff']['sced'],'cutoff'),'official_reference_T_minus_15','Original research snapshot clock only')
    window.validate()
    intent=dict(stage='october_original_snapshot_research_pricing_only',solver='HiGHS',threads=1,time_limit_s=settings.time_limit_s,mip_rel_gap=settings.mip_rel_gap,mip_abs_gap=0.,tee=settings.tee,objective_offset_yuan=0.,fixed_state_lp=True,publication=pub.__dict__,experiment=binding,initial_basis='original_independent_research_declaration',**FLAGS)
    model=build_october_native_model(known_dispatch_reference=inputs.known_dispatch_reference,solve_intent=intent,**bridge)
    before=assert_october_native_input_binding(model)
    require(not before['uses_future_actual'] and before['causal_eligible']is False,'snapshot retains synthetic noncausal eligibility; future actual forbidden')
    require(not any(not v.fixed and v.is_integer() for v in model.component_data_objects(pyo.Var)),'fixed state continuous LP required')
    require(len(model.N)==39 if hasattr(model,'N') else len(bridge['physical_inputs']['model_inputs']['buses'])==39,'full IEEE39 inventory required')
    from .gd_october_pricing_thermal_v4 import derive_thermal_pricing_rows
    thermal,power,states,audit=derive_thermal_pricing_rows(bridge,model,None,eligibility)
    require(sorted(audit['active_event_ids'])==policy['inventory']['transitions'],'active event inventory mismatch')
    initial,terminal,physics=snapshot_initial_values(bridge)
    return dict(model=model,before=before,window=window,bridge=bridge,settings=settings,intent=intent,experiment=binding,policy=policy,eligibility=eligibility,initial=initial,terminal=terminal,physics=physics)


def _primal(model):
    return canonical([(v.name,num(v.value,'complete finite ED primal'))for v in model.component_data_objects(pyo.Var)])


def solve_october_native_snapshot(inputs):
    require(snapshot_path_intact(),'replaced snapshot/source path cannot issue receipt')
    context=preflight_october_native_snapshot(inputs);m=context['model'];settings=context['settings']
    started=datetime.now(timezone.utc);require(highs_solver.native_solver_path_intact(),'real HiGHS path required before ED')
    result=gd_solve.solve_dispatch(m,stage=context['intent']['stage'],time_limit_s=settings.time_limit_s,threads=1,mip_rel_gap=settings.mip_rel_gap,objective_offset_yuan=0.,tee=settings.tee)
    ended=datetime.now(timezone.utc);require(snapshot_path_intact() and highs_solver.native_solver_path_intact(),'replaced call path cannot issue receipt')
    check=_checked_result(result,m,settings);require(check['metrics']['solver_version']=='1.15.1','tested HiGHS1.15.1 required')
    after=assert_october_native_input_binding(m);require(after==context['before'],'input binding changed during ED');_experiment_binding(inputs)
    extraction=extract_october_model_steps(m,window=context['window'],read_value=pyo.value)
    values=deepcopy(context['initial']);replay=[]
    for step in extraction['steps']:
        values=advance_state_values(values,powers_mw=step['powers_mw'],states=step['states'],physics=context['physics'],delivery_start=step['delivery_start'],delivery_end=step['delivery_end'])
        _same_numbers(step['end_inventory'],inventory(values),'original source endpoint replay')
        replay.append(deepcopy(values))
    _same_numbers(inventory(values),context['terminal'],'original terminal boundary')
    from .gd_october_pricing_thermal_v4 import derive_thermal_pricing_rows
    derive_thermal_pricing_rows(context['bridge'],m,extraction['steps'],context['eligibility'])
    prov=dict(evidence_kind='internal_highs_call',solver_called=True,native_numerical_acceptance=True,mock_flow_only=False,solver_backend='highs',invocation_started_at=started.isoformat(),invocation_completed_at=ended.isoformat(),solver_version=check['metrics']['solver_version'],solver_settings=check['metrics']['solver_settings'],input_mode='research',uses_future_actual=after['uses_future_actual'],input_causal_eligible=after['causal_eligible'],derived_simulation=after['derived_simulation'],publication_clock_basis='declared_research_clock',**check,**FLAGS)
    pub=inputs.publication
    document=dict(schema_version='gd.october.native_sced_snapshot_selected.v1',run_id=after['run_id'],window_id=after['window_id'],selected_dispatch_id=pub.selected_dispatch_id,published_at=pub.published_at,available_at=pub.available_at,source=pub.source,delivery_start=context['window'].delivery_start,delivery_end=context['window'].delivery_end,steps=extraction['steps'],initial_inventory=inventory(context['initial']),original_source_replay=replay,assignment_audit=extraction['assignment_audit'],full_primal=json.loads(_primal(m)),solver_call_provenance=prov,experiment=context['experiment'],source_identity=context['bridge']['expected_identity'],**{k:after[k]for k in ('input_bundle_sha256','solve_intent_sha256','physical_model_sha256')},**FLAGS)
    raw=canonical_bytes(document);solution=NativeSCEDSnapshotSolution(raw,sha256(raw).hexdigest())
    _ISSUED[solution]=(inputs,raw,context['before'],m,_primal(m),context['experiment'])
    return solution


def validate_october_native_snapshot(solution,inputs):
    require(snapshot_path_intact(),'snapshot implementation changed')
    require(type(solution)is NativeSCEDSnapshotSolution and solution in _ISSUED,'fresh process-local snapshot receipt required; archive/old native receipt forbidden')
    original,raw,before,m,primal,experiment=_ISSUED[solution]
    require(inputs is original and solution.artifact==raw and solution.selected_dispatch_sha256==sha256(raw).hexdigest(),'snapshot input/result identity mismatch')
    require(_primal(m)==primal,'live ED primal changed')
    context=preflight_october_native_snapshot(inputs)
    require(before==context['before']==assert_october_native_input_binding(m) and experiment==context['experiment'],'snapshot intent/source/code/precommit changed; fresh ED required')
    data=json.loads(raw);identity={k:data[k]for k in ('run_id','window_id','selected_dispatch_id')};identity['selected_dispatch_sha256']=solution.selected_dispatch_sha256
    return data,identity,context


def finalize_snapshot_pricing_choice(inputs,solution):
    data,identity,context=validate_october_native_snapshot(solution,inputs)
    return canonical(dict(context['policy'],ed_selected_identity=identity))

# The original functions are captured once. This is ordinary patch detection,
# not protection against hostile Python modifying both implementation and guard.
_HELPERS={n:globals()[n]for n in ('validate_research_pricing_experiment','freeze_research_pricing_experiment','_experiment_binding','snapshot_initial_values','preflight_october_native_snapshot','_primal','solve_october_native_snapshot','validate_october_native_snapshot','finalize_snapshot_pricing_choice','_codes','build_october_native_model','assert_october_native_input_binding','_checked_result','advance_state_values','extract_october_model_steps','_same_numbers','inventory')}
_DEPS=((gd_solve,'solve_dispatch',gd_solve.solve_dispatch),(highs_solver,'native_solver_path_intact',highs_solver.native_solver_path_intact))
from types import FunctionType as _FunctionType
from . import gd_october_pricing_thermal_v4 as _thermal_pricing_module
from . import gd_october_thermal as _thermal_source_module
from . import gd_october_ieee39_scenario as _scenario_module
from . import gd_october_research_thermal as _research_thermal_module
from . import gd_october_pricing_network as _network_module
from . import gd_october_forecast_inputs as _forecast_module
from . import gd_october_research_forecast as _research_forecast_module
from . import gd_horizon as _horizon_module
_SOURCE_HELPERS=tuple((m,name,f)for m in (_thermal_pricing_module,_thermal_source_module,_scenario_module,_research_thermal_module,_network_module,_forecast_module,_research_forecast_module,_horizon_module)
    for name,f in vars(m).items()if isinstance(f,_FunctionType))
def snapshot_path_intact():
    return (all(globals()[n]is f for n,f in _HELPERS.items()) and all(getattr(m,n)is f for m,n,f in _DEPS)
        and all(getattr(m,n)is f for m,n,f in _SOURCE_HELPERS))
