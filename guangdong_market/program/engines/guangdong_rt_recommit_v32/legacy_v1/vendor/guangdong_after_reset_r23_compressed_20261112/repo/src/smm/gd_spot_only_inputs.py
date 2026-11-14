"""Explicit no-contract spot research scenario; never an official total ledger.

The existing energy cash implementation owns all arithmetic and signed units.
Only this explicitly selected scenario can create sourced research zero
contract/base/agency deductions. Missing actual energy or prices stay missing.
"""
from __future__ import annotations

from dataclasses import dataclass

from .gd_energy_cash import (
    DayAheadDeduction, EnergyCashScope, EnergyVolumeInput, SettlementKey,
    calculate_energy_cash, _scope,
)
from .gd_price_research import EvidenceValue
from .gd_external_inputs import (
    ENERGY_UNITS, ImportedEnergyInputs, _audit, _decode, _energy_prices, _envelope,
    _list, _object, _record, _require, _text, json_safe,
)

SCENARIO = "no_mid_long_term_contract_research"


@dataclass(frozen=True)
class SpotVolumeInput:
    key: SettlementKey
    day_ahead_mwh: EvidenceValue
    actual_mwh: EvidenceValue


@dataclass(frozen=True)
class ImportedSpotOnlyInputs:
    energy: ImportedEnergyInputs
    scenario: str
    scenario_source: str
    base_and_agency_treatment: str


def load_spot_only_inputs(document, *, expected_run_id, expected_selected_dispatch, asof):
    metadata, payload = _envelope(document, input_type="spot_only", expected_run_id=expected_run_id,
        expected_selected_dispatch=expected_selected_dispatch, asof=asof, units=ENERGY_UNITS)
    _object(payload, ("scenario", "scenario_source", "base_and_agency_treatment", "scope", "volumes",
                      "prices", "settlement_interval_source"), "spot_only payload")
    _require(payload["scenario"] == SCENARIO, "explicit no-contract research scenario required")
    _text(payload["scenario_source"], "scenario_source")
    _require(payload["base_and_agency_treatment"] == "explicit_research_exclusion",
             "base/agency exclusion must be explicit for the simplified spot research formula")
    _text(payload["settlement_interval_source"], "settlement_interval_source")
    scope = _decode(EnergyCashScope, payload["scope"], "scope")
    _scope(scope)
    volumes = []
    for wrapper in _list(payload["volumes"], "volumes"):
        q, published, available = _record(SpotVolumeInput, wrapper, "spot volume", metadata,
            completed_at=lambda q: q.key.end if q.actual_mwh.kind != "missing" else None)
        categories = ("mid_long_term_net_contract",) if q.key.resource_kind in {"battery", "pumped"} else (
            "mid_long_term_net_contract", "base", "agency_grid_export")
        # A scenario assumption, never a zero invented for missing real data.
        source = payload["scenario_source"] + "; explicit no-contract and base/agency-excluded research scenario"
        deductions = tuple(DayAheadDeduction(category, EvidenceValue(0., "research", source)) for category in categories)
        volumes.append(EnergyVolumeInput(q.key, q.day_ahead_mwh, q.actual_mwh, deductions))
    prices = _energy_prices(payload["prices"], metadata)
    energy = ImportedEnergyInputs(metadata, scope, tuple(volumes), prices, payload["settlement_interval_source"])
    calculate_energy_cash(scope=scope, volumes=energy.volumes, prices=prices)
    return ImportedSpotOnlyInputs(energy, SCENARIO, payload["scenario_source"], payload["base_and_agency_treatment"])


def build_spot_only_cash(document, *, expected_run_id, expected_selected_dispatch, asof):
    inputs = load_spot_only_inputs(document, expected_run_id=expected_run_id,
        expected_selected_dispatch=expected_selected_dispatch, asof=asof)
    energy = inputs.energy
    report = calculate_energy_cash(scope=energy.scope, volumes=energy.volumes, prices=energy.prices)
    report["metadata"] = json_safe(energy.metadata)
    report["audit"].update(_audit(energy.metadata), scenario=inputs.scenario,
        scenario_source=inputs.scenario_source, base_and_agency_treatment=inputs.base_and_agency_treatment,
        settlement_interval_source=energy.settlement_interval_source,
        ledger_scope="spot_only_research", formula="Q_DA * P_DA + (Q_actual - Q_DA) * P_RT",
        missing_contract_assumed_zero=False, zero_deductions_are_explicit_research_assumptions=True,
        actual_missing_values_replaced_with_zero=False, full_official_settlement_verified=False)
    return report
