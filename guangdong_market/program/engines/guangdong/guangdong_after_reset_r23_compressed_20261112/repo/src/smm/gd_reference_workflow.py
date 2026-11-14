"""Callable, deliberately scoped Guangdong-reference DA and single-window RT.

Research orchestration, not a complete official market. No legacy path, regional
auction, conventional hydro, AGC auction, or settlement ledger is invoked.
See rebuild docs/14_省内参考流程接线.md for the evidence and pricing limitations.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite
from numbers import Real
from typing import Callable
from zoneinfo import ZoneInfo

import pyomo.environ as pyo
from pyomo.core.base.componentuid import ComponentUID

from .gd_dispatch_reserves import attach_reserves
from .gd_energy_core import build_energy_core, assert_linear_energy_model, terminal_thermal_state
from .gd_horizon import EnergyHorizon
from .gd_lmp import PricingSpec, build_pricing_lp, solve_pricing_lp
from .gd_piecewise_bids import ResourceEnergyBid, attach_piecewise_energy_bids
from .gd_pumped_plan_boundary import PumpedPlanBoundaryInput
from .gd_service_boundaries import PhysicalEnvelope, RegulationCapacityResult, apply_regulation_boundaries
from .gd_solve import solve_dispatch, polish_fixed_state
from .gd_storage_core import attach_storage, terminal_storage_state


class WorkflowInputError(ValueError):
    """Missing or unsupported research workflow contract."""


class WorkflowSolveError(RuntimeError):
    """A stopped chain, retaining successful earlier stages for inspection."""
    def __init__(self, message, *, stages, events):
        super().__init__(message)
        self.stages = dict(stages)
        self.events = deepcopy(events)


@dataclass(frozen=True)
class SolveSettings:
    time_limit_s: float
    polish: bool
    threads: int = 1
    mip_rel_gap: float = .001
    tee: bool = False
    require_gap_target: bool = True
    objective_offset_yuan: float = 0.
    offset_source: str | None = None


@dataclass(frozen=True)
class ReserveSpec:
    # DA: up/down/primary/deduction; RT: up/primary/deduction. D-T is wired,
    # never accepted from a possibly netted caller array.
    requirements: dict
    capacity_basis: str
    capacity_mapping_source: str
    source_notes: dict
    ramp_10min_mw: dict | None = None
    penalties_yuan_per_mw_h: dict | None = None


@dataclass(frozen=True)
class ServiceInputs:
    intervals: dict[int, tuple[datetime, datetime]]
    information_cutoff: datetime
    results: tuple[RegulationCapacityResult, ...]
    # result_id -> {kind: external_verified|synthetic, source: ...}
    result_provenance: dict
    physical_boundary_source: str
    forecast_source: str
    allow_nonbinding_lookahead: bool = False


@dataclass(frozen=True)
class RealtimeStateSource:
    commitment_source: str
    actual_initial_state_source: str
    actual_observed_at: datetime
    # Actual observations and their projection to the model's initial boundary
    # must be explicit, not future actual data silently installed at T-15.
    initial_boundary_mapping_source: str
    intraday_state_market_enabled: bool


@dataclass(frozen=True)
class PricingRequest:
    spec_factory: Callable
    source: str
    sensitivity_policy: str


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise WorkflowInputError(f'{label}: explicit source required')


def _aware(value, label):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise WorkflowInputError(f'{label}: timezone-aware datetime required')
    return value


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(float(value)):
        raise WorkflowInputError(f'{label}: finite number required')
    return float(value)


def _settings(settings, pricing):
    if not isinstance(settings, SolveSettings):
        raise WorkflowInputError('SolveSettings required')
    if _number(settings.time_limit_s, 'time_limit_s') <= 0:
        raise WorkflowInputError('positive time limit required')
    if type(settings.threads) is not int or settings.threads != 1:
        raise WorkflowInputError('reference workflow uses exactly one HiGHS thread')
    if _number(settings.mip_rel_gap, 'mip_rel_gap') != .001:
        raise WorkflowInputError('reference workflow requires requested 0.1% gap (.001)')
    for key in ('polish', 'tee', 'require_gap_target'):
        if type(getattr(settings, key)) is not bool:
            raise WorkflowInputError(f'{key}: explicit Boolean required')
    if _number(settings.objective_offset_yuan, 'objective_offset_yuan'):
        _text(settings.offset_source, 'offset_source')
    if pricing is not None:
        if not isinstance(pricing, PricingRequest) or not callable(pricing.spec_factory):
            raise WorkflowInputError('PricingRequest with a callable spec_factory required')
        _text(pricing.source, 'pricing source')
        if pricing.sensitivity_policy != 'reviewed_stage_constraints_hold_load_caps_constant':
            raise WorkflowInputError('only explicit reviewed_stage_constraints_hold_load_caps_constant sensitivity is supported')
        if not settings.polish:
            raise WorkflowInputError('independent pricing requires explicit same-state continuous polish')


def _validate_common(core_inputs, service, settings, pricing, expected_stage):
    _settings(settings, pricing)
    if not isinstance(core_inputs, dict) or {'mode', 'commitment'} & set(core_inputs):
        raise WorkflowInputError('core_inputs must not override workflow mode/commitment')
    h = core_inputs.get('horizon')
    if not isinstance(h, EnergyHorizon):
        raise WorkflowInputError('explicit EnergyHorizon required')
    h.validate()
    if tuple(sorted(h.ids)) != h.ids:
        raise WorkflowInputError('reserve adapter requires increasing period IDs in chronological order')
    if expected_stage == 'RT_SCED' and h.mode != 'real_time':
        raise WorkflowInputError('RT requires real_time horizon with only first interval executed')
    if expected_stage == 'DA_SCED' and h.mode == 'real_time':
        raise WorkflowInputError('DA cannot reuse an RT execution horizon')
    if expected_stage == 'DA_SCED' and not h.settlement_ids:
        raise WorkflowInputError('DA requires at least one explicitly settling delivery interval')
    if not isinstance(service, ServiceInputs):
        raise WorkflowInputError('ServiceInputs required')
    if not isinstance(service.intervals, dict) or set(service.intervals) != set(h.ids):
        raise WorkflowInputError('absolute intervals must exactly cover horizon')
    cutoff = _aware(service.information_cutoff, 'information_cutoff')
    first = None
    for t in h.ids:
        pair = service.intervals[t]
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise WorkflowInputError('interval must be immutable start/end pair')
        start, end = (_aware(value, 'interval') for value in pair)
        first = start if first is None else first
        if start != first + timedelta(hours=h.start_h[t]) or end != first + timedelta(hours=h.end_h[t]):
            raise WorkflowInputError('absolute intervals must match the explicit duration/chronology chain')
    if h.mode.startswith('day_ahead_'):
        local = first.astimezone(ZoneInfo('Asia/Shanghai'))
        if local.hour or local.minute or local.second or local.microsecond:
            raise WorkflowInputError('full DA delivery day must start at Shanghai midnight')
    if cutoff > first:
        raise WorkflowInputError('information cutoff cannot exceed first execution boundary')
    if expected_stage == 'RT_SCED' and cutoff != first - timedelta(minutes=15):
        raise WorkflowInputError('RT information cutoff must equal first execution T-15')
    _text(service.physical_boundary_source, 'physical_boundary_source')
    _text(service.forecast_source, 'forecast_source')
    if type(service.allow_nonbinding_lookahead) is not bool:
        raise WorkflowInputError('allow_nonbinding_lookahead must be Boolean')
    if expected_stage == 'DA_SCED' and service.allow_nonbinding_lookahead:
        raise WorkflowInputError('DA does not inherit formal RT lookahead')
    if not isinstance(service.results, tuple) or not service.results or not all(isinstance(r, RegulationCapacityResult) for r in service.results):
        raise WorkflowInputError('explicit typed AGC results tuple required, including zero awards')
    ids = [r.result_id for r in service.results]
    if len(ids) != len(set(ids)) or not isinstance(service.result_provenance, dict) or set(service.result_provenance) != set(ids):
        raise WorkflowInputError('complete unique AGC result provenance required')
    for rid, entry in service.result_provenance.items():
        if not isinstance(entry, dict) or set(entry) != {'kind', 'source'} or entry['kind'] not in {'external_verified', 'synthetic'}:
            raise WorkflowInputError(f'{rid}: explicitly declared external_verified or synthetic provenance required')
        _text(entry['source'], rid + '.source')
    return deepcopy(core_inputs), deepcopy(service)


def _storage_contract(inputs):
    if inputs is None:
        return None
    required = {'batteries', 'pumped_plants', 'clock_start_h', 'anchor_periods', 'day_end_period', 'cycle_periods', 'source_notes', 'rt_start_stop_budget_policy'}
    optional = {'scuc_aggregation_policy', 'rt_pumped_boundary_policy', 'pumped_plan_boundary'}
    if not isinstance(inputs, dict) or not required <= set(inputs) or set(inputs) - required - optional:
        raise WorkflowInputError('storage_inputs require exact attach_storage input keys, excluding model/core/horizon/stage; only explicit SCUC aggregation and RT pumped-plan fields are optional')
    return deepcopy(inputs)


def _piecewise_contract(inputs):
    if inputs is None:
        return None
    allowed = {'renewable_bids', 'battery_bids', 'pumped_plant_bids'}
    if not isinstance(inputs, dict) or not inputs or set(inputs) - allowed:
        raise WorkflowInputError('piecewise_bids requires explicitly selected energy-bid family keys')
    for family, bids in inputs.items():
        if not isinstance(bids, tuple) or not bids or not all(isinstance(b, ResourceEnergyBid) for b in bids):
            raise WorkflowInputError(f'{family}: nonempty typed ResourceEnergyBid tuple required')
    return deepcopy(inputs)


def _validate_storage_clock(storage, service, horizon):
    if storage is not None:
        local = service.intervals[horizon.ids[0]][0].astimezone(ZoneInfo('Asia/Shanghai'))
        actual = local.hour + local.minute/60 + local.second/3600 + local.microsecond/3.6e9
        supplied = _number(storage['clock_start_h'], 'storage clock_start_h')
        if abs(supplied % 24 - actual) > 1e-9:
            raise WorkflowInputError('storage clock must match Shanghai absolute interval start')
        if storage.get('rt_pumped_boundary_policy') == 'external_initial_plan':
            boundary = storage.get('pumped_plan_boundary')
            if not isinstance(boundary, PumpedPlanBoundaryInput):
                raise WorkflowInputError('external pumped plan requires typed boundary inputs')
            first = service.intervals[horizon.ids[0]][0]
            if boundary.information_cutoff != service.information_cutoff:
                raise WorkflowInputError('pumped plan cutoff must match this RT information cutoff')
            if boundary.model_initial_time != first or boundary.first_calculation_time != first:
                raise WorkflowInputError('pumped plan initial/first times must match this explicit RT interval start')


def _extra_service_resources(storage):
    if storage is None:
        return set()
    ids = list(storage['batteries']) + [u for p in storage['pumped_plants'].values() for u in p['units']]
    if len(ids) != len(set(ids)):
        raise WorkflowInputError('AGC resource IDs must be globally unique')
    return set(ids)


def _thermal_service_results(service, thermal_ids, storage):
    """Require explicit zero for unimplemented battery/pumped AGC mapping."""
    extra = _extra_service_resources(storage)
    if extra & set(thermal_ids):
        raise WorkflowInputError('thermal/storage AGC identifiers must be distinct')
    inventory = set(thermal_ids) | extra
    output = []
    for r in service.results:
        for capacities in [r.capacity_mw, *(s.capacity_mw for s in r.lookahead_slots)]:
            if not isinstance(capacities, dict) or set(capacities) != inventory:
                raise WorkflowInputError('AGC capacity inventory must exactly cover thermal, batteries and pumped units, including zeros')
            for uid in extra:
                if _number(capacities[uid], uid + '.AGC capacity') != 0:
                    raise WorkflowInputError('nonzero storage/pumped AGC qualification and effective-bound mapping unsupported')
        output.append(replace(r, capacity_mw={g:r.capacity_mw[g] for g in thermal_ids},
            lookahead_slots=tuple(replace(s, capacity_mw={g:s.capacity_mw[g] for g in thermal_ids}) for s in r.lookahead_slots)))
    return tuple(output), sorted(extra)


def _validate_reserve(spec, stage, periods):
    if not isinstance(spec, ReserveSpec):
        raise WorkflowInputError('ReserveSpec required for ' + stage)
    if spec.capacity_basis not in {'raw_physical', 'after_agc'}:
        raise WorkflowInputError('reserve capacity_basis must be raw_physical or after_agc')
    if stage == 'DA_SCUC' and spec.capacity_basis != 'raw_physical':
        raise WorkflowInputError('SCUC precedes AGC prearrangement; raw_physical capacity required')
    _text(spec.capacity_mapping_source, 'research reserve capacity_mapping_source')
    expected = {'up_mw', 'primary_mw', 'deduction_mw'} | ({'down_mw'} if stage.startswith('DA') else set())
    if not isinstance(spec.requirements, dict) or set(spec.requirements) != expected:
        raise WorkflowInputError('explicit stage reserve requirements required; D-T is wired from the core')
    for label, series in spec.requirements.items():
        if not isinstance(series, dict) or set(series) != set(periods):
            raise WorkflowInputError(label + ': exact period coverage required')
        for v in series.values():
            if _number(v, label) < 0:
                raise WorkflowInputError(label + ': nonnegative requirement required')
    if stage.startswith('DA') and spec.ramp_10min_mw is not None:
        raise WorkflowInputError('DA cannot receive RT ramp_10min_mw')
    if stage == 'RT_SCED' and spec.ramp_10min_mw is None:
        raise WorkflowInputError('RT requires explicit thermal 10-minute capabilities')
    return deepcopy(spec)


def _attach(model, core_inputs, stage, reserve, storage, bounds=None, piecewise_bids=None):
    if bounds is not None:
        for g in model.G:
            for t in model.T:
                model.online_min[g,t].set_value(bounds.lower_mw[g,t] * model.online[g,t])
                model.online_max[g,t].set_value(bounds.upper_mw[g,t] * model.online[g,t])
    block = None
    if storage is not None:
        block = attach_storage(model, core=model, horizon=core_inputs['horizon'], stage=stage, **deepcopy(storage))
    ports = {}
    for g in model.G:
        data = core_inputs['thermal'][g]
        ports[g] = {'power': {t:model.power[g,t] for t in model.T}}
        for bound, name in [('min', 'online_min'), ('max', 'online_max')]:
            ports[g][name] = {t: (getattr(model,name)[g,t] if reserve.capacity_basis == 'after_agc'
                else data[bound+'_mw'][t]*model.online[g,t]) for t in model.T}
        if stage == 'RT_SCED':
            if not isinstance(reserve.ramp_10min_mw, dict) or set(reserve.ramp_10min_mw) != set(model.G):
                raise WorkflowInputError('RT 10-minute ramp inventory must cover thermal IDs exactly')
            ports[g]['ramp_10min_mw'] = reserve.ramp_10min_mw[g]
    requirements = deepcopy(reserve.requirements)
    if stage.startswith('DA'):
        requirements['system_demand_minus_tie_mw'] = {t:model.raw_system_demand_minus_tie[t] for t in model.T}
    rb = attach_reserves(model, stage=stage, periods=tuple(model.T), thermal=ports,
        pumped={} if block is None else block.reserve_ports,
        requirements=requirements, period_hours=core_inputs['horizon'].duration_h,
        source_notes=reserve.source_notes,
        primary_granularity={'DA_SCUC':'optimization_unit','DA_SCED':'generating_unit','RT_SCED':'plant'}[stage],
        pumped_system_capacity=({} if block is None else block.pumped_system_capacity) if stage.startswith('DA') else None,
        penalties_yuan_per_mw_h=reserve.penalties_yuan_per_mw_h)
    model.other_cost.set_value(model.other_cost.expr + rb.penalty_cost)
    if piecewise_bids is not None:
        attach_piecewise_energy_bids(model, core=model, horizon=core_inputs['horizon'],
            storage=block, **deepcopy(piecewise_bids))
    model._workflow_reserve_audit = dict(capacity_basis=reserve.capacity_basis,
        capacity_mapping_source=reserve.capacity_mapping_source,
        capacity_mapping_is_research_interpretation=True,
        operating_reserve_load_basis='raw_system_demand_minus_tie_no_RE_storage_pumped_subtraction',
        pumped_port_source=None if block is None else deepcopy(block.audit['sources']['reserve_capacity_mapping']))
    assert_linear_energy_model(model, require_fixed_commitment=stage != 'DA_SCUC')
    return model


def _envelope(core_inputs, commitment, service, thermal_results, stage):
    ids = tuple(core_inputs['thermal'])
    if not ids:
        raise WorkflowInputError('first reference workflow requires at least one thermal resource')
    physical = PhysicalEnvelope(
        {(g,t):core_inputs['thermal'][g]['min_mw'][t]*commitment[g,t] for g in ids for t in core_inputs['horizon'].ids},
        {(g,t):core_inputs['thermal'][g]['max_mw'][t]*commitment[g,t] for g in ids for t in core_inputs['horizon'].ids},
        {key:bool(value) for key,value in commitment.items()}, service.physical_boundary_source,
        'raw_availability_adjusted_before_services')
    return apply_regulation_boundaries(physical, energy_stage=stage, intervals=service.intervals,
        information_cutoff=service.information_cutoff, results=thermal_results,
        allow_nonbinding_lookahead=service.allow_nonbinding_lookahead)


def _events(callback):
    if callback is not None and not callable(callback):
        raise WorkflowInputError('stage_callback must be callable')
    events = []
    def emit(stage, state, **details):
        entry = dict(stage=stage, state=state, **details)
        events.append(deepcopy(entry))
        if callback is not None:
            callback(deepcopy(entry))
    return events, emit


def _solve(model, stage, settings, stages, events, emit):
    emit(stage, 'started')
    try:
        solution = solve_dispatch(model, stage=stage, time_limit_s=settings.time_limit_s,
            threads=settings.threads, mip_rel_gap=settings.mip_rel_gap,
            objective_offset_yuan=settings.objective_offset_yuan, offset_source=settings.offset_source, tee=settings.tee)
        stages[stage] = solution
        emit(stage, 'completed', metrics=solution.metrics)
        if settings.require_gap_target and not solution.metrics['requested_gap_met']:
            raise RuntimeError('requested 0.1% gap target not met; downstream stages stopped')
        return solution
    except Exception as exc:
        emit(stage, 'failed', error=str(exc))
        raise WorkflowSolveError(f'{stage}: {exc}', stages=stages, events=events) from exc


def _pricing_source(solution):
    """Clone and freeze explicit load-shortfall caps at baseline; reserve rows stay intact."""
    source = solution.model.clone()
    held = []
    if hasattr(source, 'c_shortfall_cap'):
        for b in source.B:
            for t in source.T:
                value = float(pyo.value(source.demand_mw[b,t]))
                source.c_shortfall_cap[b,t].set_value(source.balance_shortfall[b,t] <= value)
                held.append(dict(boundary='load_shortfall_cap', node=b, period=t, baseline_mw=value,
                                 rows=[str(ComponentUID(source.c_shortfall_cap[b,t]))]))
    return source, held


def _finish(sced, core_inputs, bounds, service, settings, pricing, stages, events, emit, extra_resources, state_audit):
    stage = sced.metrics['stage']
    selected = sced
    if settings.polish:
        name = stage + '_fixed_state_continuous_polish'
        emit(name, 'started')
        try:
            selected = polish_fixed_state(sced, time_limit_s=settings.time_limit_s, threads=settings.threads)
            stages[name] = selected
            emit(name, 'completed', metrics=selected.metrics)
        except Exception as exc:
            emit(name, 'failed', error=str(exc))
            raise WorkflowSolveError(f'{name}: {exc}', stages=stages, events=events) from exc
    pricing_lp = pricing_result = None
    pricing_audit = dict(requested=pricing is not None, final_official_lmp_status='not_implemented',
        scarcity_increment_applied=False, emergency_multiplier_separately_added=False,
        official_RT_formula_contains_emergency_multiplier=stage == 'RT_SCED',
        official_RT_emergency_multiplier_basis_mapping_status='unresolved' if stage == 'RT_SCED' else 'not_applicable')
    if pricing is not None:
        name = 'DA_CONDITIONAL_LMP' if stage == 'DA_SCED' else 'RT_CONDITIONAL_LMP'
        emit(name, 'started')
        try:
            source, held = _pricing_source(selected)
            context = dict(stage='DA' if stage == 'DA_SCED' else 'RT',
                horizon=core_inputs['horizon'], adjusted_thermal_bounds=deepcopy(bounds),
                held_boundaries=deepcopy(held), original_dispatch_metrics=deepcopy(sced.metrics),
                source_scope='Guangdong-reference research subset; not official full LMP',
                sensitivity_policy=pricing.sensitivity_policy)
            spec = pricing.spec_factory(source, deepcopy(context))
            if not isinstance(spec, PricingSpec) or spec.stage != context['stage']:
                raise WorkflowInputError('pricing factory must return a stage-matching complete PricingSpec')
            expected_omissions = ({str(ComponentUID(c)) for component in (source.gd_reserve.c_up, source.gd_reserve.c_down)
                for c in component.values()} if stage == 'DA_SCED' else set())
            if {r.constraint_ref for r in spec.omitted_dispatch_rows} != expected_omissions:
                raise WorkflowInputError('DA pricing must explicitly omit complete reviewed operating up/down reserve rows; RT omits none')
            pricing_lp = build_pricing_lp(source, spec, sced_result=selected.native_result)
            pricing_result = solve_pricing_lp(pricing_lp, time_limit_s=settings.time_limit_s,
                threads=settings.threads, mip_rel_gap=settings.mip_rel_gap, tee=settings.tee)
            pricing_audit.update(source=pricing.source, held_boundaries=held,
                sensitivity_policy=pricing.sensitivity_policy,
                interpretation='conditional_nodal_marginal_given_selected_integer_state_and_reviewed_pricing_rows',
                omitted_dispatch_rows=[vars(r).copy() for r in spec.omitted_dispatch_rows],
                reserve_D_minus_T_frozen_for_pricing=False,
                not_total_physical_load_derivative=True,
                sced_source_unmodified=True, raw_price_only=True)
            # Preserve the workflow sensitivity limitation with the independently
            # obtained result, not only in a remote orchestration object.
            pricing_result.audit['workflow_sensitivity'] = deepcopy(pricing_audit)
            emit(name, 'completed', metrics=dict(backend=pricing_result.backend,
                termination=pricing_result.termination_condition, objective_yuan=pricing_result.objective_yuan,
                final_price_status=pricing_result.final_price_status))
        except Exception as exc:
            emit(name, 'failed', error=str(exc))
            raise WorkflowSolveError(f'{name}: {exc}', stages=stages, events=events) from exc
    h = core_inputs['horizon']
    through = h.ids[0] if stage == 'RT_SCED' else h.settlement_ids[-1]
    model = selected.model
    state = dict(through_period=through, thermal=terminal_thermal_state(model, through_period=through),
        storage=None if not hasattr(model,'gd_storage') else terminal_storage_state(model.gd_storage, through_period=through),
        observation_status='solved_plan_boundary_not_a_future_actual_observation')
    audit = dict(scope='Guangdong-provincial-reference-research-subset',
        full_official_market_verified=False, ieee39_acceptance=False, horizon=h.audit(),
        solver='highs', threads=1, requested_mip_rel_gap=.001,
        cross_regional_market=False, physical_tie_role='explicit_exogenous_schedule', conventional_hydro=False,
        agc_auction=False, reserve_auction=False, formal_new_cash_ledger=False,
        fixed_spinning_compensation='separate_ex_post_module_not_in_dispatch_objective',
        agc_result_provenance=deepcopy(service.result_provenance),
        agc_authenticity_verified_by_workflow=False, information_cutoff=service.information_cutoff.isoformat(),
        forecast_source=service.forecast_source, service_boundary_audit=deepcopy(bounds.audit),
        nonthermal_agc_zero_required_resources=extra_resources,
        nonthermal_agc_mapping='unsupported_nonzero_rejected',
        reserve_by_stage={key:deepcopy(value.model._workflow_reserve_audit) for key,value in stages.items()},
        numeric_state_boundary_by_stage={key:deepcopy(value.model._energy_scope.get('numeric_state_boundary'))
            for key,value in stages.items()},
        storage_by_stage={key:deepcopy(value.model.gd_storage.audit) for key,value in stages.items() if hasattr(value.model,'gd_storage')},
        piecewise_bids_by_stage={key:deepcopy(value.model.gd_piecewise_bids.audit) for key,value in stages.items() if hasattr(value.model,'gd_piecewise_bids')},
        state_source=deepcopy(state_audit), pricing=pricing_audit,
        sced_fixed_thermal_cost_constants_retained=True,
        sced_storage_and_pumped_integers_not_inherited_from_scuc=True,
        single_window_only=stage == 'RT_SCED',
        rt_pumped_start_preparation=(deepcopy(model.gd_storage.audit['pumped_plan_boundary'])
            if hasattr(model,'gd_storage') else {'policy':'not_applicable'}),
        original_mip_metrics_preserved=True)
    return dict(stages=stages, dispatch=sced, selected_dispatch=selected,
        commitment={(g,t):int(round(pyo.value(model.online[g,t]))) for g in model.G for t in model.T},
        adjusted_thermal_bounds=bounds, executed_boundary=state,
        pricing_lp=pricing_lp, pricing_result=pricing_result, audit=audit, events=deepcopy(events))


def run_day_ahead_reference(*, core_inputs, reserve_by_stage, services, settings,
                            storage_by_stage=None, pricing=None, stage_callback=None,
                            piecewise_bids=None):
    """SCUC -> external AGC prearrangement -> fixed-thermal SCED -> research LMP.

    Separate explicit storage inputs are required for both stages when supplied.
    No storage integer decisions are copied from SCUC into SCED.
    """
    core, service = _validate_common(core_inputs, services, settings, pricing, 'DA_SCED')
    bids = _piecewise_contract(piecewise_bids)
    if not core.get('thermal'):
        raise WorkflowInputError('first workflow requires nonempty thermal inventory')
    if not isinstance(reserve_by_stage, dict) or set(reserve_by_stage) != {'DA_SCUC','DA_SCED'}:
        raise WorkflowInputError('DA reserve_by_stage requires DA_SCUC and DA_SCED')
    reserve = {s:_validate_reserve(reserve_by_stage[s],s,core['horizon'].ids) for s in reserve_by_stage}
    if storage_by_stage is not None and (not isinstance(storage_by_stage,dict) or set(storage_by_stage) != {'DA_SCUC','DA_SCED'}):
        raise WorkflowInputError('storage_by_stage requires separate DA_SCUC and DA_SCED inputs')
    storage = {s:_storage_contract(None if storage_by_stage is None else storage_by_stage[s]) for s in reserve}
    if _extra_service_resources(storage['DA_SCUC']) != _extra_service_resources(storage['DA_SCED']):
        raise WorkflowInputError('DA storage resource inventory must be conserved across stages')
    thermal_results, extra = _thermal_service_results(service, core['thermal'], storage['DA_SCED'])
    for item in storage.values():
        _validate_storage_clock(item, service, core['horizon'])
    events, emit = _events(stage_callback); stages = {}
    # Validate every algebraic model/input that can be checked before dispatch.
    uc_model = _attach(build_energy_core(**core),core,'DA_SCUC',reserve['DA_SCUC'],storage['DA_SCUC'],piecewise_bids=bids)
    uc_model._workflow_stage = 'DA_SCUC'
    # Check external timing, result coverage and raw physical capacity before
    # using solver time. Actual SCUC offline states are checked again afterward.
    _envelope(core, {(g,t):1 for g in core['thermal'] for t in core['horizon'].ids},
        service, thermal_results, 'DA_SCED')
    uc = _solve(uc_model,'DA_SCUC',settings,stages,events,emit)
    commitment = {(g,t):int(round(pyo.value(uc.model.online[g,t]))) for g in uc.model.G for t in uc.model.T}
    emit('AGC_PREARRANGEMENT_BOUNDARY','started')
    try:
        bounds = _envelope(core,commitment,service,thermal_results,'DA_SCED')
    except Exception as exc:
        emit('AGC_PREARRANGEMENT_BOUNDARY','failed',error=str(exc))
        raise WorkflowSolveError(f'AGC_PREARRANGEMENT_BOUNDARY: {exc}',stages=stages,events=events) from exc
    emit('AGC_PREARRANGEMENT_BOUNDARY','completed', source='external_supplied_results_not_an_auction')
    try:
        sced_model = _attach(build_energy_core(**core,mode='SCED',commitment=commitment),core,
            'DA_SCED',reserve['DA_SCED'],storage['DA_SCED'],bounds,piecewise_bids=bids)
        sced_model._workflow_stage = 'DA_SCED'
    except Exception as exc:
        emit('DA_SCED_BUILD','failed',error=str(exc))
        raise WorkflowSolveError(f'DA_SCED_BUILD: {exc}',stages=stages,events=events) from exc
    sced = _solve(sced_model,'DA_SCED',settings,stages,events,emit)
    return _finish(sced,core,bounds,service,settings,pricing,stages,events,emit,extra,
        dict(thermal_commitment='this_workflow_DA_SCUC', initial_state_source=core['source_notes']['thermal']))


def run_real_time_reference(*, core_inputs, reserve, services, settings, commitment,
                            state_source, storage_inputs=None, pricing=None, stage_callback=None,
                            piecewise_bids=None):
    """One RT window, explicit approved commitment and observed initial-state source.

    The only executed/settling interval is first; later boundary values remain
    forecasts/plans. This function neither schedules another run nor reads future
    actual data or starts an intraday market.
    """
    core, service = _validate_common(core_inputs,services,settings,pricing,'RT_SCED')
    bids = _piecewise_contract(piecewise_bids)
    if not core.get('thermal'):
        raise WorkflowInputError('first workflow requires nonempty thermal inventory')
    reserve = _validate_reserve(reserve,'RT_SCED',core['horizon'].ids)
    storage = _storage_contract(storage_inputs)
    _validate_storage_clock(storage,service,core['horizon'])
    if not isinstance(state_source,RealtimeStateSource):
        raise WorkflowInputError('RealtimeStateSource required')
    for field in ('commitment_source','actual_initial_state_source','initial_boundary_mapping_source'):
        _text(getattr(state_source,field),field)
    observed = _aware(state_source.actual_observed_at,'actual_observed_at')
    if observed > service.information_cutoff:
        raise WorkflowInputError('future actual initial states unavailable at RT cutoff are forbidden')
    if type(state_source.intraday_state_market_enabled) is not bool:
        raise WorkflowInputError('intraday_state_market_enabled must be explicit Boolean')
    if storage is not None and storage.get('rt_pumped_boundary_policy') == 'external_initial_plan':
        if storage['pumped_plan_boundary'].intraday_state_market_enabled != state_source.intraday_state_market_enabled:
            raise WorkflowInputError('pumped plan and RT state must agree on intraday state market activation')
    if state_source.intraday_state_market_enabled and storage is not None and _extra_service_resources(storage):
        raise WorkflowInputError('enabled intraday storage/pumped state inheritance is not mapped; refusing free RT integers')
    commitment = deepcopy(commitment)
    # Build validates exact binary/status contract before deriving online envelopes.
    model = build_energy_core(**core,mode='SCED',commitment=commitment)
    thermal_results, extra = _thermal_service_results(service,core['thermal'],storage)
    bounds = _envelope(core,commitment,service,thermal_results,'RT_SCED')
    model = _attach(model,core,'RT_SCED',reserve,storage,bounds,piecewise_bids=bids)
    model._workflow_stage = 'RT_SCED'
    events, emit = _events(stage_callback); stages = {}
    emit('FORMAL_AGC_BOUNDARY','completed', source='external_supplied_formal_result_with_explicit_lookahead')
    sced = _solve(model,'RT_SCED',settings,stages,events,emit)
    return _finish(sced,core,bounds,service,settings,pricing,stages,events,emit,extra,
        dict(commitment_source=state_source.commitment_source,
             actual_initial_state_source=state_source.actual_initial_state_source,
             actual_observed_at=observed.isoformat(),
             initial_boundary_mapping_source=state_source.initial_boundary_mapping_source,
             intraday_state_market_enabled=state_source.intraday_state_market_enabled))


# Traceability guard for native acceptance. Pure workflow tests may still inject
# solutions, but callers must not label those results native numerical evidence.
_ORIGINAL_WORKFLOW_SOLVE_DISPATCH = solve_dispatch
_ORIGINAL_WORKFLOW_POLISH = polish_fixed_state
_ORIGINAL_WORKFLOW_PRICING = solve_pricing_lp


def native_workflow_path_intact():
    from . import gd_solve, gd_lmp
    from .highs_solver import native_solver_path_intact
    return (native_solver_path_intact()
        and solve_dispatch is _ORIGINAL_WORKFLOW_SOLVE_DISPATCH is gd_solve.solve_dispatch
        and polish_fixed_state is _ORIGINAL_WORKFLOW_POLISH is gd_solve.polish_fixed_state
        and gd_lmp.HighsSolver is gd_lmp._ORIGINAL_PRICING_SOLVER_CLASS is gd_solve.HighsSolver
        and solve_pricing_lp is _ORIGINAL_WORKFLOW_PRICING is gd_lmp.solve_pricing_lp is gd_lmp._ORIGINAL_SOLVE_PRICING_LP)
