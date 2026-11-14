"""Executable, source-bound inputs for the independent research-only 8x15 LP.

No caller-authored validation report is accepted. The original 24/8 contract and
policy checker run first. Frozen values contain only bytes; live input sources
are re-read at every gate. This is traceability, not authentication of providers.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import timezone
from hashlib import sha256
import json
from math import isfinite
from weakref import WeakKeyDictionary

from . import gd_october_rt_contract as c
from .gd_october_pricing_rules import audit_pricing_input_contract, PARAMETERS

SCHEMA = 'gd.october.pricing_numeric.v1'
SCHEMA_V2 = 'gd.october.pricing_numeric.v2'
SCHEMA_V3 = 'gd.october.pricing_numeric.v3'
SCHEMA_V4 = 'gd.october.pricing_numeric.v4'
FAMILIES = ('thermal', 'wind', 'solar', 'tie', 'battery', 'pumped', 'hydro', 'groups', 'transitions', 'load_side_reserve')
CHOICES = dict(horizon='eight_full_quarters_research', state_mapping='constant_three_steps_reject_mixed',
    storage_policy='reject_present_until_supported', group_policy='reject_present_24x5_conflict',
    thermal_power_band='literal_relative_delta_nonnegative', bids='zero_origin_incremental_widths_convex_research', reserve_inheritance='positive_negative_primary_and_accident',
    reserve_sharing='independent_headroom_no_additive_award',
    reserve_agc_basis='headroom_net_agc_fraction_physical_max_research',
    fixed_renewable_reserve='absolute_baseline_zero_incremental_reserve', network='lossless_ptdf_connected',
    network_slack='translated_two_sided', load_slack='hard_zero', objective='yuan_0.25_once',
    dual_basis='raw_rhs_duals_normalize_by_0.25', publication='research_only_no_publication_no_cash')
_BINDINGS = WeakKeyDictionary()


def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def require(ok, text):
    if not ok:
        raise ValueError(text)


def num(x, label, minimum=None):
    require(type(x) in (int, float) and isfinite(x), label + ': finite numeric value required')
    require(minimum is None or x >= minimum, label + ': below minimum')
    return float(x)


def vector(x, label, minimum=None):
    require(type(x) is list and len(x) == 8, label + ': exact eight-quarter array required')
    return [num(v, label, minimum) for v in x]


def boolean(x, label):
    require(type(x) is bool, label + ': explicit Boolean required')
    return x


def obj(x, fields, label):
    c._object(x, fields, label)
    return x


def header(x, fields, schema, identity, cutoff, label):
    obj(x, set(fields) | {'schema_version','identity','published_at','available_at','classification'}, label)
    require(x['schema_version'] == schema, label + ': schema mismatch')
    c._identity(x['identity'], identity, label)
    require(x['classification'] == 'synthetic_research', label + ': explicit synthetic research provenance required')
    require(c._time(x['published_at'], label) <= c._time(x['available_at'], label) <= cutoff, label + ': unavailable at cutoff')


@dataclass(frozen=True, eq=False)
class OctoberPricingInputs:
    normalized_bytes: bytes
    input_sha256: str

    @property
    def data(self):
        return json.loads(self.normalized_bytes)

    def __deepcopy__(self, memo):
        return self


def _prepare(document, *, pricing_policy_source, numerical_spec_source, expected_identity,
             expected_delivery_start, asof, source_artifacts, native_reference=None):
    audit = audit_pricing_input_contract(document, pricing_policy_source=pricing_policy_source,
        expected_identity=expected_identity, expected_delivery_start=expected_delivery_start,
        asof=asof, source_artifacts=source_artifacts, mode='research')
    require(audit['status'] == 'declared_inputs_complete_not_model_ready',
            'original contract/policy rejected: ' + str(audit.get('issues') or audit.get('missing') or audit['input_contract']['issues']))
    doc = c._document(document)
    cutoff = c._time(doc['information_cutoff']['lmp'], 'LMP cutoff')
    require(doc['active_sced_start_index'] == 0, 'first slice requires eight complete active quarters; no partial active horizon')
    used = dict(audit['input_contract']['source_sha256']); used.update(audit['policy_source_hashes'])
    source = lambda ref,label: c._source(ref,source_artifacts,used,label)
    spec = source(numerical_spec_source,'numeric')
    fields = {'choices','quarters','inventory_source','power_reference_source','state_source','nodes','reference_node',
        'series_nodes','thermal','renewable_reserve','lines','sections','reserves','tie_priority_penalty_yuan_per_mwh'}
    version=spec.get('schema_version')
    require(version in {SCHEMA,SCHEMA_V2,SCHEMA_V3,SCHEMA_V4},'unsupported numeric pricing schema')
    storage_v2=version in {SCHEMA_V2,SCHEMA_V3,SCHEMA_V4}
    native_audit=None
    if version in {SCHEMA_V3,SCHEMA_V4}:
        from .gd_october_pricing_native_bridge import assert_native_pricing_binding
        native_audit=assert_native_pricing_binding(native_reference,document,dict(pricing_policy_source=pricing_policy_source,
            numerical_spec_source=numerical_spec_source,expected_identity=expected_identity,expected_delivery_start=expected_delivery_start,
            asof=asof,source_artifacts=source_artifacts))
    else: require(native_reference is None,'native receipt requires distinct numeric V3 schema')
    if storage_v2:fields.add('storage_source')
    header(spec,fields,version,expected_identity,cutoff,'numeric')
    obj(spec['choices'],set(CHOICES)|{'reference_stage','power_mapping'},'choices')
    choices=dict(CHOICES)
    if storage_v2:choices['storage_policy']='stable_native_storage_v3_research' if native_audit else 'stable_authored_storage_v2_research'
    if version==SCHEMA_V4:choices.update(bids='source_Pmin_base_plus_incremental_segments',state_mapping='homogeneous_three_steps_exact_source_phase')
    for k,v in choices.items(): require(spec['choices'][k] == v,'unknown/unimplemented executable choice: '+k)
    stage=spec['choices']['reference_stage']; mapping=spec['choices']['power_mapping']
    require(stage in {'DA','RT'}, 'reference stage must explicitly be DA or RT research choice')
    require(mapping in {'native_eight_quarter','energy_mean_24x5'}, 'unsupported reference power mapping')
    intervals=c._grid(c._time(expected_delivery_start,'start'),15,8)
    require(type(spec['quarters']) is list and len(spec['quarters'])==8,'exact eight quarters required')
    for row,(a,b) in zip(spec['quarters'],intervals):
        obj(row,{'start','end'},'quarter')
        require(c._time(row['start'],'quarter')==a and c._time(row['end'],'quarter')==b,'quarter coverage mismatch')
        require((b.astimezone(timezone.utc)-a.astimezone(timezone.utc)).total_seconds()==900,'900-second quarters required')
    inv=source(spec['inventory_source'],'inventory')
    header(inv,{'families','absence_reasons'},'gd.october.pricing_inventory.v2' if storage_v2 else 'gd.october.pricing_inventory.v1',expected_identity,cutoff,'inventory')
    families=FAMILIES+('pumped_plants',) if storage_v2 else FAMILIES
    obj(inv['families'],families,'inventory.families'); obj(inv['absence_reasons'],families,'inventory.absence_reasons')
    all_ids=[]
    for family,ids in inv['families'].items():
        c._resources(ids,family); all_ids.extend(ids)
        require((ids and inv['absence_reasons'][family] is None) or
                (not ids and type(inv['absence_reasons'][family]) is str and inv['absence_reasons'][family].strip()),
                family+': explicit absence reason required only for absent family')
    require(len(all_ids)==len(set(all_ids)),'resource identities must be globally unique')
    unsupported=('hydro','groups','load_side_reserve') if version==SCHEMA_V4 else ('hydro','groups','transitions','load_side_reserve') if storage_v2 else ('battery','pumped','hydro','groups','transitions','load_side_reserve')
    for family in unsupported:
        require(not inv['families'][family],family+': present resources not yet implemented; cannot discard them')
    require(bool(inv['families']['thermal']),'first slice requires thermal resources')
    nodes=c._resources(spec['nodes'],'nodes'); require(bool(nodes),'nonempty nodes required')
    require(spec['reference_node'] in nodes,'unknown reference node')
    obj(spec['series_nodes'],{'load_mw','wind_mw','solar_mw','boundary_tie_mw'},'series_nodes')
    lmp={s['family']:s for s in audit['input_contract']['data']['series'] if s['stage']=='lmp'}
    for required in doc['required_series']:
        if required['stage']=='lmp' and not required['resource_ids']:
            lmp[required['family']]=dict(resource_ids=[],rows=[])
    require(set(lmp)==set(spec['series_nodes']),'all four exogenous families must be explicitly present/absent in base contract')
    loads={n:[0.]*8 for n in nodes}; fixed={n:[0.]*8 for n in nodes}
    exogenous={}
    for family,bindings in spec['series_nodes'].items():
        require(type(bindings) is dict and set(bindings)==set(lmp[family]['resource_ids']),family+': full source resource coverage required')
        require(all(n in nodes for n in bindings.values()),family+': unknown node')
        if family!='load_mw':
            target={'wind_mw':'wind','solar_mw':'solar','boundary_tie_mw':'tie'}[family]
            require(set(bindings)==set(inv['families'][target]),target+': inventory/source mismatch')
        exogenous[family]={rid:[None]*8 for rid in bindings}
        for row in lmp[family]['rows']:
            t=next(i for i,(a,b) in enumerate(intervals) if c._time(row['delivery_start'],'series')==a)
            rid=row['resource_id']; v=float(row['value']); exogenous[family][rid][t]=v
            (loads if family=='load_mw' else fixed)[bindings[rid]][t]+=v
    thermal=spec['thermal']; require(type(thermal) is dict and set(thermal)==set(inv['families']['thermal']),'thermal full inventory coverage required')
    power=source(spec['power_reference_source'],'reference_power')
    header(power,{'stage','mapping','rows'},'gd.october.pricing_reference.v1',expected_identity,cutoff,'reference_power')
    require(power['stage']==stage and power['mapping']==mapping,'reference stage/mapping mismatch')
    states=source(spec['state_source'],'selected_states')
    header(states,{'stage','rows','initial'},'gd.october.pricing_states.v2' if version==SCHEMA_V4 else 'gd.october.pricing_states.v1',expected_identity,cutoff,'selected_states')
    require(states['stage'] in {'DA','RT'},'state stage must independently be DA/RT')
    require(spec['power_reference_source']['source_id']!=spec['state_source']['source_id'],'reference power and selected state require independent source objects')
    for x,label in ((power['rows'],'reference power'),(states['rows'],'states'),(states['initial'],'initial')):
        require(type(x) is dict and set(x)==set(thermal),label+': resource coverage mismatch')
    reference={}; selected={}
    if version==SCHEMA_V4:
        require(native_audit['reference_origin']=='original_sced_snapshot_price_only_research','V4 requires distinct snapshot receipt, never old NativeWindow')
        require(stage=='RT' and mapping=='energy_mean_24x5','V4 exact source means required')
        for g,d in thermal.items():
            pr=power['rows'][g];require(type(pr)is list and len(pr)==24,'V4 full live power grid required')
            vals=[]
            for r,(a,b) in zip(pr,c._grid(intervals[0][0],5,24)):
                obj(r,{'start','end','mw'},'V4 reference row')
                require(c._time(r['start'],'power')==a and c._time(r['end'],'power')==b,'V4 power coverage mismatch')
                vals.append(num(r['mw'],'V4 reference MW',0))
            reference[g]=[sum(vals[i:i+3])/3 for i in range(0,24,3)]
            for r,(a,b) in zip(states['rows'][g],c._grid(intervals[0][0],5,24)):
                require(c._time(r['start'],'state')==a and c._time(r['end'],'state')==b,'V4 state coverage mismatch')
        from .gd_october_pricing_thermal_v4 import validate_thermal_pricing_v4
        validate_thermal_pricing_v4(spec,states,reference,selected)
    else:
        for g,d in thermal.items():
            obj(d,{'node','initial_power_mw','rows'},'thermal.'+g)
            require(d['node'] in nodes,'thermal node unknown')
            initial=num(d['initial_power_mw'],'initial power',0)
            require(type(d['rows']) is list and len(d['rows'])==8,'thermal exact eight quarters required')
            pr=power['rows'][g]; count,minutes=(8,15) if mapping=='native_eight_quarter' else (24,5)
            require(type(pr) is list and len(pr)==count,'reference exact grid required')
            vals=[]
            for r,(a,b) in zip(pr,c._grid(intervals[0][0],minutes,count)):
                obj(r,{'start','end','mw'},'reference row')
                require(c._time(r['start'],'power')==a and c._time(r['end'],'power')==b,'reference exact interval mismatch')
                vals.append(num(r['mw'],'reference MW',0))
            reference[g]=vals if count==8 else [sum(vals[i:i+3])/3 for i in range(0,24,3)]
            st=states['rows'][g]; require(type(st) is list and len(st)==24,'24 selected states required')
            selected[g]=[]
            initial_on=boolean(states['initial'][g],'initial online')
            for i,r in enumerate(st):
                obj(r,{'start','end','online','transition'},'state row')
                a,b=c._grid(intervals[0][0],5,24)[i]
                require(c._time(r['start'],'state')==a and c._time(r['end'],'state')==b,'state exact grid mismatch')
                boolean(r['online'],'online'); boolean(r['transition'],'transition')
                require(not r['transition'],'startup/shutdown transitions unsupported')
            for i in range(0,24,3):
                require(len({r['online'] for r in st[i:i+3]})==1,'mixed-state quarter rejected')
                selected[g].append(st[i]['online'])
            require(all(x==initial_on for x in selected[g]),'first slice supports stable states only; transitions not discarded')
            if not initial_on: require(initial==0,'offline initial power must be zero')
            for t,r in enumerate(d['rows']):
                obj(r,{'minimum_mw','maximum_mw','agc_up_mw','agc_down_mw','pricing_eligible','ramp_up_mw_h',
                    'ramp_down_mw_h','bids','positive_eligible','negative_eligible','primary_eligible','primary_fraction',
                    'accident_eligible','accident_ramp_10min_mw'},'thermal row')
                for key in ('minimum_mw','maximum_mw','agc_up_mw','agc_down_mw','ramp_up_mw_h','ramp_down_mw_h','primary_fraction','accident_ramp_10min_mw'):num(r[key],key,0)
                for key in ('pricing_eligible','positive_eligible','negative_eligible','primary_eligible','accident_eligible'):boolean(r[key],key)
                require(r['primary_fraction']<=1,'primary fraction exceeds one')
                require(r['positive_eligible']==selected[g][t] and r['negative_eligible']==selected[g][t],
                    'thermal positive/negative reserve participation must equal fixed online state')
                lo=r['minimum_mw']+r['agc_down_mw']; hi=r['maximum_mw']-r['agc_up_mw']
                require(0<=lo<=reference[g][t]<=hi,'reference outside AGC-adjusted physical bounds')
                if t==0:require(lo<=initial<=hi,'initial stable power outside first physical bounds')
                if not selected[g][t]:
                    require(all(r[k]==0 for k in ('minimum_mw','maximum_mw','agc_up_mw','agc_down_mw')) and reference[g][t]==0,'offline nonzero bounds or reference')
                    require(not any(r[k] for k in ('pricing_eligible','positive_eligible','negative_eligible','primary_eligible','accident_eligible')),'offline eligibility forbidden')
                bids=r['bids']; require(type(bids) is list and len(bids)>0,'explicit piecewise bids required')
                require(len(bids)<=10,'thermal bid count must not exceed ten segments (section 2.2.9.1)')
                prices=[]; widths=[]
                for bid in bids:
                    obj(bid,{'width_mw','yuan_per_mwh'},'bid')
                    widths.append(num(bid['width_mw'],'bid width',0)); prices.append(num(bid['yuan_per_mwh'],'bid price'))
                    require(widths[-1]>0,'strictly positive bid segment width')
                require(prices==sorted(prices),'convex nondecreasing bids required for continuous segment LP')
                require(sum(widths)>=r['maximum_mw'],'bid coverage below physical maximum')
    rr=spec['renewable_reserve']; ids=set(inv['families']['wind']+inv['families']['solar'])
    require(type(rr) is dict and set(rr)==ids,'renewable reserve full resource coverage required')
    for rid,r in rr.items():
        obj(r,{'positive_capacity_mw','negative_minimum_mw','basis'},'renewable reserve')
        require(r['basis']=='fixed_output_absolute_baseline_zero_incremental_reserve','renewable reserve basis required')
        vector(r['positive_capacity_mw'],'renewable positive',0); vector(r['negative_minimum_mw'],'renewable negative',0)
        require(all(a<=b for a,b in zip(r['negative_minimum_mw'],r['positive_capacity_mw'])),'renewable reserve bounds reversed')
        fixed_values=exogenous['wind_mw'].get(rid,exogenous['solar_mw'].get(rid))
        require(r['positive_capacity_mw']==fixed_values and r['negative_minimum_mw']==fixed_values,
            'fixed renewable absolute reserve max/min must both equal source fixed output; zero incremental reserve')
    require(type(spec['lines']) is dict and type(spec['sections']) is dict,'explicit network inventories required')
    for lid,l in spec['lines'].items():
        c._text(lid,'line ID'); obj(l,{'from_node','to_node','susceptance_mw_per_rad','lower_mw','upper_mw','slack_limit_mw'},'line')
        require(l['from_node'] in nodes and l['to_node'] in nodes and l['from_node']!=l['to_node'],'line endpoints invalid')
        require(num(l['susceptance_mw_per_rad'],'susceptance',0)>0,'positive susceptance required')
        _network_band(l)
    for sid,s in spec['sections'].items():
        c._text(sid,'section ID'); obj(s,{'line_weights','lower_mw','upper_mw','slack_limit_mw'},'section')
        require(type(s['line_weights']) is dict and bool(s['line_weights']),'nonempty explicit section weights required')
        require(set(s['line_weights'])<=set(spec['lines']),'unknown section line')
        for v in s['line_weights'].values(): require(num(v,'section weight')!=0,'zero section weight rejected')
        _network_band(s)
    reserves=spec['reserves']
    obj(reserves,{'families','positive_mw','positive_network_deduction_mw','positive_demand_baseline_mw',
        'negative_mw','negative_demand_mw','primary_mw','accident_mw','accident_deduction_mw','accident_slack_limit_mw'},'reserves')
    obj(reserves['families'],{'positive','negative','primary','accident'},'reserve declarations')
    for k,v in reserves.items():
        if k!='families': vector(v,k,0)
    for family,d in reserves['families'].items():
        obj(d,{'status','reason'},'reserve family'); c._text(d['reason'],'reserve declaration reason')
        require(d['status'] in {'active','explicitly_inactive'},'explicit reserve family status required')
        if d['status']=='explicitly_inactive':
            require(all(v==0 for v in reserves[family+'_mw']),family+': inactive demand must be explicit zero')
            if family=='positive':require(not any(reserves['positive_network_deduction_mw']),'inactive positive deduction must be zero')
            if family=='accident':require(not any(reserves['accident_deduction_mw']),'inactive accident deduction must be zero')
    require(all(abs(reserves['positive_demand_baseline_mw'][t]-sum(loads[n][t] for n in nodes))<1e-9 for t in range(8)),
            'positive reserve demand baseline must equal source load before any sensitivity probe')
    num(spec['tie_priority_penalty_yuan_per_mwh'],'TIE priority price penalty',0)
    parameters={k:float(audit['policy']['parameters'][k]['value']) for k in PARAMETERS}
    parameters['tie_priority_penalty_yuan_per_mwh']=float(spec['tie_priority_penalty_yuan_per_mwh'])
    require(0<=parameters['delta']<=1,'research delta must lie in [0,1]')
    normalized=dict(spec=spec,inventory=inv,reference_mw=reference,online=selected,parameters=parameters,
        loads=loads,fixed_injection=fixed,exogenous=exogenous,state_stage=states['stage'],identity=expected_identity,
        source_hashes=used,contract_sha256=doc['content_sha256'],policy_classification=audit['policy']['classification'],
        official_price=None,operational_mapping_verified=False,source_authenticity_verified=False)
    if storage_v2:
        from .gd_october_pricing_storage import validate_storage_pricing
        normalized['storage']=validate_storage_pricing(source(spec['storage_source'],'storage spec'),source=source,identity=expected_identity,
            cutoff=cutoff,start=intervals[0][0],nodes=nodes,inventory=inv['families'],delta=parameters['delta'],reference_stage=stage,native_reference=native_reference)
    if native_audit:
        normalized['native_reference']=native_audit
        normalized['identity']=native_audit['ed_selected_identity']
        normalized['source_contract_identity']=expected_identity
        used.update(native_audit['original_source_sha256'])
    return canonical(normalized)


def _network_band(d):
    lo=vector(d['lower_mw'],'network lower'); hi=vector(d['upper_mw'],'network upper')
    vector(d['slack_limit_mw'],'network slack limit',0)
    require(all(a<=b for a,b in zip(lo,hi)),'network band reversed')


def prepare_october_pricing_inputs(document, *, pricing_policy_source, numerical_spec_source, expected_identity,
        expected_delivery_start, asof, source_artifacts, native_reference=None):
    args=dict(pricing_policy_source=pricing_policy_source,numerical_spec_source=numerical_spec_source,
        expected_identity=expected_identity,expected_delivery_start=expected_delivery_start,asof=asof,source_artifacts=source_artifacts)
    if native_reference is not None:args['native_reference']=native_reference
    raw=_prepare(document,**args)
    inputs=OctoberPricingInputs(raw,sha256(raw).hexdigest())
    _BINDINGS[inputs]=(document,args)
    return inputs


def assert_pricing_inputs_current(inputs):
    require(type(inputs) is OctoberPricingInputs and inputs in _BINDINGS,'internally validated immutable inputs required')
    document,args=_BINDINGS[inputs]
    raw=_prepare(document,**args)
    require(raw==inputs.normalized_bytes and sha256(raw).hexdigest()==inputs.input_sha256,
            'pricing input/source/policy changed after validation')
    return inputs.data
