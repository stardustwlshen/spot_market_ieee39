"""R10 fixed-renewable absolute-capacity correction, explicitly versioned.

The embedded R9 bundle and all its original bytes are retained unchanged.
Only max/min capacity accounting changes: fixed renewable energy F contributes
F to both absolute boundaries, but zero incremental up/down reserve. No
qualification, dispatch flexibility, commitment, demand or import is invented.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path

from . import gd_october_ieee39_scenario as legacy
from .gd_october_job_codec import dumps, loads
from .gd_october_forecast_inputs import canonical_bytes
from .gd_october_sced import PERIODS, RULE_SHA256, FIXED_CAPACITY_SCHEMA

SCHEMA = 'gd.october.ieee39_fixed_capacity_scenario.v2'
DELTA_SCHEMA = 'gd.october.ieee39_capacity_correction.v2'
SOURCE = 'research://ieee39/r10/fixed-renewable-absolute-max-min-not-reserve-qualification'


def _delta(base):
    return dict(schema_version=DELTA_SCHEMA, variant_id=base['run_id']+'-ABSOLUTE-CAPACITY-V2',
        base_scenario_sha256=base['scenario_content_sha256'], base_assumption_sha256=base['assumption_sha256'],
        base_scenario_ref=dict(source_id='r10-capacity-base-scenario',source_sha256=sha256(dumps(base)).hexdigest()),
        source_kind='research_rule_interpretation', rule_sha256=RULE_SHA256,
        rule_reference='3.4.3.1, 3.4.3.2, 3.4.3.3, 3.4.3.18; PDF114-115,124-125',
        changes=['Fixed renewable F has absolute maximum=minimum=F in capacity coverage.',
                 'Its incremental upward and downward reserve remain exactly zero.'],
        defect_corrected='Excluding fixed F from absolute capacity mistakenly requires thermal headroom >= reserve+F.',
        original_legacy_bytes_unchanged=True, positive_reserve_mw=400.,
        accident_reserve_mw=400., accident_requirement_status='unchanged_explicit_research_stress_assumption_not_official_parameter',
        unchanged=['gross load','fixed TIE','fixed renewable energy','thermal schedule and initial powers',
                   'thermal ramps and transitions','idle/off storage','all source forecasts','primary and AGC zero',
                   'negative reserve demand zero','hard balance and accident reserve','network and objective'],
        renewable_reserve_qualification_asserted=False, operational_source_verified=False,
        official_market_accepted=False, whole_day_feasibility_verified=False)


def _digest(bundle):
    return sha256(dumps({k:v for k,v in bundle.items() if k!='scenario_content_sha256'})).hexdigest()


def create_scenario(root, day_start='2026-11-11T00:00:00+08:00', seed=20261111):
    return from_legacy_scenario(legacy.create_scenario(root,day_start,seed))


def from_legacy_scenario(base):
    legacy.validate_scenario(base)
    base=deepcopy(base);delta=canonical_bytes(_delta(base))
    bundle=dict(schema_version=SCHEMA,legacy_bundle=base,correction_document=delta,
        correction_sha256=sha256(delta).hexdigest(),variant_id=_delta(base)['variant_id'])
    bundle['scenario_content_sha256']=_digest(bundle)
    return bundle


def validate_scenario(bundle):
    if type(bundle) is not dict or set(bundle)!={'schema_version','legacy_bundle','correction_document','correction_sha256','variant_id','scenario_content_sha256'}:
        raise ValueError('exact V2 scenario fields required')
    if bundle['schema_version']!=SCHEMA or bundle['scenario_content_sha256']!=_digest(bundle):
        raise ValueError('V2 scenario identity/content changed')
    base=bundle['legacy_bundle'];legacy.validate_scenario(base)
    expected=canonical_bytes(_delta(base))
    if bundle['correction_document']!=expected or bundle['correction_sha256']!=sha256(expected).hexdigest() or bundle['variant_id']!=_delta(base)['variant_id']:
        raise ValueError('V2 correction ledger or variant identity differs from retained base')
    return True


def window_inputs(bundle,index):
    validate_scenario(bundle);base=bundle['legacy_bundle'];k=legacy.window_inputs(base,index)
    core=k['physical_inputs']['model_inputs'];start=datetime.fromisoformat(k['expected_delivery_start'])
    cells={b:{t:[] for t in PERIODS} for b in core['buses']}
    for family in ('wind_mw','solar_mw'):
        series=json.loads(k['source_artifacts']['sced:'+family])
        for row in series['rows']:
            seconds=(datetime.fromisoformat(row['delivery_start'])-start).total_seconds()
            if seconds%300 or not 0<=seconds<7200:raise ValueError('V2 fixed-energy source outside exact five-minute grid')
            t=1+int(seconds/300);bus=base['resources'][family][row['resource_id']]['bus'];cells[bus][t].append(row['value'])
    fixed={b:{t:math.fsum(v) for t,v in rows.items()} for b,rows in cells.items()}
    core['reserves']['capacity_accounting']=dict(schema_version=FIXED_CAPACITY_SCHEMA,
        rule_sha256=RULE_SHA256,source=SOURCE+'; ledger_sha256='+bundle['correction_sha256'],
        source_ref=dict(source_id='r10-capacity-correction',source_sha256=bundle['correction_sha256']),
        interpretation='fixed_energy_absolute_max_equals_min_no_incremental_reserve',
        renewable_fixed_injection_mw=fixed,incremental_up_mw=0.,incremental_down_mw=0.,qualifications_asserted=False)
    # Legacy qualified-capacity fields deliberately remain excluded and zero.
    k['source_artifacts']['r10-capacity-base-scenario']=dumps(base)
    k['source_artifacts']['r10-capacity-correction']=bundle['correction_document']
    k['physical_inputs']['sources']['reserves']['source']=SOURCE
    return k


def validate_bridge_correction(*, correction_raw, base_raw, physical_inputs,
                               expected_identity, contract_document, resource_mapping_ref,
                               source_artifacts):
    """Validate a direct bridge replay, not just the convenience wrapper.

    Hashes alone permit a consistently rehashed false ledger. Reconstruct the
    exact authorized delta from its retained base and ensure that the underlying
    window is unchanged. This verifies consistency, not operator authenticity.
    """
    base=loads(base_raw);legacy.validate_scenario(base)
    if correction_raw!=canonical_bytes(_delta(base)):
        raise ValueError('capacity correction ledger differs from exact retained base/delta')
    if sha256(base_raw).hexdigest()!=_delta(base)['base_scenario_ref']['source_sha256']:
        raise ValueError('capacity correction requires canonical retained base bytes')
    at=datetime.fromisoformat(physical_inputs['period_intervals'][0]['delivery_start'])
    delta=(at-datetime.fromisoformat(base['day_start'])).total_seconds()
    if delta%900 or not 0<=delta<86400:raise ValueError('capacity correction window outside retained base')
    original=legacy.window_inputs(base,int(delta/900))
    if expected_identity!=original['expected_identity'] or contract_document!=original['contract_document'] or resource_mapping_ref!=original['resource_mapping_ref']:
        raise ValueError('capacity correction cannot change base window identity/contract/mapping')
    if physical_inputs['sources']['reserves']['source']!=SOURCE:
        raise ValueError('capacity correction reserve provenance differs from V2 source')
    physical=deepcopy(physical_inputs)
    physical['model_inputs']['reserves'].pop('capacity_accounting',None)
    physical['sources']['reserves']['source']=original['physical_inputs']['sources']['reserves']['source']
    if physical!=original['physical_inputs']:
        raise ValueError('capacity correction cannot change base physical inputs or assumptions')
    if any(source_artifacts.get(k)!=v for k,v in original['source_artifacts'].items()):
        raise ValueError('capacity correction cannot change retained original window source bytes')
    return dict(base_scenario_ref=_delta(base)['base_scenario_ref'],
                variant_id=_delta(base)['variant_id'],unchanged_base_window_verified=True)


def build_window(bundle,index):
    from .gd_october_input_bridge import build_october_model_from_contract
    return build_october_model_from_contract(**window_inputs(bundle,index))


def build_native_window(bundle,index):
    from .gd_october_native_inputs import build_october_native_model
    from .gd_october_run_identity import KnownDispatchReference
    k=window_inputs(bundle,index);base=bundle['legacy_bundle'];identity=k['expected_identity']
    raw=k['source_artifacts']['hypothetical-plan'];at=(datetime.fromisoformat(base['day_start'])-timedelta(minutes=30)).isoformat()
    known=KnownDispatchReference(role='effective_baseline_dispatch',run_id=identity['run_id'],window_id=identity['window_id'],
        dispatch_id=identity['selected_dispatch_id'],source_id='hypothetical-plan',source_sha256=sha256(raw).hexdigest(),
        artifact=raw,source=legacy.SOURCE+'; unchanged known hypothetical plan, not future RT dispatch',published_at=at,available_at=at)
    return build_october_native_model(known_dispatch_reference=known,
        solve_intent=dict(purpose='corrected_fixed_capacity_research_v2',variant_id=bundle['variant_id'],
            correction_sha256=bundle['correction_sha256'],research_assumptions_sha256=base['assumption_sha256'],
            operator_authority=None,requested_relative_gap=.001),**k)


def export_scenario(bundle,output_dir):
    """New portable variant only. Never overwrite any prior directory/artifact."""
    validate_scenario(bundle);out=Path(output_dir);out.mkdir(parents=True,exist_ok=False)
    (out/'scenario.codec.json').write_bytes(dumps(bundle))
    (out/'correction.json').write_bytes(bundle['correction_document'])
    result=dict(schema_version=SCHEMA,variant_id=bundle['variant_id'],
        scenario_content_sha256=bundle['scenario_content_sha256'],correction_sha256=bundle['correction_sha256'],
        baseline_scenario_content_sha256=bundle['legacy_bundle']['scenario_content_sha256'],
        source_kind='research',solver_called=False,official_market_accepted=False)
    (out/'manifest.json').write_bytes(canonical_bytes(result));return result
