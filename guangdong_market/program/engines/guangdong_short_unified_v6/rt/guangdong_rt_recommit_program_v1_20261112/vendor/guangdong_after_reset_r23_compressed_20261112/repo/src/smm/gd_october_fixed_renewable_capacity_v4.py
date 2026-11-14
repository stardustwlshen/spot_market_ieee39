"""Explicit research fixed-renewable baseline for general original sources.

This is NOT IEEE39 V2 or operator qualification. Retain a complete canonical
original bridge package and permit one exact physical delta: absolute U=L=F.
Storage V3 may subsequently wrap this declaration; neither wrapper can edit
the other's retained base. No source classifications, schedules or demands are
changed. Consistency of user-supplied originals is not external authenticity.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import math

from .gd_energy_core import EnergyInputError
from .gd_october_job_codec import dumps, loads
from .gd_october_sced import PERIODS, RULE_SHA256, FIXED_RESEARCH_CAPACITY_SCHEMA
from .gd_october_run_identity import snapshot_bytes, time_text

SCHEMA = FIXED_RESEARCH_CAPACITY_SCHEMA
LEDGER_SCHEMA = 'gd.october.fixed_renewable_research_source.v4'
SOURCE = 'research://r10e/general-original-fixed-renewable-absolute-baseline-v4'
ORIGINAL_ID = 'r10e-fixed-renewable-original-inputs'
POLICY_ID = 'r10e-fixed-renewable-capacity-policy'
POLICY = dict(interpretation='fixed_energy_absolute_max_equals_min_no_incremental_reserve',
    rule_reference='3.4.3.1-3,3.4.3.18; fixed injection in energy balance and gross-load absolute capacity',
    formula_status='explicit_research_interpretation_not_official_renewable_qualification',
    incremental_up_mw=0., incremental_down_mw=0., qualifications_asserted=False,
    source_authenticity_verified=False, operational_source_verified=False,
    official_algorithm_certified=False, official_market_accepted=False)

def _require(ok, message):
    if not ok: raise EnergyInputError('fixed renewable V4: '+message)

def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def _base(base):
    _require(type(base) is dict and set(base)=={'contract_document','source_artifacts','expected_identity',
        'expected_delivery_start','asof','mode','physical_inputs','resource_mapping_ref'},'exact original bridge fields required')
    core=base['physical_inputs']['model_inputs']
    _require(base['mode']=='research','research mode required')
    _require('capacity_accounting' not in core['reserves'] and 'storage_capacity_accounting' not in core,
        'apply to original unextended inputs, before storage V3; no stacking or relabeling V2')
    _require(core['renewable_mode']=='fixed_exogenous','only original fixed exogenous renewable supported')
    _require(not {ORIGINAL_ID,POLICY_ID}&set(base['source_artifacts']),'reserved source ID collision')

def _fixed(base):
    """Original contract rows at the declared node/time; no fitted output."""
    from .gd_october_input_bridge import _source
    _base(base)
    artifacts=base['source_artifacts'];contract=json.loads(base['contract_document'])
    mapping=json.loads(_source(base['resource_mapping_ref'],artifacts,'original V4 resource mapping'))
    buses=base['physical_inputs']['model_inputs']['buses']
    intervals=base['physical_inputs']['period_intervals']
    times={datetime.fromisoformat(r['delivery_start']):r['period'] for r in intervals}
    cells={b:{t:[] for t in PERIODS} for b in buses};refs=[]
    seen=set()
    for ref in contract['series_sources']:
        raw=_source(ref,artifacts,'original V4 source');src=json.loads(raw)
        if src['stage']!='sced' or src['family'] not in ('wind_mw','solar_mw'):continue
        family=src['family'];_require(family not in seen,'duplicate renewable source');seen.add(family);refs.append(deepcopy(ref))
        rows=[r for r in mapping['rows'] if r['family']==family]
        nodes={r['source_resource_id']:r['bus'] for r in rows}
        _require(len(nodes)==len(rows) and set(nodes)==set(src['resource_ids']),'exact original renewable node coverage required')
        for row in src['rows']:
            at=datetime.fromisoformat(row['delivery_start']);end=datetime.fromisoformat(row['delivery_end'])
            _require(at in times and (end-at).total_seconds()==300,'exact original five-minute interval required')
            value=row['value'];_require(type(value) in (int,float) and math.isfinite(value) and value>=0,'finite nonnegative fixed source required')
            cells[nodes[row['resource_id']]][times[at]].append(value)
    _require(seen=={'wind_mw','solar_mw'},'explicit wind and solar originals required')
    fixed={b:{t:math.fsum(values) for t,values in rows.items()} for b,rows in cells.items()}
    _require(any(v>0 for row in fixed.values() for v in row.values()),'V4 requires nonzero original renewable injection; use legacy explicit-zero path otherwise')
    return fixed,refs

def _ledger(base):
    fixed,refs=_fixed(base)
    return dict(schema_version=LEDGER_SCHEMA,rule_sha256=RULE_SHA256,policy=deepcopy(POLICY),
        identity=deepcopy(base['expected_identity']),original_bridge_ref=dict(source_id=ORIGINAL_ID,source_sha256=sha256(dumps(base)).hexdigest()),
        contract_sha256=sha256(base['contract_document']).hexdigest(),resource_mapping_ref=deepcopy(base['resource_mapping_ref']),
        original_source_sha256={sid:sha256(raw).hexdigest() for sid,raw in sorted(base['source_artifacts'].items())},
        original_renewable_sources=refs,renewable_fixed_injection_mw=fixed,
        original_physical_sha256=sha256(dumps(base['physical_inputs'])).hexdigest(),
        exact_allowed_delta='add reserves.capacity_accounting only; all original inputs and source bytes unchanged')

def _declaration(base,ledger_raw):
    fixed,_=_fixed(base)
    return dict(schema_version=SCHEMA,rule_sha256=RULE_SHA256,source=SOURCE,
        source_ref=dict(source_id=POLICY_ID,source_sha256=sha256(ledger_raw).hexdigest()),
        interpretation=POLICY['interpretation'],renewable_fixed_injection_mw=fixed,
        incremental_up_mw=0.,incremental_down_mw=0.,qualifications_asserted=False)

def enable_fixed_renewable_capacity(bridge_inputs):
    """Create a new data-only V4 input, preserving the caller's original bytes."""
    from .gd_october_input_bridge import build_october_model_from_contract
    base=deepcopy(bridge_inputs);_base(base)
    # Recheck the original contract, time, resource mapping, and real SCED
    # construction before issuing a source declaration. This does not solve.
    build_october_model_from_contract(**base)
    ledger_raw=_json(_ledger(base));k=deepcopy(base)
    k['source_artifacts'].update({ORIGINAL_ID:dumps(base),POLICY_ID:ledger_raw})
    k['physical_inputs']['model_inputs']['reserves']['capacity_accounting']=_declaration(base,ledger_raw)
    return k

