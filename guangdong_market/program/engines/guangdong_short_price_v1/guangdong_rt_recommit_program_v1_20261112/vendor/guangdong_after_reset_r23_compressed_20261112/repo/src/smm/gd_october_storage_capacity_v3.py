"""Opt-in research accounting of storage's current-LP absolute capacity.

This is an algebraic gross-load consistency interpretation, not an official
algorithm or a grant of reserve qualification. Sources prove consistency, not
operator authenticity. The original storage physics and qualified ports remain
unchanged. Only matching upward/downward eligibility is supported.
"""
from copy import deepcopy
from hashlib import sha256
import json
import pyomo.environ as pyo
from .gd_energy_core import EnergyInputError
from .gd_october_sced import RULE_SHA256

SCHEMA = 'gd.october.storage_absolute_capacity.v3'
POLICY = dict(
    interpretation='current_lp_signed_power_plus_up_minus_down',
    qualification_scope='symmetric_existing_boolean_only',
    eligible_generation='existing_absolute_agc_adjusted_bounds_without_extra_baseline',
    other_generation='current_generation_zero_incremental_reserve',
    pumping='negative_current_pumping_zero_incremental_operating_reserve',
    battery='current_discharge_minus_charge_zero_incremental_reserve',
    inactive='zero_by_existing_physical_bounds',
    primary_and_contingency='unchanged_separate_products',
    rule_reference='3.4.3.2-3 printed112-113/PDF115-116; accident3.4.3.14 printed119-120/PDF122-123',
    official_algorithm_certified=False, operational_source_verified=False,
    directional_qualification_supported=False, qualifications_asserted=False)

def _require(ok, message):
    if not ok: raise EnergyInputError('storage capacity V3: '+message)

def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def _hash(value):
    from .gd_october_job_codec import dumps
    return sha256(dumps(value)).hexdigest()

def declaration(storage_spec, *, source_ref):
    """Pure model declaration; full source verification occurs at input bridge."""
    _require(type(storage_spec) is dict, 'explicit storage required')
    return dict(schema_version=SCHEMA, rule_sha256=RULE_SHA256, policy=deepcopy(POLICY),
        storage_spec_sha256=_hash(storage_spec), run_id=storage_spec['run_id'],
        window_start=storage_spec['window_start'],information_cutoff=storage_spec['information_cutoff'],
        source_ref=deepcopy(source_ref))

def validate_declaration(raw, storage_spec):
    _require(type(raw) is dict and type(raw.get('source_ref')) is dict, 'exact declaration required')
    ref=raw['source_ref']
    _require(set(ref)=={'source_id','source_sha256'} and type(ref['source_id']) is str and bool(ref['source_id'].strip()), 'exact source reference required')
    h=ref['source_sha256']
    _require(type(h) is str and len(h)==64 and all(c in '0123456789abcdef' for c in h),'source SHA256 required')
    _require(_json(raw)==_json(declaration(storage_spec,source_ref=ref)), 'policy, storage, identity, clock or fields differ from exact supported declaration')
    return deepcopy(raw)

def attach_baseline(model, raw, storage_spec):
    checked=validate_declaration(raw,storage_spec)
    s=model.october_storage; d=model._gd_october_storage_inputs
    # Qualified generating units already supply absolute U/L: never add P again.
    def baseline(_,t):
        return (sum(s.battery_net[b,t] for b in d['batteries']) +
            sum(s.pumped_net[u,t] for u,x in d['units'].items()
                if not (x['modes'][t]=='generate' and x['operating_reserve_eligible'][t])))
    model.storage_absolute_baseline=pyo.Expression(model.T,rule=baseline)
    return checked

def _ledger(base, bridge):
    storage=base['model_inputs']['storage_spec']
    return dict(schema_version=SCHEMA+'.source',rule_sha256=RULE_SHA256,policy=deepcopy(POLICY),
        identity=deepcopy(base['identity']),window_start=storage['window_start'],
        information_cutoff=storage['information_cutoff'],
        original_physical_ref=dict(source_id='r10c-storage-original-physical',source_sha256=_hash(base)),
        original_storage_sha256=_hash(storage),
        original_storage_provenance=deepcopy(base['sources']['storage']),
        contract_sha256=sha256(bridge['contract_document']).hexdigest(),
        resource_mapping_ref=deepcopy(bridge['resource_mapping_ref']))

def enable_storage_capacity(bridge_inputs):
    """Return a NEW input package retaining exact original physical/source bytes.

    This never solves, publishes, upgrades receipts or changes effective modes.
    """
    from .gd_october_job_codec import dumps
    k=deepcopy(bridge_inputs); base=deepcopy(k['physical_inputs'])
    _require('storage_capacity_accounting' not in base['model_inputs'],'already enabled')
    _require(k['mode']=='research','research mode required')
    _require(base['model_inputs']['storage_spec'] is not None,'storage required')
    ledger=_json(_ledger(base,k));sid='r10c-storage-capacity-policy'
    additions={'r10c-storage-original-physical':dumps(base),sid:ledger}
    _require(not set(additions)&set(k['source_artifacts']),'reserved source IDs already used')
    k['source_artifacts'].update(additions)
    k['physical_inputs']['model_inputs']['storage_capacity_accounting']=declaration(
        base['model_inputs']['storage_spec'],source_ref=dict(source_id=sid,source_sha256=sha256(ledger).hexdigest()))
    return k

def validate_bridge(*, physical_inputs, source_artifacts, contract_document, resource_mapping_ref, mode):
    from .gd_october_input_bridge import _source, _json as parse, _availability, _time
    from .gd_october_job_codec import loads, dumps
    raw=physical_inputs['model_inputs']['storage_capacity_accounting']
    validate_declaration(raw,physical_inputs['model_inputs']['storage_spec'])
    _require(mode=='research','research mode required')
    ledger_raw=_source(raw['source_ref'],source_artifacts,'storage capacity policy')
    ledger=parse(ledger_raw,'storage capacity policy')
    original=_source(ledger.get('original_physical_ref'),source_artifacts,'original storage physical inputs')
    base=loads(original)
    _require(original==dumps(base),'canonical original physical bytes required')
    current=deepcopy(physical_inputs);current['model_inputs'].pop('storage_capacity_accounting')
    _require(current==base,'physical inputs differ from original beyond single accounting extension')
    expected=_ledger(base,dict(contract_document=contract_document,resource_mapping_ref=resource_mapping_ref))
    _require(ledger_raw==_json(expected),'source ledger differs from reconstructed exact interpretation and original sources')
    _availability(base['sources']['storage'],_time(raw['information_cutoff'],'storage cutoff'),'original storage provenance')
    return dict(schema_version=SCHEMA, source_ref=deepcopy(raw['source_ref']),
        original_physical_ref=expected['original_physical_ref'],exact_delta_verified=True,
        original_storage_sha256=expected['original_storage_sha256'],source_authenticity_verified=False,
        operational_source_verified=False, official_algorithm_certified=False)
