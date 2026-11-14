"""Live selected-ED -> independently rebuilt pricing inputs, research only.

Nothing loaded from JSON can issue this capability. Original source bytes and
identities are retained; generated envelopes identify their ED parent separately.
The two native solves are independent. Neither proves execution or official price.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isclose
from weakref import WeakKeyDictionary

from .gd_october_pricing_inputs import canonical, require, obj, num, boolean, vector, CHOICES, FAMILIES
from .gd_october_pricing_rules import PARAMETERS, DECISIONS, REGION_SHA256
from .gd_october_roll import validate_prepared, fingerprint, MappingSelection
from .gd_october_native_solve import validate_october_native_solution, preflight_october_native_window, map_october_native_to_pricing_report
from .gd_october_job_codec import dumps
from . import gd_october_rt_contract as c

CHOICE_SCHEMA='gd.october.native_pricing_choice.v1'
CHOICE_SCHEMA_V2='gd.october.native_pricing_choice.v2'
NUMERIC_SCHEMA='gd.october.pricing_numeric.v3'
STORAGE_SCHEMA='gd.october.pricing_storage.v2'
ORIGIN='native_selected_sced_research'
PRICE_PARAMETERS=PARAMETERS+('tie_priority_penalty_yuan_per_mwh',)
BRIDGE_CHOICES=dict(reference_stage='same_RT_selected_research_not_official_DA_resolution',
    power='energy_equivalent_mean',inventory='corresponding_endpoints',states='constant_full_window_including_initial',
    four_sl='constant_three_steps_no_averaging',parameters='constant_three_steps_no_averaging',
    exogenous='existing_LMP_source_equals_SCED_energy_mean',thermal_bids='zero_base_same_source_segments',
    balance='source_hard_and_pricing_hard',storage_capacity='verified_V3_current_signed_baseline',
    renewable_capacity='verified_V2_if_nonzero_otherwise_explicit_zero',
    services='storage_zero_incremental_all_products_zero_AGC',
    carry='declared_or_observed_seed_plus_known_plan_projection_not_execution',
    clocks='logical_research_and_actual_runtime_separate',pumping='fixed_selected_reference_research',
    negative_power='signed_magnitude_research_printed_conflict_unresolved',
    terminal='explicit_source_rolling_target_not_fitted_to_ED',publication='research_only_no_cash_no_execution')
BRIDGE_CHOICES_V2=dict(BRIDGE_CHOICES,renewable_capacity='verified_general_research_V4_original_fixed_baseline',
    negative_demand_basis='energy_mean_from_own_original_24x5_source_not_replaced_by_load')
_BINDINGS=WeakKeyDictionary()

@dataclass(frozen=True,eq=False)
class NativePricingReference:
    binding_sha256: str
    def __deepcopy__(self,memo): return self


def is_issued_native_reference(value):
    return type(value) is NativePricingReference and value in _BINDINGS


def _iso(value): return c._time(value,'time').isoformat()
def _same(a,b,label): require(isclose(float(a),float(b),rel_tol=1e-10,abs_tol=1e-8),label+': numerical mapping mismatch')
def _quarters(values,label):
    require(type(values) is dict and set(values)==set(range(1,25)),label+': exact 24 source parameters required')
    out=[]
    for i in range(1,25,3):
        require(canonical(values[i])==canonical(values[i+1])==canonical(values[i+2]),label+': varying source parameters inside quarter unsupported')
        out.append(values[i])
    return out

def _zero(values,label): require(all(v==0 for v in values.values()),label+': nonzero source value unsupported')

def _materialize(native_inputs,request,solution,choice_document,rule_bytes):
    from .gd_october_native_solve import NativeWindowInputs, NativeWindowSolution
    require(native_bridge_path_intact(),'replaced native source-validation path cannot issue a bridge')
    require(type(choice_document) is bytes and type(rule_bytes) is bytes,'original choice and rule bytes required')
    require(sha256(rule_bytes).hexdigest()==REGION_SHA256,'wrong October rule bytes')
    require(type(native_inputs)is NativeWindowInputs and type(solution)is NativeWindowSolution,'exact old NativeWindow input and issued-solution types required; snapshot receipts are price-only and incompatible')
    choice=c._document(choice_document)
    obj(choice,{'schema_version','source','classification','source_contract_identity','ed_selected_identity','published_at','available_at',
        'approved_at','approval_source','asof','choices','parameters','inventory','absence_reasons','pricing_eligibility',
        'network_slack_limit_mw','accident_slack_limit_mw'},'native pricing choice')
    require(choice['schema_version'] in (CHOICE_SCHEMA,CHOICE_SCHEMA_V2) and choice['classification']=='research','explicit research choice schema required')
    v4_choice=choice['schema_version']==CHOICE_SCHEMA_V2
    c._text(choice['source'],'choice source');c._text(choice['approval_source'],'research selection approval source')
    require(canonical(choice['choices'])==canonical(BRIDGE_CHOICES_V2 if v4_choice else BRIDGE_CHOICES),'unknown/unimplemented native bridge choice')
    prepared_hash=validate_prepared(native_inputs.prepared,request)
    data,ed_identity=validate_october_native_solution(solution,native_inputs.prepared,prepared_hash)
    prov=solution.provenance
    require(prov['evidence_kind']=='internal_highs_call' and prov['native_numerical_acceptance'] is True and
        prov['solver_called'] is True and prov['mock_flow_only'] is False and prov['solver_backend']=='highs' and
        prov['termination']=='optimal' and prov['solver_version']=='1.15.1','live genuine optimal HiGHS ED required; mock receipt is insufficient')
    # causal_eligible=False also denotes explicitly authored synthetic research,
    # not just a timestamp violation. Preserve it; preflight rechecks every source cutoff.
    require(prov['uses_future_actual'] is False,'future-actual ED sources are outside this bridge slice')
    context=preflight_october_native_window(native_inputs,request=request)
    model=context['model'];before=context['before'];bridge=native_inputs.bridge_inputs
    for key in ('input_bundle_sha256','solve_intent_sha256','physical_model_sha256'):
        require(data[key]==before[key],key+': selected result differs from rebuilt original source inputs')
    require(solution.rt_reference.input_identity.as_dict()=={k:before[k] for k in solution.rt_reference.input_identity.as_dict()},'ED result reference identity mismatch')
    contract=c._document(bridge['contract_document']);identity=bridge['expected_identity'];w=native_inputs.prepared.window
    require(choice['source_contract_identity']==identity and choice['ed_selected_identity']==ed_identity,'separate source-contract and ED-selected identities required')
    cutoff=c._time(contract['information_cutoff']['lmp'],'LMP cutoff');asof=c._time(choice['asof'],'asof')
    p,a,approved=(c._time(choice[k],k) for k in ('published_at','available_at','approved_at'))
    require(c._time(data['available_at'],'ED availability')<=p<=a<=cutoff<=asof and p<=approved<=cutoff,'mapping/ED/LMP logical time order invalid')
    require(c._time(prov['invocation_started_at'],'runtime start')<=c._time(prov['invocation_completed_at'],'runtime end'),'ED runtime clock order invalid')
    selection=MappingSelection(ed_identity,'energy_equivalent_mean','corresponding_endpoints','require_constant','require_constant',
        choice['source'],p,a,approved,choice['approval_source'])
    mapped=map_october_native_to_pricing_report(solution=solution,window=w,expected_identity=ed_identity,selection=selection,lmp_cutoff=cutoff,asof=asof)
    require(mapped['status']=='mapped_research_only','selected ED mapping rejected: '+str(mapped['missing']))
    core=bridge['physical_inputs']['model_inputs'];initial=native_inputs.prepared.projection.projected_state.values
    require(core['balance_policy']=='hard','native pricing bridge requires source hard balance; no silent soft-to-hard change')
    require(core['renewable_mode']=='fixed_exogenous','dispatchable renewable mapping unsupported')
    require(not core['reserves']['load_side'],'load-side resources unsupported; cannot discard')
    require(core.get('thermal_trajectories') is None,'thermal trajectories unsupported; cannot discard transitioning units')
    require(len(core['reference_buses'])==1,'one connected network reference required')
    storage=core['storage_spec'];require(storage is not None,'native storage bridge requires declared full storage inventory')
    sa=model._gd_october_input_bridge_audit.get('storage_capacity_accounting_binding')
    require(sa is not None and sa['exact_delta_verified'] is True and 'storage_capacity_accounting' in core,'verified source storage V3 required; no legacy receipt upgrade')
    resources=deepcopy(choice['inventory']);families=FAMILIES+('pumped_plants',)
    obj(resources,families,'explicit full resource inventory');obj(choice['absence_reasons'],families,'explicit absence reasons')
    for family,ids in resources.items():
        c._resources(ids,family)
        require(ids==sorted(ids),'deterministic sorted inventory required')
        require((ids and choice['absence_reasons'][family] is None) or (not ids and isinstance(choice['absence_reasons'][family],str) and choice['absence_reasons'][family].strip()),'inventory absence declaration mismatch')
    require(len(sum(resources.values(),[]))==len(set(sum(resources.values(),[]))),'globally unique resource IDs required')
    for family in ('hydro','groups','transitions','load_side_reserve'):require(not resources[family],family+': actual unsupported inventory cannot disappear')
    units={u:d for plant in storage['pumped'].values() for u,d in plant['units'].items()}
    for family,ids in [('thermal',core['thermal']),('battery',storage['batteries']),('pumped',units),('pumped_plants',storage['pumped'])]:
        require(set(resources[family])==set(ids),'full source '+family+' inventory mismatch')
    exog={};series_nodes={}
    physical_map=c._document(bridge['source_artifacts'][bridge['resource_mapping_ref']['source_id']])
    for family,target in [('load_mw',None),('wind_mw','wind'),('solar_mw','solar'),('boundary_tie_mw','tie')]:
        raw={stage:next((c._document(bridge['source_artifacts'][r['source_id']]) for r in contract['series_sources'] if c._document(bridge['source_artifacts'][r['source_id']])['stage']==stage and c._document(bridge['source_artifacts'][r['source_id']])['family']==family),None) for stage in ('sced','lmp')}
        require(raw['sced'] is not None and raw['lmp'] is not None,'explicit zero/absent exogenous source envelope required')
        require(raw['sced']['resource_ids']==raw['lmp']['resource_ids'],'SCED/LMP resource inventory differs')
        if target:require(set(resources[target])==set(raw['sced']['resource_ids']),'full exogenous '+family+' inventory mismatch')
        bindings={r['source_resource_id']:r['bus'] for r in physical_map['rows'] if r['family']==family}
        require(set(bindings)==set(raw['sced']['resource_ids']),'physical exogenous node coverage mismatch')
        series_nodes[family]=bindings;exog[family]={}
        for rid in raw['sced']['resource_ids']:
            vs=[r['value'] for r in sorted(raw['sced']['rows'],key=lambda r:c._time(r['delivery_start'],'source interval')) if r['resource_id']==rid]
            ls=[r['value'] for r in sorted(raw['lmp']['rows'],key=lambda r:c._time(r['delivery_start'],'source interval')) if r['resource_id']==rid]
            require(len(vs)==24 and len(ls)==8,'exogenous exact 24/8 source coverage required')
            means=[sum(vs[i:i+3])/3 for i in range(0,24,3)]
            for x,y in zip(means,ls):_same(x,y,'original LMP source vs SCED energy mean')
            exog[family][rid]=ls
    for row in core['reserves']['renewable_capacity'].values():
        require(row['participation']=='excluded_unverified' and not any(row['positive_mw'].values()) and not any(row['negative_mw'].values()),'qualified renewable incremental reserve unsupported; no silent loss of source capability')
    nonzero_re=any(v for family in ('wind_mw','solar_mw') for values in exog[family].values() for v in values)
    re_binding=model._gd_october_input_bridge_audit.get('capacity_accounting_binding')
    v4_binding=re_binding is not None and re_binding.get('schema_version')=='gd.october.fixed_renewable_absolute_capacity.v4'
    require(v4_choice==v4_binding,'V4 source requires explicit new choice schema; legacy V2/zero choice cannot relabel it')
    require(not nonzero_re or (re_binding is not None and 'capacity_accounting' in core['reserves']),'nonzero fixed renewable requires original verified V2 or explicit V4 ledger and base; no implicit zeroing')
    eligibility=choice['pricing_eligibility'];obj(eligibility,{'thermal','battery','pumped'},'pricing eligibility')
    for family in eligibility:
        obj(eligibility[family],resources[family],'complete pricing eligibility')
        for uid,values in eligibility[family].items():
            require(type(values)is list and len(values)==8,'eight pricing eligibility values required')
            for v in values:boolean(v,'pricing eligibility')
    obj(choice['parameters'],PRICE_PARAMETERS,'explicit seven pricing inputs')
    for name,value in choice['parameters'].items():
        obj(value,{'value','source'},'pricing parameter');num(value['value'],name,0);c._text(value['source'],'parameter source')
    obj(choice['network_slack_limit_mw'],{'lines','sections'},'network limits')
    for family in ('lines','sections'):
        obj(choice['network_slack_limit_mw'][family],core[family],family+' complete slack limit inventory')
        for v in choice['network_slack_limit_mw'][family].values():vector(v,'network slack limit',0)
    vector(choice['accident_slack_limit_mw'],'accident slack limit',0)
    if core['reserves']['contingency_slack']['policy']=='hard':require(not any(choice['accident_slack_limit_mw']),'source hard accident reserve cannot be implicitly relaxed')
    # No policy values, capabilities, bids or source parameters are inferred.
    source_artifacts=dict(bridge['source_artifacts']);prefix='r10d-native-pricing:'
    require(not any(k.startswith(prefix) for k in source_artifacts),'reserved bridge source namespace collision')
    def put(name,value):
        sid=prefix+name;raw=value if type(value)is bytes else canonical(value);source_artifacts[sid]=raw
        return dict(source_id=sid,source_sha256=sha256(raw).hexdigest())
    def head(schema,**fields):return dict(schema_version=schema,identity=identity,published_at=_iso(p),available_at=_iso(a),classification='synthetic_research',**fields)
    def sh(schema,**fields):return head(schema,origin=ORIGIN,**fields)
    choice_ref=put('choice',choice_document);rule_ref=put('rule',rule_bytes)
    inventory_ref=put('inventory',head('gd.october.pricing_inventory.v2',families=resources,absence_reasons=choice['absence_reasons']))
    intervals=c._grid(w.delivery_start,15,8);steps=data['steps'];thermal={};power={};states={};initial_on={}
    for g in resources['thermal']:
        d=core['thermal'][g];on=initial['thermal'][g]['online'];initial_on[g]=bool(on)
        require(d['initial_state_kind']=='stable' and all(st['states']['thermal'][g]==on for st in steps),'stable thermal full window including initial required')
        require(d['bid_base_mw']==0 and all(v==0 for v in d['base_bid_yuan_per_h'].values()),'nonzero thermal bid base/fee requires unsupported explicit curve conversion')
        require(1<=len(d['segments'])<=10,'thermal complete 1..10 source segments required')
        _zero(core['agc']['thermal'][g]['up_mw'],'thermal AGC');_zero(core['agc']['thermal'][g]['down_mw'],'thermal AGC')
        require(all(v is None for v in d['fixed_power_mw'].values()),'source fixed thermal schedule requires separately supported pricing semantics')
        for key in ('min_mw','max_mw','ramp_up_mw_per_h','ramp_down_mw_per_h'):_quarters(d[key],g+'.'+key)
        rr=core['reserves']['thermal'][g]
        for key in ('primary_eligible','primary_fraction','contingency_eligible','ramp_10min_mw'):_quarters(rr[key],g+'.'+key)
        for seg in d['segments']:_quarters(seg['price_yuan_per_mwh'],g+' source bids')
        rows=[]
        for t in range(8):
            i=3*t+1
            rows.append(dict(minimum_mw=d['min_mw'][i] if on else 0.,maximum_mw=d['max_mw'][i] if on else 0.,agc_up_mw=0.,agc_down_mw=0.,
                pricing_eligible=eligibility['thermal'][g][t],ramp_up_mw_h=d['ramp_up_mw_per_h'][i],ramp_down_mw_h=d['ramp_down_mw_per_h'][i],
                bids=[dict(width_mw=s['width_mw'],yuan_per_mwh=s['price_yuan_per_mwh'][i]) for s in d['segments']],positive_eligible=bool(on),negative_eligible=bool(on),
                primary_eligible=bool(rr['primary_eligible'][i]) and bool(on),primary_fraction=rr['primary_fraction'][i],accident_eligible=bool(rr['contingency_eligible'][i]) and bool(on),accident_ramp_10min_mw=rr['ramp_10min_mw'][i]))
        thermal[g]=dict(node=d['bus'],initial_power_mw=initial['thermal'][g]['power_mw'],rows=rows)
        power[g]=[dict(start=st['delivery_start'],end=st['delivery_end'],mw=st['powers_mw']['thermal'][g]) for st in steps]
        states[g]=[dict(start=st['delivery_start'],end=st['delivery_end'],online=bool(st['states']['thermal'][g]),transition=False) for st in steps]
    power_ref=put('thermal-reference',head('gd.october.pricing_reference.v1',stage='RT',mapping='energy_mean_24x5',rows=power))
    state_ref=put('thermal-states',head('gd.october.pricing_states.v1',stage='RT',rows=states,initial=initial_on))
    storage_ref=_storage_sources(storage,units,initial,native_inputs.prepared,steps,eligibility,put,sh,intervals)
    reserves=core['reserves']['requirements']
    reserve_values={key:([sum(v[i+j] for j in range(3))/3 for i in range(1,25,3)]
        if v4_choice and key=='negative_demand_mw' else _quarters(v,'reserve '+key)) for key,v in reserves.items()}
    rv=dict(families={f:dict(status='active',reason='Explicit source reserve family retained including zero requirement') for f in ('positive','negative','primary','accident')},
        positive_mw=reserve_values['positive_mw'],positive_network_deduction_mw=reserve_values['positive_network_deduction_mw'],
        positive_demand_baseline_mw=[sum(v[t]for v in exog['load_mw'].values())for t in range(8)],
        negative_mw=reserve_values['negative_mw'],negative_demand_mw=reserve_values['negative_demand_mw'],primary_mw=reserve_values['primary_mw'],
        accident_mw=reserve_values['contingency_mw'],accident_deduction_mw=reserve_values['contingency_deduction_mw'],accident_slack_limit_mw=choice['accident_slack_limit_mw'])
    networks={}
    for family in ('lines','sections'):
        networks[family]={}
        for uid,row in sorted(core[family].items()):
            target=dict(lower_mw=_quarters(row['lower_mw'],uid+' lower'),upper_mw=_quarters(row['upper_mw'],uid+' upper'),slack_limit_mw=choice['network_slack_limit_mw'][family][uid])
            if family=='lines':target.update(from_node=row['from_bus'],to_node=row['to_bus'],susceptance_mw_per_rad=row['susceptance_mw_per_rad'])
            else:target['line_weights']=row['line_weights']
            networks[family][uid]=target
    choices=dict(CHOICES,storage_policy='stable_native_storage_v3_research',reference_stage='RT',power_mapping='energy_mean_24x5')
    spec=head(NUMERIC_SCHEMA,choices=choices,quarters=[dict(start=_iso(a),end=_iso(b)) for a,b in intervals],inventory_source=inventory_ref,
        power_reference_source=power_ref,state_source=state_ref,nodes=sorted(core['buses']),reference_node=core['reference_buses'][0],series_nodes=series_nodes,thermal=thermal,
        renewable_reserve={rid:dict(positive_capacity_mw=vals,negative_minimum_mw=vals,basis='fixed_output_absolute_baseline_zero_incremental_reserve') for family in ('wind_mw','solar_mw') for rid,vals in exog[family].items()},
        lines=networks['lines'],sections=networks['sections'],reserves=rv,tie_priority_penalty_yuan_per_mwh=choice['parameters']['tie_priority_penalty_yuan_per_mwh']['value'],storage_source=storage_ref)
    numeric_ref=put('numeric',spec)
    policy=dict(schema_version='gd.october.pricing_policy.v1',identity=identity,rule_source=rule_ref,classification='research',published_at=_iso(p),available_at=_iso(a),
        parameters={name:dict(value=choice['parameters'][name]['value'],kind='research',source=choice['parameters'][name]['source'],missing_reason=None) for name in PARAMETERS},
        decisions={name:dict(status='research_choice',description='Native ED bridge restricted research interpretation: '+description,source_ref=choice_ref) for name,description in DECISIONS.items()})
    policy_ref=put('policy',policy)
    # Source roles remain separate even when effective states are confirmed by ED.
    observation=next(o for o in request.available_observations if o.state_id==native_inputs.prepared.projection.observation_id)
    carry={uid:dict(seed_state_kind=observation.kind,seed_as_of=_iso(observation.as_of),seed_available_at=_iso(observation.available_at),
        seed_cumulative_mwh=observation.values['batteries'][uid]['cumulative_throughput_mwh'],seed_by_day=observation.values['batteries'][uid]['throughput_mwh_by_day'],
        projected_cumulative_mwh=v['cumulative_throughput_mwh'],projected_by_day=v['throughput_mwh_by_day'],
        planned_increment_mwh=v['cumulative_throughput_mwh']-observation.values['batteries'][uid]['cumulative_throughput_mwh'],
        executed_carry_verified=False,projection_is_execution=False) for uid,v in initial['batteries'].items()}
    audit=dict(reference_origin=ORIGIN,source_contract_identity=identity,ed_selected_identity=ed_identity,prepared_sha256=prepared_hash,
        **{key:before[key]for key in ('input_bundle_sha256','solve_intent_sha256','physical_model_sha256')},
        ed_native_call_verified=True,ed_receipt_process_local=True,source_authenticity_verified=False,operational_mapping_verified=False,
        source_uses_future_actual=prov['uses_future_actual'],source_input_causal_eligible=prov['input_causal_eligible'],source_derived_simulation=prov['derived_simulation'],source_input_mode=prov['input_mode'],
        ed_publication_clock_basis=prov['publication_clock_basis'],logical_ed_published_at=data['published_at'],logical_ed_available_at=data['available_at'],
        logical_mapping_published_at=_iso(p),logical_mapping_available_at=_iso(a),logical_lmp_cutoff=_iso(cutoff),logical_asof=_iso(asof),
        actual_ed_started_at=prov['invocation_started_at'],actual_ed_completed_at=prov['invocation_completed_at'],timely_operational_execution_verified=False,
        storage_capacity_v3=sa,renewable_capacity_v2=re_binding if not v4_binding else None,
        **({'renewable_capacity_v4':re_binding} if v4_binding else {}),
        renewable_accounting_applicability='verified_general_research_V4' if v4_binding else 'verified_V2' if re_binding else 'explicit_zero_no_nonzero_renewable',
        effective_state_origin='prepared_effective_plan_verified_against_ED_not_native_SCUC',carry=carry,
        original_source_sha256={sid:sha256(raw).hexdigest()for sid,raw in sorted(bridge['source_artifacts'].items())},choice_sha256=sha256(choice_document).hexdigest(),
        execution_confirmed=False,official_price=None,settlement_available=False)
    args=dict(pricing_policy_source=policy_ref,numerical_spec_source=numeric_ref,expected_identity=identity,
        expected_delivery_start=bridge['expected_delivery_start'],asof=_iso(asof),source_artifacts=source_artifacts)
    return bridge['contract_document'],args,audit



def _materialize_snapshot(native_inputs,solution,choice_document,rule_bytes):
    from .gd_october_native_snapshot import validate_october_native_snapshot, finalize_snapshot_pricing_choice, FLAGS
    from .gd_october_pricing_thermal_v4 import derive_thermal_pricing_rows
    require(snapshot_bridge_path_intact is _ORIGINAL_SNAPSHOT_BRIDGE_GUARD and snapshot_bridge_path_intact(),'replaced native source path')
    require(type(choice_document)is bytes and type(rule_bytes)is bytes,'original bytes required')
    require(sha256(rule_bytes).hexdigest()==REGION_SHA256,'wrong October rule bytes')
    data,ed_identity,context=validate_october_native_snapshot(solution,native_inputs)
    require(choice_document==finalize_snapshot_pricing_choice(native_inputs,solution),'post-ED choice may add only selected identity; policy mutation requires new experiment and ED')
    choice=c._document(choice_document);prov=solution.provenance
    model=context['model'];before=context['before'];bridge=context['bridge'];w=context['window']
    contract=c._document(bridge['contract_document']);identity=bridge['expected_identity']
    cutoff=c._time(contract['information_cutoff']['lmp'],'LMP cutoff');asof=c._time(choice['asof'],'asof')
    p,a=(c._time(choice[k],k)for k in ('published_at','available_at'))
    core=bridge['physical_inputs']['model_inputs'];initial=context['initial'];v4_choice=False
    require(core['balance_policy']=='hard','native pricing bridge requires source hard balance; no silent soft-to-hard change')
    require(core['renewable_mode']=='fixed_exogenous','dispatchable renewable mapping unsupported')
    require(not core['reserves']['load_side'],'load-side resources unsupported; cannot discard')
    require(len(core['reference_buses'])==1,'one connected network reference required')
    storage=core['storage_spec'];require(storage is not None,'native storage bridge requires declared full storage inventory')
    sa=model._gd_october_input_bridge_audit.get('storage_capacity_accounting_binding')
    require(sa is not None and sa['exact_delta_verified'] is True and 'storage_capacity_accounting' in core,'verified source storage V3 required; no legacy receipt upgrade')
    resources=deepcopy(choice['inventory']);families=FAMILIES+('pumped_plants',)
    obj(resources,families,'explicit full resource inventory');obj(choice['absence_reasons'],families,'explicit absence reasons')
    for family,ids in resources.items():
        c._resources(ids,family)
        require(ids==sorted(ids),'deterministic sorted inventory required')
        require((ids and choice['absence_reasons'][family] is None) or (not ids and isinstance(choice['absence_reasons'][family],str) and choice['absence_reasons'][family].strip()),'inventory absence declaration mismatch')
    require(len(sum(resources.values(),[]))==len(set(sum(resources.values(),[]))),'globally unique resource IDs required')
    for family in ('hydro','groups','load_side_reserve'):require(not resources[family],family+': actual unsupported inventory cannot disappear')
    units={u:d for plant in storage['pumped'].values() for u,d in plant['units'].items()}
    for family,ids in [('thermal',core['thermal']),('battery',storage['batteries']),('pumped',units),('pumped_plants',storage['pumped'])]:
        require(set(resources[family])==set(ids),'full source '+family+' inventory mismatch')
    exog={};series_nodes={}
    physical_map=c._document(bridge['source_artifacts'][bridge['resource_mapping_ref']['source_id']])
    for family,target in [('load_mw',None),('wind_mw','wind'),('solar_mw','solar'),('boundary_tie_mw','tie')]:
        raw={stage:next((c._document(bridge['source_artifacts'][r['source_id']]) for r in contract['series_sources'] if c._document(bridge['source_artifacts'][r['source_id']])['stage']==stage and c._document(bridge['source_artifacts'][r['source_id']])['family']==family),None) for stage in ('sced','lmp')}
        require(raw['sced'] is not None and raw['lmp'] is not None,'explicit zero/absent exogenous source envelope required')
        require(raw['sced']['resource_ids']==raw['lmp']['resource_ids'],'SCED/LMP resource inventory differs')
        if target:require(set(resources[target])==set(raw['sced']['resource_ids']),'full exogenous '+family+' inventory mismatch')
        bindings={r['source_resource_id']:r['bus'] for r in physical_map['rows'] if r['family']==family}
        require(set(bindings)==set(raw['sced']['resource_ids']),'physical exogenous node coverage mismatch')
        series_nodes[family]=bindings;exog[family]={}
        for rid in raw['sced']['resource_ids']:
            vs=[r['value'] for r in sorted(raw['sced']['rows'],key=lambda r:c._time(r['delivery_start'],'source interval')) if r['resource_id']==rid]
            ls=[r['value'] for r in sorted(raw['lmp']['rows'],key=lambda r:c._time(r['delivery_start'],'source interval')) if r['resource_id']==rid]
            require(len(vs)==24 and len(ls)==8,'exogenous exact 24/8 source coverage required')
            means=[sum(vs[i:i+3])/3 for i in range(0,24,3)]
            for x,y in zip(means,ls):_same(x,y,'original LMP source vs SCED energy mean')
            exog[family][rid]=ls
    for row in core['reserves']['renewable_capacity'].values():
        require(row['participation']=='excluded_unverified' and not any(row['positive_mw'].values()) and not any(row['negative_mw'].values()),'qualified renewable incremental reserve unsupported; no silent loss of source capability')
    nonzero_re=any(v for family in ('wind_mw','solar_mw') for values in exog[family].values() for v in values)
    re_binding=model._gd_october_input_bridge_audit.get('capacity_accounting_binding')
    v4_binding=re_binding is not None and re_binding.get('schema_version')=='gd.october.fixed_renewable_absolute_capacity.v4'
    require(v4_choice==v4_binding,'V4 source requires explicit new choice schema; legacy V2/zero choice cannot relabel it')
    require(not nonzero_re or (re_binding is not None and 'capacity_accounting' in core['reserves']),'nonzero fixed renewable requires original verified V2 or explicit V4 ledger and base; no implicit zeroing')
    eligibility=choice['pricing_eligibility'];obj(eligibility,{'thermal','battery','pumped'},'pricing eligibility')
    for family in eligibility:
        obj(eligibility[family],resources[family],'complete pricing eligibility')
        for uid,values in eligibility[family].items():
            require(type(values)is list and len(values)==8,'eight pricing eligibility values required')
            for v in values:boolean(v,'pricing eligibility')
    obj(choice['parameters'],PRICE_PARAMETERS,'explicit seven pricing inputs')
    for name,value in choice['parameters'].items():
        obj(value,{'value','source'},'pricing parameter');num(value['value'],name,0);c._text(value['source'],'parameter source')
    obj(choice['network_slack_limit_mw'],{'lines','sections'},'network limits')
    for family in ('lines','sections'):
        obj(choice['network_slack_limit_mw'][family],core[family],family+' complete slack limit inventory')
        for v in choice['network_slack_limit_mw'][family].values():vector(v,'network slack limit',0)
    vector(choice['accident_slack_limit_mw'],'accident slack limit',0)
    if core['reserves']['contingency_slack']['policy']=='hard':require(not any(choice['accident_slack_limit_mw']),'source hard accident reserve cannot be implicitly relaxed')
    # No policy values, capabilities, bids or source parameters are inferred.
    source_artifacts=dict(bridge['source_artifacts']);prefix='r10f-snapshot-pricing:'
    require(not any(k.startswith(prefix) for k in source_artifacts),'reserved bridge source namespace collision')
    def put(name,value):
        sid=prefix+name;raw=value if type(value)is bytes else canonical(value);source_artifacts[sid]=raw
        return dict(source_id=sid,source_sha256=sha256(raw).hexdigest())
    def head(schema,**fields):return dict(schema_version=schema,identity=identity,published_at=_iso(p),available_at=_iso(a),classification='synthetic_research',**fields)
    def sh(schema,**fields):return head(schema,origin=ORIGIN,**fields)
    choice_ref=put('choice',choice_document);rule_ref=put('rule',rule_bytes)
    inventory_ref=put('inventory',head('gd.october.pricing_inventory.v2',families=resources,absence_reasons=choice['absence_reasons']))
    intervals=c._grid(w.delivery_start,15,8);steps=data['steps']
    thermal,power,states,thermal_audit=derive_thermal_pricing_rows(bridge,model,steps,context['eligibility'])
    require(sorted(thermal_audit['active_event_ids'])==resources['transitions'],'active transitions cannot disappear')
    initial_on={g:bool(d['initial_online'])for g,d in core['thermal'].items()}
    put('experiment-policy',native_inputs.experiment.policy_document)
    source_artifacts[choice['eligibility_source']['source_id']]=native_inputs.experiment.eligibility_document
    power_ref=put('thermal-reference',head('gd.october.pricing_reference.v1',stage='RT',mapping='energy_mean_24x5',rows=power))
    state_ref=put('thermal-states',head('gd.october.pricing_states.v2',stage='RT',rows=states,initial=initial_on))
    storage_ref=_storage_sources_explicit(storage,units,initial,w.delivery_start,w.delivery_end,context['terminal'],steps,eligibility,put,sh,intervals)
    reserves=core['reserves']['requirements']
    reserve_values={key:([sum(v[i+j] for j in range(3))/3 for i in range(1,25,3)]
        if key=='negative_demand_mw' else _quarters(v,'reserve '+key)) for key,v in reserves.items()}
    rv=dict(families={f:dict(status='active',reason='Explicit source reserve family retained including zero requirement') for f in ('positive','negative','primary','accident')},
        positive_mw=reserve_values['positive_mw'],positive_network_deduction_mw=reserve_values['positive_network_deduction_mw'],
        positive_demand_baseline_mw=[sum(v[t]for v in exog['load_mw'].values())for t in range(8)],
        negative_mw=reserve_values['negative_mw'],negative_demand_mw=reserve_values['negative_demand_mw'],primary_mw=reserve_values['primary_mw'],
        accident_mw=reserve_values['contingency_mw'],accident_deduction_mw=reserve_values['contingency_deduction_mw'],accident_slack_limit_mw=choice['accident_slack_limit_mw'])
    networks={}
    for family in ('lines','sections'):
        networks[family]={}
        for uid,row in sorted(core[family].items()):
            target=dict(lower_mw=_quarters(row['lower_mw'],uid+' lower'),upper_mw=_quarters(row['upper_mw'],uid+' upper'),slack_limit_mw=choice['network_slack_limit_mw'][family][uid])
            if family=='lines':target.update(from_node=row['from_bus'],to_node=row['to_bus'],susceptance_mw_per_rad=row['susceptance_mw_per_rad'])
            else:target['line_weights']=row['line_weights']
            networks[family][uid]=target
    choices=dict(CHOICES,storage_policy='stable_native_storage_v3_research',reference_stage='RT',power_mapping='energy_mean_24x5',bids='source_Pmin_base_plus_incremental_segments',state_mapping='homogeneous_three_steps_exact_source_phase')
    spec=head('gd.october.pricing_numeric.v4',choices=choices,quarters=[dict(start=_iso(a),end=_iso(b)) for a,b in intervals],inventory_source=inventory_ref,
        power_reference_source=power_ref,state_source=state_ref,nodes=sorted(core['buses']),reference_node=core['reference_buses'][0],series_nodes=series_nodes,thermal=thermal,
        renewable_reserve={rid:dict(positive_capacity_mw=vals,negative_minimum_mw=vals,basis='fixed_output_absolute_baseline_zero_incremental_reserve') for family in ('wind_mw','solar_mw') for rid,vals in exog[family].items()},
        lines=networks['lines'],sections=networks['sections'],reserves=rv,tie_priority_penalty_yuan_per_mwh=choice['parameters']['tie_priority_penalty_yuan_per_mwh']['value'],storage_source=storage_ref)
    numeric_ref=put('numeric',spec)
    policy=dict(schema_version='gd.october.pricing_policy.v1',identity=identity,rule_source=rule_ref,classification='research',published_at=_iso(p),available_at=_iso(a),
        parameters={name:dict(value=choice['parameters'][name]['value'],kind='research',source=choice['parameters'][name]['source'],missing_reason=None) for name in PARAMETERS},
        decisions={name:dict(status='research_choice',description='Native ED bridge restricted research interpretation: '+description,source_ref=choice_ref) for name,description in DECISIONS.items()})
    policy_ref=put('policy',policy)
    audit=dict(reference_origin='original_sced_snapshot_price_only_research',source_contract_identity=identity,ed_selected_identity=ed_identity,
        **{key:before[key]for key in ('input_bundle_sha256','solve_intent_sha256','physical_model_sha256')},
        ed_native_call_verified=True,ed_receipt_process_local=True,operational_mapping_verified=False,
        source_uses_future_actual=prov['uses_future_actual'],source_input_causal_eligible=prov['input_causal_eligible'],source_derived_simulation=prov['derived_simulation'],source_input_mode='research',
        ed_publication_clock_basis=prov['publication_clock_basis'],logical_ed_published_at=data['published_at'],logical_ed_available_at=data['available_at'],
        logical_mapping_published_at=_iso(p),logical_mapping_available_at=_iso(a),logical_lmp_cutoff=_iso(cutoff),logical_asof=_iso(asof),
        actual_ed_started_at=prov['invocation_started_at'],actual_ed_completed_at=prov['invocation_completed_at'],timely_operational_execution_verified=False,
        storage_capacity_v3=sa,renewable_capacity_v2=re_binding,renewable_accounting_applicability='verified_V2',
        effective_state_origin='original_independent_research_declaration_not_native_SCUC',
        carry_basis='original_research_declaration',carry=initial['batteries'],experiment=context['experiment'],thermal_source_audit=thermal_audit,
        original_source_sha256={sid:sha256(raw).hexdigest()for sid,raw in sorted(bridge['source_artifacts'].items())},choice_sha256=sha256(choice_document).hexdigest(),
        original_ed_cost=prov['objective_yuan'],price_model_cost_at_ed_mean=thermal_audit['price_model_cost_at_ed_mean'],
        aggregation_cost_gap=prov['objective_yuan']-thermal_audit['price_model_cost_at_ed_mean'],
        pricing_ramp_basis='quarter_mean_research_not_exact_5min_projection',
        ramp_domain_limits=dict(original_C1_first_mean_max_mw=547.,pricing_C1_first_mean_max_mw=595.5,source_5min_endpoint_replay_retained=True),
        slack_domain_limits=dict(original_ED_line='nonnegative_unbounded',pricing_line_caps_mw=choice['network_slack_limit_mw'],domain_equivalent=False,penalty_is_price_cap=False),
        **FLAGS,settlement_available=False)
    args=dict(pricing_policy_source=policy_ref,numerical_spec_source=numeric_ref,expected_identity=identity,
        expected_delivery_start=bridge['expected_delivery_start'],asof=_iso(asof),source_artifacts=source_artifacts)
    return bridge['contract_document'],args,audit


def _storage_sources(storage,units,initial,prepared,steps,eligibility,put,sh,intervals):
    from .gd_october_pricing_storage import POLICIES,SERVICES
    return _storage_sources_explicit(storage,units,initial,prepared.window.delivery_start,prepared.window.delivery_end,prepared.terminal.inventories,steps,eligibility,put,sh,intervals)


def _storage_sources_explicit(storage,units,initial,start,end,terminal_values,steps,eligibility,put,sh,intervals):
    from .gd_october_pricing_storage import POLICIES,SERVICES
    points=[start]+[b for a,b in intervals]
    batteries={};pumped={};plants={};b_boundary={};p_boundary={};initial_power={};modes={};initial_modes={}
    for family,source in [('battery',storage['batteries']),('pumped',units)]:
        for uid,d in sorted(source.items()):
            values=initial['batteries' if family=='battery' else 'pumped_units'][uid];mode=values['mode'];modes[uid]=mode
            require(all(st['states']['batteries' if family=='battery' else 'pumped_units'][uid]==mode for st in steps),'stable storage initial and full-window modes required')
            require(not d['effective_plan']['transition_authorizations'],'storage transitions unsupported')
            initial_modes[uid]=dict(mode=mode,mode_elapsed_h=values['mode_elapsed_h']);initial_power[uid]=dict(at=_iso(start),mw=values['net_power_mw'])
            if family=='battery':
                require(d['reserve_eligibility']=='excluded_unverified','incremental battery services unsupported')
                _zero(d['agc_award']['up_mw'],'battery AGC');_zero(d['agc_award']['down_mw'],'battery AGC')
                keys=('charge_min_mw','charge_max_mw','discharge_min_mw','discharge_max_mw','charge_bid_yuan_per_mwh','discharge_bid_yuan_per_mwh','ramp_up_mw_per_h','ramp_down_mw_per_h')
            else:
                require(not any(d['operating_reserve_eligible'].values()) and not any(d['primary_eligible'].values()),'source pumped incremental services unsupported; do not drop qualification')
                for key in ('agc_up_mw','agc_down_mw','standby_available_mw'):_zero(d[key],key)
                keys=('pump_power_mw','generation_min_mw','generation_max_mw','pump_bid_yuan_per_mwh','generation_bid_yuan_per_mwh','pump_m3_per_mwh','generation_m3_per_mwh','ramp_up_mw_per_h','ramp_down_mw_per_h')
            q={key:_quarters(d[key],uid+'.'+key)for key in keys};rows=[]
            for t in range(8):
                lo,hi,dl,dh=(q[k][t]for k in ('charge_min_mw','charge_max_mw','discharge_min_mw','discharge_max_mw')) if family=='battery' else (q['pump_power_mw'][t],q['pump_power_mw'][t],q['generation_min_mw'][t],q['generation_max_mw'][t])
                cp=q['charge_bid_yuan_per_mwh' if family=='battery' else 'pump_bid_yuan_per_mwh'][t];dp=q['discharge_bid_yuan_per_mwh' if family=='battery' else 'generation_bid_yuan_per_mwh'][t]
                r=dict(charge_min_mw=lo,charge_max_mw=hi,discharge_min_mw=dl,discharge_max_mw=dh,
                    charge_bids=[] if hi==0 else [dict(width_mw=hi,yuan_per_mwh=cp)],discharge_bids=[] if dh==0 else [dict(width_mw=dh,yuan_per_mwh=dp)],
                    pricing_eligible=eligibility[family][uid][t],agc_up_mw=0.,agc_down_mw=0.,ramp_up_mw_h=q['ramp_up_mw_per_h'][t],ramp_down_mw_h=q['ramp_down_mw_per_h'][t])
                if family=='pumped':r.update(pump_m3_per_mwh=q['pump_m3_per_mwh'][t],generate_m3_per_mwh=q['generation_m3_per_mwh'][t])
                rows.append(r)
            target=dict(node=d['bus'],rows=rows,services={k:False for k in SERVICES})
            if family=='battery':
                lo=_quarters(d['energy_min_mwh'],uid+' energy min');hi=_quarters(d['energy_max_mwh'],uid+' energy max')
                eta=d['roundtrip_efficiency']**.5
                target.update(rated_energy_mwh=d['energy_capacity_mwh'],roundtrip_efficiency=d['roundtrip_efficiency'],eta_charge=eta,eta_discharge=eta,
                    energy_bounds=[dict(at=_iso(at),minimum_mwh=lo[max(0,i-1)],maximum_mwh=hi[max(0,i-1)]) for i,at in enumerate(points)])
                b_boundary[uid]=dict(initial=dict(at=_iso(start),energy_mwh=values['energy_mwh']),terminal=dict(at=_iso(end),lower_mwh=d['final_energy_mwh'],upper_mwh=d['final_energy_mwh']),
                    cycle_budgets={day:dict(used_as_of=_iso(start),limit_cycles=r['limit_cycles'],used_internal_throughput_mwh=r['used_throughput_mwh']) for day,r in sorted(d['cycle_budgets'].items())})
                batteries[uid]=target
            else:
                target['plant_id']=next(pid for pid,p in storage['pumped'].items()if uid in p['units']);pumped[uid]=target
    for pid,d in sorted(storage['pumped'].items()):
        require(not any(d['contingency_eligible'].values()),'source plant accident eligibility unsupported; do not silently remove service')
        fields={key:_quarters(d[key],pid+'.'+key)for key in ('upper_min_m3','upper_max_m3','lower_min_m3','lower_max_m3','da_upper_target_m3','da_lower_target_m3','upper_band_m3','lower_band_m3')}
        target=dict(unit_ids=sorted(d['units']),external_upper_m3_h=[0.]*8,external_lower_m3_h=[0.]*8,
            target_rows=[dict(at=_iso(b),upper_target_m3=fields['da_upper_target_m3'][t],lower_target_m3=fields['da_lower_target_m3'][t],upper_width_m3=fields['upper_band_m3'][t],lower_width_m3=fields['lower_band_m3'][t])for t,(a,b)in enumerate(intervals)])
        for side in ('upper','lower'):target[side+'_bounds']=[dict(at=_iso(at),minimum_m3=fields[side+'_min_m3'][max(0,i-1)],maximum_m3=fields[side+'_max_m3'][max(0,i-1)])for i,at in enumerate(points)]
        plants[pid]=target
        p_boundary[pid]=dict(initial=dict(at=_iso(start),upper_m3=initial['pumped_plants'][pid]['upper_m3'],lower_m3=initial['pumped_plants'][pid]['lower_m3']))
        for side in ('upper','lower'):
            v=terminal_values[side+'_volume_m3'][pid];p_boundary[pid][side+'_terminal']=dict(at=_iso(end),lower_m3=v,upper_m3=v)
    state_ref=put('storage-states',sh('gd.october.pricing_storage_states.v1',stage='RT',initial_at=_iso(start),initial=initial_modes,
        rows=[dict(start=s['delivery_start'],end=s['delivery_end'],modes={**s['states']['batteries'],**s['states']['pumped_units']},transition_active={u:False for u in sorted(modes)})for s in steps]))
    boundary_ref=put('storage-boundary',sh('gd.october.pricing_storage_boundary.v1',batteries=b_boundary,pumped_plants=p_boundary,initial_power=initial_power))
    rows=[]
    for s in steps:
        pp=s['powers_mw'];inv=s['end_inventory']
        rows.append(dict(start=s['delivery_start'],end=s['delivery_end'],power_mw={**{u:pp['battery_charge'][u]+pp['battery_discharge'][u]for u in sorted(batteries)},**{u:pp['pumped_generation'][u]-pp['pumped_pumping'][u]for u in sorted(pumped)}},
            battery_energy_end_mwh=inv['battery_energy_mwh'],upper_end_m3=inv['upper_volume_m3'],lower_end_m3=inv['lower_volume_m3']))
    reference_ref=put('storage-reference',sh('gd.october.pricing_storage_reference.v1',stage='RT',grid='twenty_four_five_minute',
        initial_inventory=dict(at=_iso(start),battery_energy_mwh={u:v['energy_mwh']for u,v in initial['batteries'].items()},upper_m3={p:v['upper_m3']for p,v in initial['pumped_plants'].items()},lower_m3={p:v['lower_m3']for p,v in initial['pumped_plants'].items()}),rows=rows))
    slack_ref=None
    if plants:
        names=dict(upper_positive='upper_plus',upper_negative='upper_minus',lower_positive='lower_plus',lower_negative='lower_minus')
        slack_ref=put('storage-slacks',sh('gd.october.pricing_storage_slacks.v1',stage='RT',grid='twenty_four_five_minute',meaning='live_native_selected_ED_slacks',
            rows=[dict(at=s['delivery_end'],values={pid:{names[key]:value for key,value in vals.items()}for pid,vals in s['reservoir_sl'].items()})for s in steps]))
    return put('storage',sh(STORAGE_SCHEMA,choices=dict(POLICIES,reference_origin=ORIGIN,power_mapping='energy_mean_24x5',slack_mapping='constant_24x5'),
        reference_source=reference_ref,state_source=state_ref,boundary_source=boundary_ref,slack_source=slack_ref,batteries=batteries,pumped_units=pumped,pumped_plants=plants))


def _digest(document,args,audit):
    return sha256(dumps(dict(document=document,args=args,audit=audit))).hexdigest()

def assert_native_pricing_binding(reference,document=None,args=None):
    require(is_issued_native_reference(reference),'live issued native-pricing bridge required; JSON/archive cannot grant native origin')
    ni,request,solution,choice,rule,expected=_BINDINGS[reference]
    doc,rebuilt,audit=(_materialize_snapshot(ni,solution,choice,rule) if request is _SNAPSHOT_KIND else _materialize(ni,request,solution,choice,rule))
    require(_digest(doc,rebuilt,audit)==expected==reference.binding_sha256,'native bridge source, receipt, choice or mapping changed')
    if document is not None:
        require(document==doc and dumps(args)==dumps(rebuilt),'native derived pricing sources differ from live bridge output')
    return audit


def prepare_october_native_pricing_inputs(*,native_inputs,request,solution,choice_document,rule_bytes):
    from .gd_october_pricing_inputs import prepare_october_pricing_inputs
    document,args,audit=_materialize(native_inputs,request,solution,choice_document,rule_bytes)
    digest=_digest(document,args,audit);reference=NativePricingReference(digest)
    _BINDINGS[reference]=(native_inputs,request,solution,choice_document,rule_bytes,digest)
    return prepare_october_pricing_inputs(document,**args,native_reference=reference)


_SNAPSHOT_KIND=object()
def prepare_october_snapshot_pricing_inputs(*,native_inputs,solution,choice_document,rule_bytes):
    from .gd_october_pricing_inputs import prepare_october_pricing_inputs
    document,args,audit=_materialize_snapshot(native_inputs,solution,choice_document,rule_bytes)
    digest=_digest(document,args,audit);reference=NativePricingReference(digest)
    _BINDINGS[reference]=(native_inputs,_SNAPSHOT_KIND,solution,choice_document,rule_bytes,digest)
    return prepare_october_pricing_inputs(document,**args,native_reference=reference)


# Ordinary monkeypatches are test evidence, never native source authority.
from . import gd_october_native_solve as _native_module
from . import gd_october_roll as _roll_module
_SOURCE_FUNCTIONS=(
    ('validate_october_native_solution',_native_module,validate_october_native_solution),
    ('preflight_october_native_window',_native_module,preflight_october_native_window),
    ('map_october_native_to_pricing_report',_native_module,map_october_native_to_pricing_report),
    ('validate_prepared',_roll_module,validate_prepared),
)
from . import gd_october_native_snapshot as _snapshot_source_module
_SNAPSHOT_SOURCE_HELPERS=tuple((name,getattr(_snapshot_source_module,name))for name in tuple(_snapshot_source_module._HELPERS)+('snapshot_path_intact',))
_BRIDGE_HELPERS={name:globals()[name]for name in ('_materialize_snapshot','prepare_october_snapshot_pricing_inputs','_storage_sources_explicit','_digest')}
def native_bridge_path_intact():
    # Preserve the original D/E source-validator scope and failure precedence.
    # An intentionally mock-issued old ED reaches its own genuine-HiGHS gate.
    return all(globals()[name] is original and getattr(module,name) is original for name,module,original in _SOURCE_FUNCTIONS)


_ORIGINAL_LEGACY_BRIDGE_GUARD=native_bridge_path_intact

def snapshot_bridge_path_intact():
    # Snapshot authority additionally binds all thermal/source/backend helpers.
    # The snapshot entry and pricing solver independently pin this guard itself.
    return (snapshot_bridge_path_intact is _ORIGINAL_SNAPSHOT_BRIDGE_GUARD
        and native_bridge_path_intact is _ORIGINAL_LEGACY_BRIDGE_GUARD
        and native_bridge_path_intact()
        and all(getattr(_snapshot_source_module,name)is f for name,f in _SNAPSHOT_SOURCE_HELPERS)
        and _snapshot_source_module.snapshot_path_intact()
        and all(globals()[name]is f for name,f in _BRIDGE_HELPERS.items()))


_ORIGINAL_SNAPSHOT_BRIDGE_GUARD=snapshot_bridge_path_intact
