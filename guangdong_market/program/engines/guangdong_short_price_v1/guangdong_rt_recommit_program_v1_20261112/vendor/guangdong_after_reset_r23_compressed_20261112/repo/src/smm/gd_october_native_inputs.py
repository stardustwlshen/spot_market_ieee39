"""Pre-solve October source snapshots and post-solve RT lineage bindings.

This unsolved wrapper reuses, and does not modify, the R4 physical bridge. Its
legacy selected header means the explicitly declared known DA/effective source,
never the future RT output. Creating a wrapper is not native computation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .gd_october_input_bridge import build_october_model_from_contract, assert_october_input_binding
from .gd_october_rt_contract import validate_october_rt_contract
from .gd_october_run_identity import (
    KnownDispatchReference, PreSolveIdentity, OctoberRunIdentityError,
    require, text, digest, time_text, canonical_json_bytes, parse_json_bytes, snapshot_bytes,
    bind_rt_result, assert_rt_result_binding,
)

SCHEMA = 'gd.october.native_input_binding.v1'


@dataclass(frozen=True)
class NativeInputSnapshot:
    """No mutable containers or references back to caller-owned dictionaries."""
    contract_document: bytes
    source_artifacts: tuple
    expected_identity: bytes
    expected_delivery_start: str
    asof: str
    mode: str
    physical_inputs: bytes
    resource_mapping_ref: bytes
    known_dispatch_reference: KnownDispatchReference
    solve_intent: bytes


@dataclass(frozen=True)
class _NativeBinding:
    snapshot: NativeInputSnapshot
    identity: PreSolveIdentity
    audit_sha256: str


def _validate_intent(intent):
    require(type(intent) is dict and bool(intent), 'solve_intent: explicit nonempty settings/declarations required')
    def inspect(value):
        if type(value) is dict:
            require(not ({'selected_dispatch_sha256', 'rt_result_sha256', 'rt_selected_dispatch_sha256'} & set(value)),
                    'solve_intent: future RT result hash is not a pre-solve input')
            for child in value.values():
                inspect(child)
        elif type(value) in (list, tuple):
            for child in value:
                inspect(child)
    inspect(intent)
    return snapshot_bytes(intent)


def _known_reference(snapshot, contract):
    known = snapshot.known_dispatch_reference.validate()
    expected = parse_json_bytes(snapshot.expected_identity)
    require(known.run_id == expected['run_id'] and known.window_id == expected['window_id'],
            'known dispatch: run/window identity mismatch')
    require(known.dispatch_id == expected['selected_dispatch_id'] and
            known.source_sha256 == expected['selected_dispatch_sha256'],
            'known dispatch: legacy selected source identity mismatch')
    reference = dict(source_id=known.source_id, source_sha256=known.source_sha256)
    require(contract['selected_dispatch_source'] == reference,
            'known dispatch: must identify exactly the legacy selected source')
    artifacts = dict(snapshot.source_artifacts)
    require(artifacts.get(known.source_id) == known.artifact,
            'known dispatch: original source bytes were substituted or rewritten')
    cutoff = time_text(contract['information_cutoff']['sced'], 'SCED cutoff')
    require(known.available_at <= cutoff, 'known dispatch: source unavailable at SCED cutoff')
    mapping = parse_json_bytes(artifacts[contract['mapping_source']['source_id']])
    require(time_text(mapping['selected_dispatch_available_at'], 'legacy selected availability') == known.available_at,
            'known dispatch: availability contradicts legacy source mapping')
    return known


def _source_recheck(snapshot):
    report = validate_october_rt_contract(snapshot.contract_document,
        expected_identity=parse_json_bytes(snapshot.expected_identity),
        expected_delivery_start=snapshot.expected_delivery_start, asof=snapshot.asof,
        source_artifacts=dict(snapshot.source_artifacts), mode=snapshot.mode)
    require(report['status'] == 'validated', 'native input snapshot revalidation failed: ' +
            '; '.join(x['code'] + ': ' + x['message'] for x in report['issues']))
    _known_reference(snapshot, report['data']['contract'])
    resource_ref = parse_json_bytes(snapshot.resource_mapping_ref)
    require(set(resource_ref) == {'source_id', 'source_sha256'},
            'native input snapshot: exact physical mapping source reference required')
    text(resource_ref['source_id'], 'physical mapping source ID')
    digest(resource_ref['source_sha256'], 'physical mapping source SHA256')
    artifacts = dict(snapshot.source_artifacts)
    require(resource_ref['source_id'] in artifacts and
            sha256(artifacts[resource_ref['source_id']]).hexdigest() == resource_ref['source_sha256'],
            'native input snapshot: physical mapping source hash mismatch')
    return report


def _input_digest(snapshot):
    # Include EVERY source byte, including originals of derived five-minute
    # forecasts and extra supplied evidence, plus all static and timed inputs.
    return sha256(snapshot_bytes(dict(schema_version=SCHEMA,
        contract_document=snapshot.contract_document, source_artifacts=snapshot.source_artifacts,
        expected_identity=snapshot.expected_identity, expected_delivery_start=snapshot.expected_delivery_start,
        asof=snapshot.asof, mode=snapshot.mode, physical_inputs=snapshot.physical_inputs,
        resource_mapping_ref=snapshot.resource_mapping_ref,
        known_dispatch_reference=snapshot.known_dispatch_reference))).hexdigest()


def _identity(snapshot, model_hash, contract):
    bundle_hash = _input_digest(snapshot)
    known = snapshot.known_dispatch_reference
    intent_hash = sha256(snapshot_bytes(dict(schema_version=SCHEMA, input_bundle_sha256=bundle_hash,
        physical_model_sha256=model_hash, solve_intent=snapshot.solve_intent))).hexdigest()
    return PreSolveIdentity(known.run_id, known.window_id, known, bundle_hash, intent_hash, model_hash,
        time_text(contract['delivery_start'], 'delivery start'),
        # The bridge's explicit physical periods, not an inferred time policy,
        # determine the two-hour endpoint; the contract independently verifies it.
        time_text(contract['_verified_delivery_end'], 'delivery end'),
        time_text(contract['information_cutoff']['sced'], 'SCED cutoff'))


def _contract_with_end(report):
    contract = deepcopy(report['data']['contract'])
    contract['_verified_delivery_end'] = report['data']['grids']['sced'][-1]['delivery_end']
    return contract


def build_october_native_model(*, known_dispatch_reference, solve_intent,
        contract_document, source_artifacts, expected_identity, expected_delivery_start,
        asof, mode, physical_inputs, resource_mapping_ref):
    """Build an UNSOLVED model with explicit known-source and solve-intent roles.

    All R4 parameters remain required. There is no selection-by-name, implicit
    replay promotion, source relabelling, policy default, or solver invocation.
    """
    require(isinstance(known_dispatch_reference, KnownDispatchReference),
            'explicit typed known_dispatch_reference required; no automatic R4 replay promotion')
    known = known_dispatch_reference.validate()
    require(type(contract_document) is bytes and bool(contract_document), 'original immutable contract bytes required')
    require(type(source_artifacts) is dict and all(type(k) is str and bool(k.strip()) and
            type(v) is bytes and bool(v) for k, v in source_artifacts.items()),
            'source_artifacts: explicit source IDs and original immutable nonempty bytes required')
    require(type(expected_identity) is dict, 'explicit legacy source identity required')
    intent_input = deepcopy(solve_intent)
    storage_accounting = physical_inputs.get('model_inputs', {}).get('storage_capacity_accounting')
    if storage_accounting is not None:
        require('storage_capacity_accounting' not in intent_input or intent_input['storage_capacity_accounting'] == storage_accounting,
                'solve intent storage capacity declaration conflicts with physical input')
        intent_input['storage_capacity_accounting'] = deepcopy(storage_accounting)
    renewable_accounting = physical_inputs.get('model_inputs', {}).get('reserves', {}).get('capacity_accounting')
    if renewable_accounting is not None and renewable_accounting.get('schema_version') == 'gd.october.fixed_renewable_absolute_capacity.v4':
        require('fixed_renewable_capacity_v4' not in intent_input or snapshot_bytes(intent_input['fixed_renewable_capacity_v4']) == snapshot_bytes(renewable_accounting),
                'solve intent V4 fixed renewable declaration conflicts with physical input')
        intent_input['fixed_renewable_capacity_v4'] = deepcopy(renewable_accounting)
        implementation_sha256 = sha256(Path(__file__).with_name('gd_october_fixed_renewable_capacity_v4.py').read_bytes()).hexdigest()
        key = 'fixed_renewable_capacity_v4_implementation_sha256'
        require(key not in intent_input or intent_input[key] == implementation_sha256,
                'solve intent V4 implementation SHA conflicts with current source bytes')
        intent_input[key] = implementation_sha256
    intent = _validate_intent(intent_input)
    # Deepcopy before construction so neither the bridge nor caller retains a
    # mutable alias into the sealed physical parameter declaration.
    physical = deepcopy(physical_inputs)
    mapping = deepcopy(resource_mapping_ref)
    snapshot = NativeInputSnapshot(contract_document, tuple(sorted(source_artifacts.items())),
        canonical_json_bytes(expected_identity), time_text(expected_delivery_start, 'expected start'),
        time_text(asof, 'asof'), text(mode, 'mode'), snapshot_bytes(physical),
        canonical_json_bytes(mapping), known, intent)
    report = _source_recheck(snapshot)
    model = build_october_model_from_contract(contract_document=snapshot.contract_document,
        source_artifacts=dict(snapshot.source_artifacts), expected_identity=parse_json_bytes(snapshot.expected_identity),
        expected_delivery_start=snapshot.expected_delivery_start, asof=snapshot.asof, mode=snapshot.mode,
        physical_inputs=physical, resource_mapping_ref=mapping)
    binding = assert_october_input_binding(model)
    identity = _identity(snapshot, binding['physical_model_sha256'], _contract_with_end(report))
    audit = dict(schema_version=SCHEMA, **identity.as_dict(), mode=mode,
        contract_document_sha256=sha256(contract_document).hexdigest(),
        physical_inputs_sha256=sha256(snapshot.physical_inputs).hexdigest(),
        source_sha256={k: sha256(v).hexdigest() for k, v in snapshot.source_artifacts},
        legacy_selected_header_role='known_source_reference_not_future_rt_result',
        known_dispatch_role_explicitly_declared=True, known_dispatch_values_reproduced=False,
        uses_future_actual=model._gd_october_input_bridge_audit['uses_future_actual'],
        derived_simulation=model._gd_october_input_bridge_audit['derived_simulation'],
        causal_eligible=model._gd_october_input_bridge_audit['causal_eligible'],
        research_thermal_assumptions=model._gd_october_input_bridge_audit['research_thermal_assumptions'],
        operational_source_verified=False,
        source_authenticity_verified=False, native_solver_verified=False,
        numerical_feasibility_verified=False, official_algorithm_certified=False)
    model._gd_october_native_input_audit = audit
    model._gd_october_native_input_binding = _NativeBinding(snapshot, identity,
        sha256(canonical_json_bytes(audit)).hexdigest())
    assert_october_native_input_binding(model)
    return model


def assert_october_native_input_binding(model):
    """Revalidate retained raw source bytes and sealed model before/after solving."""
    binding = getattr(model, '_gd_october_native_input_binding', None)
    require(isinstance(binding, _NativeBinding), 'native input wrapper-built model required')
    bridge = assert_october_input_binding(model)
    report = _source_recheck(binding.snapshot)
    expected = _identity(binding.snapshot, bridge['physical_model_sha256'], _contract_with_end(report))
    require(expected == binding.identity, 'native input: snapshot/model/pre-solve identity mismatch')
    audit = getattr(model, '_gd_october_native_input_audit', None)
    require(type(audit) is dict and sha256(canonical_json_bytes(audit)).hexdigest() == binding.audit_sha256,
            'native input: audit changed after construction')
    require(audit['source_sha256'] == {k: sha256(v).hexdigest() for k, v in binding.snapshot.source_artifacts},
            'native input: source snapshot hash mismatch')
    return dict(**expected.as_dict(), input_binding_verified=True, model_integrity_verified=True,
        source_bytes_revalidated=True, known_dispatch_role_explicitly_declared=True,
        known_dispatch_values_reproduced=False, uses_future_actual=audit['uses_future_actual'],
        derived_simulation=audit['derived_simulation'], causal_eligible=audit['causal_eligible'],
        research_thermal_assumptions=audit['research_thermal_assumptions'], operational_source_verified=False,
        source_authenticity_verified=False, native_solver_verified=False,
        numerical_feasibility_verified=False, official_algorithm_certified=False)


def bind_october_rt_result(model, *, selected_dispatch_id, artifact):
    """Bind finished bytes; native solver evidence belongs to the native runner."""
    assert_october_native_input_binding(model)
    return bind_rt_result(model._gd_october_native_input_binding.identity,
                          selected_dispatch_id=selected_dispatch_id, artifact=artifact)


def assert_october_rt_result_binding(reference, *, model, expected_identity=None):
    assert_october_native_input_binding(model)
    return assert_rt_result_binding(reference,
        expected_input_identity=model._gd_october_native_input_binding.identity,
        expected_identity=expected_identity)
