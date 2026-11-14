"""Strict JSON adapter to the existing external pumped-storage plan boundary.

No dispatch or preparation schedule is invented. Observed mode and effective
initial planned mode remain different fields. A planned-off first point stays
off even when a preparation is declared ready.
"""
from __future__ import annotations

from dataclasses import dataclass

from .gd_external_inputs import (
    ExternalMetadata, _audit, _decode, _envelope, _list, _object, _record, _require,
    _time, json_safe,
)
from .gd_pumped_plan_boundary import (
    ApprovedPumpedPreparation, ExternalPumpedPlan, ObservedPumpedState,
    PumpedModeProjection, PumpedPlanBoundaryInput, ReservoirProjection,
    ValidatedPumpedPlanBoundary, validate_pumped_plan_boundary,
)

PUMPED_UNITS = {"power": "MW", "energy": "MWh", "reservoir": "m3", "duration": "h"}


@dataclass(frozen=True)
class ImportedPumpedPlan:
    metadata: ExternalMetadata
    boundary: ValidatedPumpedPlanBoundary
    record_availability: dict


def load_pumped_plan_import(document, *, expected_run_id, expected_selected_dispatch, asof, expected_inventory):
    metadata, payload = _envelope(document, input_type="pumped_plan", expected_run_id=expected_run_id,
        expected_selected_dispatch=expected_selected_dispatch, asof=asof, units=PUMPED_UNITS)
    _object(payload, ("boundary",), "pumped payload")
    body = payload["boundary"]
    _object(body, ("inventory", "information_cutoff", "model_initial_time", "first_calculation_time",
        "time_mapping_source", "intraday_state_market_enabled", "observations", "initial_plans",
        "plans", "reservoirs", "preparations", "mode_projections"), "boundary")
    cutoff = _time(body["information_cutoff"], "information_cutoff")
    _require(metadata.available_at <= cutoff <= metadata.asof,
             "pumped envelope must be available at boundary information_cutoff <= asof")
    decoded = dict(body)
    record_availability = {}
    families = (("observations", ObservedPumpedState), ("plans", ExternalPumpedPlan),
        ("reservoirs", ReservoirProjection), ("preparations", ApprovedPumpedPreparation),
        ("mode_projections", PumpedModeProjection))
    for name, cls in families:
        decoded[name] = []
        record_availability[name] = {}
        for wrapper in _list(body[name], name):
            completed = (lambda row: row.observed_at) if name == "observations" else (
                (lambda row: row.measured_at) if name == "reservoirs" else None)
            row, published, available = _record(cls, wrapper, name, metadata, completed_at=completed)
            _require(available <= cutoff, name + ": input unavailable at boundary cutoff")
            if name in {"observations", "reservoirs"}:
                _require(row.available_at == available, name + ": record available_at mismatch")
            if name in {"plans", "preparations"}:
                _require(row.published_at == published and row.approved_at <= available,
                         name + ": publication/approval/availability mismatch")
            key_field = {"observations": "unit_id", "plans": "plan_id", "reservoirs": "plant_id",
                         "preparations": "preparation_id", "mode_projections": "projection_id"}[name]
            key = getattr(row, key_field)
            _require(key not in record_availability[name], name + ": duplicate record identifier")
            record_availability[name][key] = dict(published_at=published, available_at=available)
            decoded[name].append(json_safe(row))
    inputs = _decode(PumpedPlanBoundaryInput, decoded, "boundary")
    boundary = validate_pumped_plan_boundary(inputs, expected_inventory=expected_inventory)
    # A record received after execution cannot support an earlier issued path.
    # This adds no preparation duration and does not reinterpret observations.
    plan_by_id = {p.plan_id: p for p in inputs.plans}
    for path in (*inputs.reservoirs, *inputs.mode_projections):
        segments = path.plan_segments if isinstance(path, ReservoirProjection) else path.segments
        for segment in segments:
            _require(record_availability["plans"][segment.plan_id]["available_at"] <= segment.start,
                     "projected segment plan unavailable at execution start")
            if hasattr(segment, "preparation_id") and segment.preparation_id is not None:
                _require(record_availability["preparations"][segment.preparation_id]["available_at"]
                         <= plan_by_id[segment.plan_id].issued_at,
                         "historical preparation unavailable at original plan issuance")
    return ImportedPumpedPlan(metadata, boundary, record_availability)


def build_pumped_plan_report(document, *, expected_run_id, expected_selected_dispatch, asof, expected_inventory):
    inputs = load_pumped_plan_import(document, expected_run_id=expected_run_id,
        expected_selected_dispatch=expected_selected_dispatch, asof=asof, expected_inventory=expected_inventory)
    audit = json_safe(inputs.boundary.audit)
    audit.update(_audit(inputs.metadata), actual_state_overwritten_by_plan=False,
        preparation_overrides_planned_off=False, input_missing_values_replaced_with_zero=False,
        automatic_preparation_duration_inferred=False, physical_model_or_solver_invoked=False)
    audit["record_availability"] = json_safe(inputs.record_availability)
    return dict(status="validated_external_boundary", metadata=json_safe(inputs.metadata),
        units=json_safe(inputs.boundary.units), audit=audit)