def validate_bridge(*,physical_inputs,source_artifacts,contract_document,resource_mapping_ref,
                    expected_identity,expected_delivery_start,asof,mode):
    from .gd_october_input_bridge import _source
    raw=physical_inputs['model_inputs']['reserves']['capacity_accounting']
    ledger_raw=_source(raw.get('source_ref'),source_artifacts,'V4 policy')
    ledger=json.loads(ledger_raw)
    original=_source(ledger.get('original_bridge_ref'),source_artifacts,'V4 original bridge')
    base=loads(original);_base(base)
    _require(original==dumps(base),'canonical original bridge bytes required')
    _require(ledger_raw==_json(_ledger(base)),'ledger differs from reconstructed exact original source policy')
    _require(snapshot_bytes(raw)==snapshot_bytes(_declaration(base,ledger_raw)),'declaration differs from original fixed injection and exact zero-increment policy')
    current=deepcopy(physical_inputs);current['model_inputs']['reserves'].pop('capacity_accounting')
    _require(snapshot_bytes(current)==snapshot_bytes(base['physical_inputs']),'physical inputs changed beyond single fixed-renewable capacity extension')
    for key,value in dict(contract_document=contract_document,resource_mapping_ref=resource_mapping_ref,
        expected_identity=expected_identity,expected_delivery_start=expected_delivery_start,asof=asof,mode=mode).items():
        if key in ('asof','expected_delivery_start'):
            _require(time_text(value,key)==time_text(base[key],key),key+' differs from retained original instant')
        else:
            _require(snapshot_bytes(value)==snapshot_bytes(base[key]),key+' differs from retained original')
    _require(all(source_artifacts.get(sid)==raw for sid,raw in base['source_artifacts'].items()),'original source bytes changed or missing')
    return dict(schema_version=SCHEMA,source_ref=deepcopy(raw['source_ref']),original_bridge_ref=deepcopy(ledger['original_bridge_ref']),
        exact_delta_verified=True,original_source_sha256=ledger['original_source_sha256'],
        original_physical_sha256=ledger['original_physical_sha256'],policy=deepcopy(POLICY),
        general_research_source=True,ieee39_v2_source=False,source_authenticity_verified=False)
